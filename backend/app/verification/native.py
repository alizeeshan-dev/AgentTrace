"""Restricted native Windows execution for trusted repositories.

This boundary deliberately does not claim to sandbox arbitrary code.  It runs
only trusted benchmark repositories or explicitly trusted external repositories in managed Git
workspaces, using a per-verification virtual environment, a sanitized process
environment, fixed working directories, bounded output, and hard timeouts.
"""

from __future__ import annotations

import os
import re
import site
import subprocess
import tempfile
import venv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic_ns
from typing import Protocol

from app.repositories.workspace import DisposableWorkspace

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
_PYTHON_EXECUTABLES = {"python", "python.exe", "python3", "python3.exe"}
_ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_OPTIONAL_ENVIRONMENT_KEYS = {
    "AGENTTRACE_COUNTEREXAMPLE_FILE",
    "HYPOTHESIS_PROFILE",
    "PYTHONHASHSEED",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "PYTHONPATH",
}
_FIXED_PYTHON_ENVIRONMENT = {
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
}


class NativeEnvironmentError(RuntimeError):
    """Raised when the dedicated verification environment cannot be prepared."""


@dataclass(frozen=True, slots=True)
class WindowsExecutionEnvironment:
    """One temporary Python environment dedicated to a verification attempt."""

    root: Path
    python_executable: Path
    home: Path
    temporary: Path


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """Bounded result returned by the injectable process boundary."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False
    launch_error: str | None = None


class ProcessExecutor(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessOutcome: ...


@dataclass(frozen=True, slots=True)
class WindowsExecution:
    """One controlled native verification process with bounded observations."""

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


class WindowsVerificationRunner:
    """Execute fixed Python argv in a disposable workspace on native Windows."""

    def __init__(
        self,
        workspace_root: str | Path,
        verification_root: str | Path,
        *,
        max_output_chars: int = 200_000,
        executor: ProcessExecutor | None = None,
    ) -> None:
        self.workspace_root = _canonical_non_linked_directory(
            workspace_root, field_name="workspace_root"
        )
        self.verification_root = _canonical_non_linked_directory(
            verification_root, field_name="verification_root"
        )
        if _paths_overlap(self.workspace_root, self.verification_root):
            raise ValueError("workspace_root and verification_root must not overlap")
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")
        self.max_output_chars = max_output_chars
        self._executor = executor or _BoundedWindowsSubprocessExecutor()

    def prepare_environment(self, root: str | Path) -> WindowsExecutionEnvironment:
        """Create a temporary venv backed by the frozen AgentTrace environment.

        ``system_site_packages`` intentionally exposes only the packages in the
        frozen AgentTrace Python environment; pip is not bootstrapped and user
        site packages remain disabled for repository subprocesses.
        """

        destination = Path(root)
        resolved_parent = destination.parent.resolve(strict=True)
        if (
            resolved_parent != self.verification_root
            and self.verification_root not in resolved_parent.parents
        ):
            raise ValueError("verification environment must be below verification_root")
        if destination.exists() and (
            _is_link_like(destination) or any(destination.iterdir())
        ):
            raise ValueError("verification environment root must be absent or empty")
        try:
            venv.EnvBuilder(
                system_site_packages=False,
                clear=False,
                symlinks=False,
                with_pip=False,
            ).create(destination)
            canonical = destination.resolve(strict=True)
            python = canonical / "Scripts" / "python.exe"
            if not python.is_file():
                # This fallback keeps the component importable for non-Windows
                # developer tooling, while the supported experiment host is Windows.
                python = canonical / "bin" / "python"
            if not python.is_file():
                raise OSError("virtual-environment Python executable was not created")
            site_packages = canonical / "Lib" / "site-packages"
            if not site_packages.is_dir():
                site_packages = next(
                    (
                        candidate
                        for candidate in (canonical / "lib").glob("python*/site-packages")
                        if candidate.is_dir()
                    ),
                    site_packages,
                )
            frozen_packages = _frozen_site_packages()
            if frozen_packages:
                site_packages.mkdir(parents=True, exist_ok=True)
                site_packages.joinpath("_agentrace_frozen_packages.pth").write_text(
                    "".join(f"{path}\n" for path in frozen_packages),
                    encoding="utf-8",
                )
            home = canonical / "home"
            temporary = canonical / "temp"
            home.mkdir()
            temporary.mkdir()
            return WindowsExecutionEnvironment(canonical, python.resolve(), home, temporary)
        except (OSError, subprocess.SubprocessError) as error:
            raise NativeEnvironmentError(
                f"native verification environment could not be created: {error}"
            ) from error

    def run(
        self,
        *,
        workspace: DisposableWorkspace,
        execution_environment: WindowsExecutionEnvironment,
        command: Sequence[str],
        timeout_seconds: float,
        evaluator_root: str | Path | None = None,
        output_root: str | Path | None = None,
        environment: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> WindowsExecution:
        """Run an approved argv without a shell or inherited host secrets."""

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        workspace_path = self._validate_workspace(workspace)
        isolated = self._validate_execution_environment(execution_environment)
        evaluator = self._validate_auxiliary_root(
            evaluator_root, field_name="evaluator_root", workspace=workspace_path
        )
        output = self._validate_auxiliary_root(
            output_root, field_name="output_root", workspace=workspace_path
        )
        if output is None:
            output = Path(tempfile.mkdtemp(prefix="output-", dir=isolated.root)).resolve()
        if evaluator is not None and _paths_overlap(evaluator, output):
            raise ValueError("evaluator_root and output_root must not overlap")

        approved_command = _validate_command(command)
        translated_command = _translate_command(
            approved_command,
            python=isolated.python_executable,
            workspace=workspace_path,
            evaluator=evaluator,
            output=output,
        )
        extra_environment = _validate_environment(environment)
        process_environment = _sanitized_environment(
            isolated,
            workspace=workspace_path,
            evaluator=evaluator,
            output=output,
            optional=extra_environment,
        )
        started_ns = monotonic_ns()
        outcome = self._executor.run(
            translated_command,
            cwd=workspace_path,
            environment=process_environment,
            timeout_seconds=timeout_seconds,
            max_output_chars=self.max_output_chars,
        )
        duration_ms = (monotonic_ns() - started_ns) // 1_000_000
        infrastructure_error = outcome.launch_error
        if outcome.exit_code is None and not outcome.timed_out and infrastructure_error is None:
            infrastructure_error = "native verifier returned no process exit status"
        return WindowsExecution(
            command=translated_command,
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

    def _validate_execution_environment(
        self, environment: WindowsExecutionEnvironment
    ) -> WindowsExecutionEnvironment:
        root = environment.root.resolve(strict=True)
        if self.verification_root not in root.parents or _is_link_like(environment.root):
            raise ValueError("execution environment must be below verification_root")
        for path in (environment.python_executable, environment.home, environment.temporary):
            resolved = path.resolve(strict=True)
            if root not in resolved.parents or _is_link_like(path):
                raise ValueError("execution environment contains an invalid path")
        if not environment.python_executable.is_file():
            raise ValueError("execution environment Python executable is unavailable")
        return environment

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
        if self.verification_root not in resolved.parents:
            raise ValueError(f"{field_name} must be below verification_root")
        if field_name == "output_root" and any(resolved.iterdir()):
            raise ValueError("output_root must be a dedicated empty directory")
        return resolved


class _BoundedWindowsSubprocessExecutor:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessOutcome:
        started: subprocess.Popen[bytes] | None = None
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                started = subprocess.Popen(
                    list(argv),
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    env=dict(environment),
                    creationflags=flags,
                )
                started.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                if started is not None:
                    _terminate_process_tree(started, environment)
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
                return ProcessOutcome(
                    None, "", "", launch_error=f"native verifier process unavailable: {error}"
                )
            stdout, stdout_cut = _read_bounded(stdout_file, max_output_chars)
            stderr, stderr_cut = _read_bounded(stderr_file, max_output_chars)
            return ProcessOutcome(
                started.returncode if started is not None else None,
                stdout,
                stderr,
                output_truncated=stdout_cut or stderr_cut,
            )


def _terminate_process_tree(
    process: subprocess.Popen[bytes], environment: Mapping[str, str]
) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        system_root = Path(environment.get("SYSTEMROOT", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                env=dict(environment),
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            process.kill()
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _sanitized_environment(
    execution: WindowsExecutionEnvironment,
    *,
    workspace: Path,
    evaluator: Path | None,
    output: Path,
    optional: tuple[tuple[str, str], ...],
) -> dict[str, str]:
    system_root = os.environ.get("SYSTEMROOT", os.environ.get("WINDIR", r"C:\Windows"))
    scripts = execution.python_executable.parent
    result = {
        "HOME": str(execution.home),
        "USERPROFILE": str(execution.home),
        "PATH": os.pathsep.join((str(scripts), str(Path(system_root) / "System32"))),
        "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
        "SYSTEMROOT": system_root,
        "WINDIR": system_root,
        "TEMP": str(execution.temporary),
        "TMP": str(execution.temporary),
        "VIRTUAL_ENV": str(execution.root),
        **_FIXED_PYTHON_ENVIRONMENT,
    }
    roots = {"/workspace": workspace, "/output": output}
    if evaluator is not None:
        roots["/evaluator"] = evaluator
    for key, value in optional:
        if key in _FIXED_PYTHON_ENVIRONMENT:
            continue
        result[key] = _translate_environment_value(key, value, roots)
    return result


def _translate_command(
    command: tuple[str, ...],
    *,
    python: Path,
    workspace: Path,
    evaluator: Path | None,
    output: Path,
) -> tuple[str, ...]:
    roots = {"/workspace": workspace, "/output": output}
    if evaluator is not None:
        roots["/evaluator"] = evaluator
    translated = [str(python)]
    translated.extend(_translate_virtual_roots(value, roots) for value in command[1:])
    return tuple(translated)


def _translate_environment_value(key: str, value: str, roots: Mapping[str, Path]) -> str:
    if key == "PYTHONPATH":
        return os.pathsep.join(_translate_virtual_roots(item, roots) for item in value.split(":"))
    return _translate_virtual_roots(value, roots)


def _translate_virtual_roots(value: str, roots: Mapping[str, Path]) -> str:
    unavailable = {
        virtual
        for virtual in ("/workspace", "/output", "/evaluator")
        if virtual not in roots and virtual in value
    }
    if unavailable:
        raise ValueError("verification command references an unavailable virtual root")
    translated = value
    for virtual, physical in sorted(roots.items(), key=lambda item: len(item[0]), reverse=True):
        translated = translated.replace(virtual, physical.as_posix())
    return translated


def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)) or not command:
        raise ValueError("verification command must be a non-empty argv sequence")
    values = tuple(command)
    if any(not isinstance(value, str) or "\x00" in value for value in values):
        raise ValueError("verification command arguments must be NUL-free strings")
    executable = Path(values[0]).name.casefold()
    if not executable or executable in _SHELL_EXECUTABLES:
        raise ValueError("verification commands cannot invoke a shell")
    if executable not in _PYTHON_EXECUTABLES:
        raise ValueError("native verification commands must use the dedicated Python environment")
    return values


def _validate_environment(
    environment: Mapping[str, str] | Sequence[tuple[str, str]] | None,
) -> tuple[tuple[str, str], ...]:
    if environment is None:
        return ()
    items = tuple(environment.items()) if isinstance(environment, Mapping) else tuple(environment)
    if len(items) > 16:
        raise ValueError("verification environment exceeds the variable-count bound")
    accepted: list[tuple[str, str]] = []
    seen: set[str] = set()
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
        if key in seen or key not in _OPTIONAL_ENVIRONMENT_KEYS:
            raise ValueError(f"verification environment key is not allowed or duplicated: {key}")
        seen.add(key)
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
            not (path in {"/workspace", "/evaluator"} or path.startswith("/evaluator/"))
            for path in paths
        ):
            raise ValueError("PYTHONPATH may reference only evaluator and workspace roots")
    elif key in _FIXED_PYTHON_ENVIRONMENT and value != _FIXED_PYTHON_ENVIRONMENT[key]:
        raise ValueError(f"verification environment cannot override {key}")


def _canonical_non_linked_directory(path: str | Path, *, field_name: str) -> Path:
    supplied = Path(path)
    resolved = supplied.resolve(strict=True)
    if not resolved.is_dir() or _is_link_like(supplied):
        raise ValueError(f"{field_name} must be a non-linked directory")
    return resolved


def _frozen_site_packages() -> tuple[str, ...]:
    """Return package roots from AgentTrace's own frozen Python environment."""

    user_site = Path(site.getusersitepackages()).resolve(strict=False)
    accepted: list[str] = []
    for value in site.getsitepackages():
        candidate = Path(value).resolve(strict=False)
        if candidate == user_site or not candidate.is_dir():
            continue
        accepted.append(str(candidate))
    return tuple(sorted(set(accepted)))


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


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
