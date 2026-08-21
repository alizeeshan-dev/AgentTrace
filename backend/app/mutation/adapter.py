"""A bounded, non-shelling adapter for mutmut benchmark qualification."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from time import monotonic_ns

from app.mutation.models import MutationEnvironment, MutationExecution
from app.mutation.parser import parse_mutation_result

SUPPORTED_MUTMUT_VERSION = "3.7.0"
RECOMMENDED_MUTMUT_REQUIREMENT = f"mutmut=={SUPPORTED_MUTMUT_VERSION}"
_VERSION = re.compile(r"\b(?P<version>\d+\.\d+\.\d+)\b")


class MutationEnvironmentUnavailable(RuntimeError):
    """Raised when mutation qualification cannot run in this environment."""


class MutationExecutionError(RuntimeError):
    """Raised when a mutmut subprocess or its evidence collection fails."""

    def __init__(self, message: str, *, command: Sequence[str], stdout: str, stderr: str) -> None:
        super().__init__(message)
        self.command = tuple(command)
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class MutmutConfig:
    """Qualification-only mutmut configuration.

    The paths and pytest arguments are passed as structured configuration, not
    as a command string. mutmut invokes pytest itself in its fork-capable Linux
    process model.
    """

    source_paths: tuple[str, ...]
    test_selection: tuple[str, ...]
    pytest_args: tuple[str, ...] = ()
    also_copy: tuple[str, ...] = ()
    only_mutate: tuple[str, ...] = ()
    do_not_mutate: tuple[str, ...] = ()
    max_children: int = 1
    timeout_seconds: int = 600
    timeout_multiplier: float = 15.0
    timeout_constant: float = 1.0

    def __post_init__(self) -> None:
        if not self.source_paths:
            raise ValueError("At least one mutation source path is required")
        if not self.test_selection:
            raise ValueError("At least one pytest test-selection argument is required")
        for value in (*self.source_paths, *self.also_copy):
            _validate_relative_path(value)
        for value in (*self.only_mutate, *self.do_not_mutate):
            _validate_text_argument(value, field_name="mutation pattern")
            if not (value.endswith(".py") or value.endswith("*")):
                raise ValueError("Mutation patterns must end in .py or *")
        for value in (*self.test_selection, *self.pytest_args):
            _validate_text_argument(value, field_name="pytest argument")
        if self.max_children < 1:
            raise ValueError("max_children must be positive")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if self.timeout_multiplier <= 0 or self.timeout_constant < 0:
            raise ValueError("mutmut timeout values must be non-negative and bounded")

    def render_setup_cfg(self) -> str:
        """Render the exact config format consumed by mutmut 3.7."""

        fields: list[tuple[str, tuple[str, ...] | bool | float]] = [
            ("source_paths", self.source_paths),
            ("pytest_add_cli_args_test_selection", self.test_selection),
            ("pytest_add_cli_args", self.pytest_args),
            ("also_copy", self.also_copy),
            ("only_mutate", self.only_mutate),
            ("do_not_mutate", self.do_not_mutate),
            ("mutate_only_covered_lines", False),
            ("timeout_multiplier", self.timeout_multiplier),
            ("timeout_constant", self.timeout_constant),
            ("use_git_change_detection", False),
        ]
        lines = ["[mutmut]"]
        for key, value in fields:
            if isinstance(value, tuple):
                if not value:
                    continue
                lines.append(f"{key} =")
                lines.extend(f"    {item}" for item in value)
            elif isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")
            else:
                lines.append(f"{key} = {value}")
        return "\n".join(lines) + "\n"


def detect_mutmut_environment(executable: str = "mutmut") -> MutationEnvironment:
    """Report fork and executable availability without importing mutmut."""

    if platform.system() == "Windows" or not hasattr(os, "fork"):
        return MutationEnvironment(
            available=False,
            executable=None,
            reason="mutmut 3.7 requires OS fork support; use Linux Docker or WSL",
        )
    resolved = shutil.which(executable)
    if resolved is None:
        return MutationEnvironment(
            available=False,
            executable=None,
            reason=f"{RECOMMENDED_MUTMUT_REQUIREMENT} is not installed on PATH",
        )
    return MutationEnvironment(available=True, executable=resolved, reason=None)


def build_mutmut_commands(executable: str, config: MutmutConfig) -> tuple[tuple[str, ...], ...]:
    """Build the fixed argv sequence used for qualification; no shell is involved."""

    _validate_text_argument(executable, field_name="mutmut executable")
    return (
        (executable, "--version"),
        (executable, "run", "--max-children", str(config.max_children)),
        (executable, "export-cicd-stats"),
        (executable, "results", "--all"),
    )


class MutmutAdapter:
    """Execute and reconcile one fresh qualification run in a disposable clone."""

    def __init__(self, *, executable: str = "mutmut") -> None:
        self.executable = executable

    def run(
        self,
        workspace: str | Path,
        config: MutmutConfig,
        *,
        manual_exclusions: Mapping[str, str] | None = None,
    ) -> MutationExecution:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Mutation workspace must be a directory")
        mutants_directory = root / "mutants"
        if mutants_directory.exists() or mutants_directory.is_symlink():
            raise ValueError(
                "Mutation qualification requires a fresh workspace without mutmut cache"
            )
        environment = detect_mutmut_environment(self.executable)
        if not environment.available or environment.executable is None:
            raise MutationEnvironmentUnavailable(environment.reason or "mutmut is unavailable")

        commands = build_mutmut_commands(environment.executable, config)
        config_text = config.render_setup_cfg()
        config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
        started_at = datetime.now(UTC)
        started_ns = monotonic_ns()

        with installed_mutmut_config(root, config_text):
            version_process = _run_command(commands[0], cwd=root, timeout=config.timeout_seconds)
            _require_success(version_process, commands[0], operation="version check")
            version = _parse_version(version_process.stdout or version_process.stderr)
            if version != SUPPORTED_MUTMUT_VERSION:
                raise MutationExecutionError(
                    f"Unsupported mutmut version {version}; expected {SUPPORTED_MUTMUT_VERSION}",
                    command=commands[0],
                    stdout=version_process.stdout,
                    stderr=version_process.stderr,
                )

            run_process = _run_command(commands[1], cwd=root, timeout=config.timeout_seconds)
            _require_success(run_process, commands[1], operation="mutation run")
            export_process = _run_command(commands[2], cwd=root, timeout=config.timeout_seconds)
            _require_success(export_process, commands[2], operation="CI statistics export")
            results_process = _run_command(commands[3], cwd=root, timeout=config.timeout_seconds)
            _require_success(results_process, commands[3], operation="status export")

            stats_path = root / "mutants" / "mutmut-cicd-stats.json"
            if (
                mutants_directory.is_symlink()
                or (hasattr(mutants_directory, "is_junction") and mutants_directory.is_junction())
                or stats_path.is_symlink()
                or not stats_path.is_file()
            ):
                raise MutationExecutionError(
                    "mutmut did not produce its CI statistics file",
                    command=commands[2],
                    stdout=export_process.stdout,
                    stderr=export_process.stderr,
                )
            try:
                stats_path.resolve(strict=True).relative_to(root)
            except (OSError, RuntimeError, ValueError) as error:
                raise MutationExecutionError(
                    "mutmut CI statistics escaped the qualification workspace",
                    command=commands[2],
                    stdout=export_process.stdout,
                    stderr=export_process.stderr,
                ) from error
            raw_stats = stats_path.read_text(encoding="utf-8")
            counts = parse_mutation_result(
                raw_stats,
                results_process.stdout,
                manual_exclusions=manual_exclusions,
            )

        finished_at = datetime.now(UTC)
        duration_ms = (monotonic_ns() - started_ns) // 1_000_000
        return MutationExecution(
            counts=counts,
            tool="mutmut",
            tool_version=version,
            commands=commands,
            config_sha256=config_digest,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            platform=platform.platform(),
            python_version=platform.python_version(),
            run_stdout=run_process.stdout,
            run_stderr=run_process.stderr,
            export_stdout=export_process.stdout,
            export_stderr=export_process.stderr,
            results_output=results_process.stdout,
            raw_stats_json=raw_stats,
        )


@contextmanager
def installed_mutmut_config(workspace: Path, config_text: str) -> Iterator[None]:
    """Temporarily install controlled config without replacing repository config."""

    pyproject = workspace / "pyproject.toml"
    if pyproject.exists():
        if pyproject.is_symlink() or not pyproject.is_file():
            raise ValueError("pyproject.toml must be a regular file")
        try:
            pyproject_data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError("Cannot inspect existing pyproject.toml") from error
        tool = pyproject_data.get("tool")
        if isinstance(tool, dict) and "mutmut" in tool:
            raise ValueError("Repository already defines [tool.mutmut]; refusing to override it")

    setup_cfg = workspace / "setup.cfg"
    if setup_cfg.is_symlink():
        raise ValueError("setup.cfg cannot be a symlink")
    original = setup_cfg.read_bytes() if setup_cfg.exists() else None
    if original is not None and b"[mutmut]" in original.lower():
        raise ValueError("Repository already defines [mutmut]; refusing to override it")

    prefix = b"" if not original or original.endswith((b"\n", b"\r")) else b"\n"
    setup_cfg.write_bytes((original or b"") + prefix + config_text.encode("utf-8"))
    try:
        yield
    finally:
        if original is None:
            setup_cfg.unlink(missing_ok=True)
        else:
            setup_cfg.write_bytes(original)


def _run_command(
    command: Sequence[str], *, cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MutationExecutionError(
            f"mutmut invocation failed: {error}", command=command, stdout="", stderr=""
        ) from error


def _require_success(
    completed: subprocess.CompletedProcess[str], command: Sequence[str], *, operation: str
) -> None:
    if completed.returncode != 0:
        raise MutationExecutionError(
            f"mutmut {operation} failed with exit code {completed.returncode}",
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _parse_version(output: str) -> str:
    match = _VERSION.search(output)
    if match is None:
        raise MutationExecutionError(
            "Could not parse mutmut version",
            command=("mutmut", "--version"),
            stdout=output,
            stderr="",
        )
    return match.group("version")


def _validate_relative_path(value: str) -> None:
    _validate_text_argument(value, field_name="mutmut path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if "\\" in value or posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError("mutmut paths must be portable repository-relative paths")
    if any(part in {"", ".", "..", ".git"} for part in posix.parts):
        raise ValueError("mutmut paths cannot contain traversal or .git")


def _validate_text_argument(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty single-line string")
    if any(character in value for character in "\x00\r\n"):
        raise ValueError(f"{field_name} must be a non-empty single-line string")
