"""Restricted Docker execution for untrusted candidate repository code.

The Docker daemon remains part of the trusted computing base.  These controls
reduce accidental and malicious impact; they are not a formal security proof.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic_ns
from typing import Protocol

from app.repositories.identifiers import validate_safe_identifier
from app.repositories.workspace import DisposableWorkspace

_IMAGE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHELL_EXECUTABLES = {
    "bash",
    "cmd",
    "cmd.exe",
    "dash",
    "fish",
    "ksh",
    "powershell",
    "powershell.exe",
    "pwsh",
    "sh",
    "zsh",
}
_ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_OPTIONAL_ENVIRONMENT_KEYS = {
    "AGENTTRACE_COUNTEREXAMPLE_FILE",
    "HYPOTHESIS_PROFILE",
    "PYTHONPATH",
}
_FIXED_ENVIRONMENT = {
    "HOME": "/home/agenttrace",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONPYCACHEPREFIX": "/output/pycache",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
}


class DockerEnvironmentError(RuntimeError):
    """Raised when a configured image cannot be resolved through Docker."""


@dataclass(frozen=True, slots=True)
class DockerLimits:
    """Resource bounds applied to every verification container."""

    cpus: float = 1.0
    memory_mb: int = 512
    pids: int = 128
    tmpfs_mb: int = 64
    user: str = "65532:65532"

    def __post_init__(self) -> None:
        if not 0.1 <= self.cpus <= 8:
            raise ValueError("Docker CPU limit must be between 0.1 and 8")
        if not 64 <= self.memory_mb <= 8192:
            raise ValueError("Docker memory limit must be between 64 and 8192 MiB")
        if not 16 <= self.pids <= 4096:
            raise ValueError("Docker process limit must be between 16 and 4096")
        if not 8 <= self.tmpfs_mb <= 1024:
            raise ValueError("Docker tmpfs limit must be between 8 and 1024 MiB")
        if not re.fullmatch(r"[1-9][0-9]{0,9}:[1-9][0-9]{0,9}", self.user):
            raise ValueError("Docker user must be an explicit non-root numeric uid:gid")


@dataclass(frozen=True, slots=True)
class DockerImageIdentity:
    """Configured image reference bound to Docker's immutable image ID."""

    reference: str
    image_id: str


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """Bounded result returned by the injectable host-process boundary."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False
    launch_error: str | None = None


class ProcessExecutor(Protocol):
    """Seam used to test Docker argv without starting repository code."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessOutcome: ...


@dataclass(frozen=True, slots=True)
class DockerExecution:
    """One container execution with bounded observable output."""

    container_name: str
    image: DockerImageIdentity
    command: tuple[str, ...]
    exit_code: int | None
    duration_ms: int
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False
    infrastructure_error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.infrastructure_error is None


class DockerRunner:
    """Execute commands in read-only disposable workspaces under Docker."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        docker_executable: str = "docker",
        limits: DockerLimits | None = None,
        max_output_chars: int = 200_000,
        executor: ProcessExecutor | None = None,
    ) -> None:
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir() or _is_link_like(Path(workspace_root)):
            raise ValueError("workspace_root must be a non-linked directory")
        if not docker_executable or "\x00" in docker_executable:
            raise ValueError("docker_executable must be non-empty text")
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")
        self.workspace_root = root
        self.docker_executable = docker_executable
        self.limits = limits or DockerLimits()
        self.max_output_chars = max_output_chars
        self._executor = executor or _BoundedSubprocessExecutor()

    def inspect_image(self, reference: str, *, timeout_seconds: float = 30) -> DockerImageIdentity:
        """Resolve a configured image to an immutable local Docker image ID."""

        _validate_image_reference(reference)
        outcome = self._executor.run(
            (
                self.docker_executable,
                "image",
                "inspect",
                "--format={{.Id}}",
                reference,
            ),
            timeout_seconds=timeout_seconds,
            max_output_chars=1_000,
        )
        image_id = outcome.stdout.strip()
        if (
            outcome.launch_error is not None
            or outcome.timed_out
            or outcome.exit_code != 0
            or _IMAGE_ID.fullmatch(image_id) is None
        ):
            detail = outcome.launch_error or "configured Docker image is unavailable"
            raise DockerEnvironmentError(detail)
        return DockerImageIdentity(reference=reference, image_id=image_id)

    def run(
        self,
        *,
        workspace: DisposableWorkspace,
        image: DockerImageIdentity,
        command: Sequence[str],
        timeout_seconds: float,
        evaluator_root: str | Path | None = None,
        output_root: str | Path | None = None,
        environment: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> DockerExecution:
        """Run a fixed argv in Docker; no shell or host repository process is used."""

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        workspace_path = self._validate_workspace(workspace)
        _validate_image_reference(image.reference)
        if _IMAGE_ID.fullmatch(image.image_id) is None:
            raise ValueError("image identity must contain a Docker sha256 image ID")
        container_command = _validate_command(command)
        evaluator = self._validate_auxiliary_root(
            evaluator_root,
            field_name="evaluator_root",
            workspace=workspace_path,
        )
        output = self._validate_auxiliary_root(
            output_root,
            field_name="output_root",
            workspace=workspace_path,
        )
        if evaluator is not None and output is not None and _paths_overlap(evaluator, output):
            raise ValueError("evaluator_root and output_root must not overlap")
        extra_environment = _validate_environment(environment)

        safe_run_id = validate_safe_identifier(workspace.run_id, field_name="run_id")
        container_name = f"agentrace-{hashlib.sha256(safe_run_id.encode()).hexdigest()[:24]}"
        argv = self._docker_argv(
            container_name=container_name,
            image=image,
            command=container_command,
            workspace=workspace_path,
            evaluator=evaluator,
            output=output,
            environment=extra_environment,
        )
        # A prior host interruption may leave the deterministic name occupied.
        # The name is derived only from a validated run ID, never caller text.
        self._remove_container(container_name)
        started_ns = monotonic_ns()
        outcome = self._executor.run(
            argv,
            timeout_seconds=timeout_seconds,
            max_output_chars=self.max_output_chars,
        )
        duration_ms = (monotonic_ns() - started_ns) // 1_000_000
        # `--rm` handles normal exits; this fixed-name cleanup also handles a
        # killed Docker client or host-enforced timeout.
        self._remove_container(container_name)
        infrastructure_error = outcome.launch_error
        if outcome.exit_code is None and not outcome.timed_out and infrastructure_error is None:
            infrastructure_error = "Docker returned no container exit status"
        return DockerExecution(
            container_name=container_name,
            image=image,
            command=container_command,
            exit_code=outcome.exit_code,
            duration_ms=duration_ms,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            timed_out=outcome.timed_out,
            output_truncated=outcome.output_truncated,
            infrastructure_error=infrastructure_error,
        )

    def _validate_workspace(self, workspace: DisposableWorkspace) -> Path:
        path = workspace.path.resolve(strict=True)
        if (
            path.parent != self.workspace_root
            or path.name != workspace.run_id
            or not path.is_dir()
            or _is_link_like(workspace.path)
        ):
            raise ValueError("verification requires a managed disposable workspace")
        git_directory = path / ".git"
        if not git_directory.is_dir() or _is_link_like(git_directory):
            raise ValueError("verification workspace must have an independent .git directory")
        return path

    def _validate_auxiliary_root(
        self,
        value: str | Path | None,
        *,
        field_name: str,
        workspace: Path,
    ) -> Path | None:
        if value is None:
            return None
        lexical = Path(value)
        resolved = lexical.resolve(strict=True)
        if not resolved.is_dir() or _is_link_like(lexical):
            raise ValueError(f"{field_name} must be a non-linked directory")
        if _paths_overlap(resolved, workspace) or _paths_overlap(resolved, self.workspace_root):
            raise ValueError(f"{field_name} must be isolated from workspaces")
        if field_name == "output_root" and any(resolved.iterdir()):
            raise ValueError("output_root must be a dedicated empty directory")
        _validate_mount_source(resolved)
        return resolved

    def _docker_argv(
        self,
        *,
        container_name: str,
        image: DockerImageIdentity,
        command: tuple[str, ...],
        workspace: Path,
        evaluator: Path | None,
        output: Path | None,
        environment: tuple[tuple[str, str], ...],
    ) -> tuple[str, ...]:
        limits = self.limits
        arguments = [
            self.docker_executable,
            "run",
            "--rm",
            "--init",
            "--name",
            container_name,
            "--network",
            "none",
            "--user",
            limits.user,
            "--cpus",
            f"{limits.cpus:g}",
            "--memory",
            f"{limits.memory_mb}m",
            "--memory-swap",
            f"{limits.memory_mb}m",
            "--pids-limit",
            str(limits.pids),
            "--ulimit",
            "nofile=1024:1024",
            "--ulimit",
            "fsize=16777216:16777216",
            "--ipc",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--tmpfs",
            _tmpfs_option("/tmp", limits, size_mb=limits.tmpfs_mb, mode="1770"),
            "--tmpfs",
            _tmpfs_option("/home/agenttrace", limits, size_mb=16, mode="0700"),
            "--workdir",
            "/workspace",
            "--mount",
            _mount_argument(workspace, "/workspace", read_only=True),
        ]
        for key, value in sorted(_FIXED_ENVIRONMENT.items()):
            arguments.extend(("--env", f"{key}={value}"))
        for key, value in environment:
            arguments.extend(("--env", f"{key}={value}"))
        if evaluator is not None:
            arguments.extend(["--mount", _mount_argument(evaluator, "/evaluator", read_only=True)])
        if output is None:
            arguments.extend(["--tmpfs", _tmpfs_option("/output", limits)])
        else:
            arguments.extend(["--mount", _mount_argument(output, "/output", read_only=False)])
        # Pin execution to the inspected immutable ID, and replace any image
        # entrypoint so the evaluator command is invoked directly.
        arguments.extend(["--entrypoint", command[0], image.image_id, *command[1:]])
        return tuple(arguments)

    def _remove_container(self, container_name: str) -> None:
        self._executor.run(
            (self.docker_executable, "rm", "--force", container_name),
            timeout_seconds=15,
            max_output_chars=1_000,
        )


class _BoundedSubprocessExecutor:
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessOutcome:
        started: subprocess.Popen[bytes] | None = None
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                started = subprocess.Popen(
                    list(argv),
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    env=_docker_cli_environment(),
                )
                started.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                if started is not None:
                    started.kill()
                    started.wait()
                stdout, stdout_cut = _read_bounded(stdout_file, max_output_chars)
                stderr, stderr_cut = _read_bounded(stderr_file, max_output_chars)
                return ProcessOutcome(
                    None,
                    stdout,
                    stderr,
                    timed_out=True,
                    output_truncated=stdout_cut or stderr_cut,
                )
            except OSError as error:
                return ProcessOutcome(None, "", "", launch_error=f"Docker unavailable: {error}")
            stdout, stdout_cut = _read_bounded(stdout_file, max_output_chars)
            stderr, stderr_cut = _read_bounded(stderr_file, max_output_chars)
            return ProcessOutcome(
                started.returncode if started is not None else None,
                stdout,
                stderr,
                output_truncated=stdout_cut or stderr_cut,
            )


def _docker_cli_environment() -> Mapping[str, str]:
    allowed = (
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    )
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _read_bounded(stream: object, limit: int) -> tuple[str, bool]:
    stream.seek(0)  # type: ignore[attr-defined]
    data: bytes = stream.read(limit * 4 + 1)  # type: ignore[attr-defined]
    text = data.decode("utf-8", errors="replace")
    truncated = len(data) > limit * 4 or len(text) > limit
    if len(text) > limit:
        text = text[:limit]
    if truncated:
        text += "\n...[output truncated by AgentTrace]"
    return text, truncated


def _validate_image_reference(reference: str) -> None:
    if _IMAGE_REFERENCE.fullmatch(reference) is None or reference.startswith("-"):
        raise ValueError("Docker image reference is invalid")


def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)) or not command:
        raise ValueError("container command must be a non-empty argv sequence")
    values = tuple(command)
    if any(not isinstance(value, str) or "\x00" in value for value in values):
        raise ValueError("container command arguments must be NUL-free strings")
    executable = Path(values[0]).name.casefold()
    if not executable or executable in _SHELL_EXECUTABLES:
        raise ValueError("verification commands cannot invoke a shell")
    return values


def _mount_argument(source: Path, destination: str, *, read_only: bool) -> str:
    _validate_mount_source(source)
    base = f"type=bind,source={source},target={destination}"
    # `--mount` bind mounts are read/write by default; `readonly` is the
    # portable explicit flag.  Unlike `--volume`, `rw` is not accepted by all
    # Docker versions as a `--mount` key.
    return f"{base},readonly" if read_only else base


def _tmpfs_option(
    destination: str,
    limits: DockerLimits,
    *,
    size_mb: int | None = None,
    mode: str = "0770",
) -> str:
    uid, gid = limits.user.split(":", maxsplit=1)
    size = limits.tmpfs_mb if size_mb is None else size_mb
    return f"{destination}:rw,noexec,nosuid,nodev,size={size}m,uid={uid},gid={gid},mode={mode}"


def _validate_environment(
    environment: Mapping[str, str] | Sequence[tuple[str, str]] | None,
) -> tuple[tuple[str, str], ...]:
    if environment is None:
        return ()
    items = tuple(environment.items()) if isinstance(environment, Mapping) else tuple(environment)
    if len(items) > 16:
        raise ValueError("verification environment exceeds the variable-count bound")
    seen: set[str] = set()
    accepted: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("verification environment must contain key/value pairs")
        key, value = item
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or _ENVIRONMENT_KEY.fullmatch(key) is None
            or len(value) > 4_096
            or any(character in value for character in "\x00\r\n")
        ):
            raise ValueError("verification environment contains an invalid key or value")
        if key in seen:
            raise ValueError("verification environment cannot contain duplicate keys")
        seen.add(key)
        if key in _FIXED_ENVIRONMENT:
            if value != _FIXED_ENVIRONMENT[key]:
                raise ValueError(f"verification environment cannot override {key}")
            continue
        if key not in _OPTIONAL_ENVIRONMENT_KEYS:
            raise ValueError(f"verification environment key is not allowed: {key}")
        _validate_optional_environment_value(key, value)
        accepted.append((key, value))
    return tuple(sorted(accepted))


def _validate_optional_environment_value(key: str, value: str) -> None:
    if key == "AGENTTRACE_COUNTEREXAMPLE_FILE":
        path = PurePosixPath(value)
        if (
            not value.startswith("/output/")
            or path.as_posix() != value
            or ".." in path.parts
            or value.endswith("/")
        ):
            raise ValueError("counterexample output must be a file below /output")
    elif key == "HYPOTHESIS_PROFILE" and value != "agentrace":
        raise ValueError("only the bounded AgentTrace Hypothesis profile is allowed")
    elif key == "PYTHONPATH":
        paths = value.split(":")
        if not paths or any(
            not (
                path in {"/workspace", "/evaluator"}
                or path.startswith(("/workspace/", "/evaluator/"))
            )
            or ".." in PurePosixPath(path).parts
            or PurePosixPath(path).as_posix() != path
            for path in paths
        ):
            raise ValueError("PYTHONPATH may contain only workspace or evaluator paths")


def _validate_mount_source(source: Path) -> None:
    if any(character in str(source) for character in {",", "\x00", "\n", "\r"}):
        raise ValueError("Docker mount paths cannot contain separators or control characters")


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
