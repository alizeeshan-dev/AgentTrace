"""Bounded pytest-gremlins adapter used only for benchmark qualification."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from time import monotonic_ns

from app.mutation.models import MutationEnvironment, MutationExecution
from app.mutation.parser import MutationParseError, parse_gremlins_report

SUPPORTED_PYTEST_GREMLINS_VERSION = "1.9.0"
RECOMMENDED_PYTEST_GREMLINS_REQUIREMENT = (
    f"pytest-gremlins=={SUPPORTED_PYTEST_GREMLINS_VERSION}"
)
PYTEST_GREMLINS_REPORT = PurePosixPath("coverage/gremlins/gremlins.json")
_VERSION = re.compile(r"\b(?P<version>\d+\.\d+\.\d+)\b")
_VERSION_SCRIPT = (
    "from importlib.metadata import version; "
    "print(version('pytest-gremlins'))"
)
_SAFE_ENVIRONMENT_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "WINDIR",
)


class MutationEnvironmentUnavailable(RuntimeError):
    """Raised when mutation qualification cannot run in this environment."""


class MutationExecutionError(RuntimeError):
    """Raised when a mutation subprocess or its evidence collection fails."""

    def __init__(
        self,
        message: str,
        *,
        command: Sequence[str],
        stdout: str,
        stderr: str,
    ) -> None:
        super().__init__(message)
        self.command = tuple(command)
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class PytestGremlinsConfig:
    """Qualification-only configuration independent of repository config files."""

    source_paths: tuple[str, ...]
    test_selection: tuple[str, ...]
    pytest_args: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    operators: tuple[str, ...] = ()
    workers: int = 1
    timeout_seconds: int = 600
    max_output_chars: int = 250_000

    def __post_init__(self) -> None:
        if not self.source_paths:
            raise ValueError("At least one mutation source path is required")
        if not self.test_selection:
            raise ValueError("At least one pytest test-selection path is required")
        for value in (*self.source_paths, *self.test_selection):
            _validate_relative_path(value)
        for value in self.pytest_args:
            _validate_text_argument(value, field_name="pytest argument")
            if value.startswith("--gremlin") or value == "--gremlins":
                raise ValueError("pytest-gremlins options must use typed config fields")
        for value in self.exclude:
            _validate_text_argument(value, field_name="mutation exclusion pattern")
        for value in self.operators:
            _validate_text_argument(value, field_name="mutation operator")
            if not value.replace("-", "").replace("_", "").isalnum():
                raise ValueError("Mutation operators must be simple identifiers")
        if self.workers < 1 or self.workers > 32:
            raise ValueError("workers must be between 1 and 32")
        if self.timeout_seconds < 1 or self.timeout_seconds > 86_400:
            raise ValueError("timeout_seconds must be positive and bounded")
        if self.max_output_chars < 1 or self.max_output_chars > 2_000_000:
            raise ValueError("max_output_chars must be positive and bounded")

    def canonical_json(self) -> str:
        """Return deterministic configuration evidence for hashing."""

        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def detect_pytest_gremlins_environment(
    python_executable: str = sys.executable,
) -> MutationEnvironment:
    """Report whether the configured Python interpreter can be invoked."""

    resolved = _resolve_executable(python_executable)
    if resolved is None:
        return MutationEnvironment(
            available=False,
            executable=None,
            reason=f"Python interpreter {python_executable!r} is unavailable",
        )
    return MutationEnvironment(available=True, executable=resolved, reason=None)


def build_pytest_gremlins_commands(
    python_executable: str,
    config: PytestGremlinsConfig,
) -> tuple[tuple[str, ...], ...]:
    """Build the fixed argv sequence used for qualification; no shell is used."""

    _validate_text_argument(python_executable, field_name="Python executable")
    targets = ",".join(config.source_paths)
    command = [
        python_executable,
        "-I",
        "-m",
        "pytest",
        "--gremlins",
        "--gremlin-report=json",
        f"--gremlin-targets={targets}",
        f"--gremlin-workers={config.workers}",
    ]
    if config.operators:
        command.append(f"--gremlin-operators={','.join(config.operators)}")
    for pattern in config.exclude:
        command.append(f"--gremlin-exclude={pattern}")
    command.extend(config.pytest_args)
    command.extend(config.test_selection)
    return (
        (python_executable, "-I", "-c", _VERSION_SCRIPT),
        tuple(command),
    )


class PytestGremlinsAdapter:
    """Execute and normalize one fresh mutation run in a disposable workspace."""

    def __init__(self, *, python_executable: str = sys.executable) -> None:
        self.python_executable = python_executable

    def run(
        self,
        workspace: str | Path,
        config: PytestGremlinsConfig,
        *,
        manual_exclusions: Mapping[str, str] | None = None,
    ) -> MutationExecution:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Mutation workspace must be a directory")

        environment = detect_pytest_gremlins_environment(self.python_executable)
        if not environment.available or environment.executable is None:
            raise MutationEnvironmentUnavailable(
                environment.reason or "pytest-gremlins environment is unavailable"
            )
        commands = build_pytest_gremlins_commands(environment.executable, config)
        report_path = _prepare_report_path(root)
        config_json = config.canonical_json()
        config_digest = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
        started_at = datetime.now(UTC)
        started_ns = monotonic_ns()
        process_environment = _sanitized_environment(root)

        version_process = _run_command(
            commands[0],
            cwd=root,
            timeout=config.timeout_seconds,
            environment=process_environment,
        )
        if version_process.returncode != 0:
            raise MutationEnvironmentUnavailable(
                f"{RECOMMENDED_PYTEST_GREMLINS_REQUIREMENT} is unavailable to "
                f"{environment.executable}"
            )
        version = _parse_version(version_process.stdout or version_process.stderr)
        if version != SUPPORTED_PYTEST_GREMLINS_VERSION:
            raise MutationExecutionError(
                f"Unsupported pytest-gremlins version {version}; expected "
                f"{RECOMMENDED_PYTEST_GREMLINS_REQUIREMENT}",
                command=commands[0],
                stdout=version_process.stdout,
                stderr=version_process.stderr,
            )

        run_process = _run_command(
            commands[1],
            cwd=root,
            timeout=config.timeout_seconds,
            environment=process_environment,
        )
        _require_success(run_process, commands[1], operation="mutation run")
        _validate_report_path(root, report_path, command=commands[1], process=run_process)
        try:
            raw_report = report_path.read_text(encoding="utf-8")
            counts = parse_gremlins_report(
                raw_report,
                manual_exclusions=manual_exclusions,
            )
        except (OSError, UnicodeError, MutationParseError) as error:
            raise MutationExecutionError(
                f"Could not normalize pytest-gremlins JSON report: {error}",
                command=commands[1],
                stdout=_bounded(run_process.stdout, config.max_output_chars),
                stderr=_bounded(run_process.stderr, config.max_output_chars),
            ) from error
        report_data = json.loads(raw_report)
        tool_reported_score = float(report_data["summary"]["percentage"]) / 100.0

        finished_at = datetime.now(UTC)
        duration_ms = (monotonic_ns() - started_ns) // 1_000_000
        return MutationExecution(
            counts=counts,
            tool="pytest-gremlins",
            tool_version=version,
            tool_reported_score=tool_reported_score,
            commands=commands,
            config_sha256=config_digest,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            platform=platform.platform(),
            python_version=platform.python_version(),
            run_stdout=_bounded(run_process.stdout, config.max_output_chars),
            run_stderr=_bounded(run_process.stderr, config.max_output_chars),
            report_relative_path=PYTEST_GREMLINS_REPORT.as_posix(),
            raw_report_json=raw_report,
        )


def _prepare_report_path(root: Path) -> Path:
    report = root.joinpath(*PYTEST_GREMLINS_REPORT.parts)
    for parent in (report.parent.parent, report.parent):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("pytest-gremlins report directory cannot be a link or junction")
    if report.exists() or report.is_symlink():
        raise ValueError("Mutation qualification requires a fresh JSON report path")
    return report


def _validate_report_path(
    root: Path,
    report: Path,
    *,
    command: Sequence[str],
    process: subprocess.CompletedProcess[str],
) -> None:
    if report.is_symlink() or not report.is_file():
        raise MutationExecutionError(
            "pytest-gremlins did not produce its JSON report",
            command=command,
            stdout=_bounded(process.stdout, 250_000),
            stderr=_bounded(process.stderr, 250_000),
        )
    try:
        report.resolve(strict=True).relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise MutationExecutionError(
            "pytest-gremlins JSON report escaped the qualification workspace",
            command=command,
            stdout=_bounded(process.stdout, 250_000),
            stderr=_bounded(process.stderr, 250_000),
        ) from error


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(environment),
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
            f"pytest-gremlins invocation failed: {error}",
            command=command,
            stdout="",
            stderr="",
        ) from error


def _require_success(
    completed: subprocess.CompletedProcess[str],
    command: Sequence[str],
    *,
    operation: str,
) -> None:
    if completed.returncode != 0:
        raise MutationExecutionError(
            f"pytest-gremlins {operation} failed with exit code {completed.returncode}",
            command=command,
            stdout=_bounded(completed.stdout, 250_000),
            stderr=_bounded(completed.stderr, 250_000),
        )


def _sanitized_environment(workspace: Path) -> dict[str, str]:
    private_home = workspace / ".agenttrace-mutation-environment"
    private_home.mkdir(exist_ok=False)
    environment = {
        key: value
        for key in _SAFE_ENVIRONMENT_KEYS
        if (value := os.environ.get(key)) is not None
    }
    environment.update(
        {
            "HOME": str(private_home),
            "NO_COLOR": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "TEMP": str(private_home),
            "TMP": str(private_home),
            "USERPROFILE": str(private_home),
        }
    )
    return environment


def _resolve_executable(value: str) -> str | None:
    _validate_text_argument(value, field_name="Python executable")
    candidate = Path(value)
    if candidate.is_absolute():
        return str(candidate.resolve()) if candidate.is_file() else None
    return shutil.which(value)


def _parse_version(output: str) -> str:
    match = _VERSION.search(output)
    if match is None:
        raise MutationExecutionError(
            "Could not parse pytest-gremlins version",
            command=("python", "-c", _VERSION_SCRIPT),
            stdout=output,
            stderr="",
        )
    return match.group("version")


def _validate_relative_path(value: str) -> None:
    _validate_text_argument(value, field_name="mutation path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        "," in value
        or "\\" in value
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
    ):
        raise ValueError("Mutation paths must be portable repository-relative paths")
    if any(part in {"", ".", "..", ".git"} for part in posix.parts):
        raise ValueError("Mutation paths cannot contain traversal or .git")


def _validate_text_argument(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty single-line string")
    if any(character in value for character in "\x00\r\n"):
        raise ValueError(f"{field_name} must be a non-empty single-line string")


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = "\n...[pytest-gremlins output truncated by AgentTrace]"
    return value[: max(0, limit - len(marker))] + marker
