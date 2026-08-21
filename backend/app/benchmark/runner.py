"""Bounded local execution for trusted benchmark-qualification commands."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic_ns


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    command: tuple[str, ...]
    exit_code: int | None
    duration_ms: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class QualificationCommandRunner:
    """Run evaluator-authored commands without a shell.

    This runner exists only for trusted pilot qualification. Agent-controlled
    commands are never accepted here. Phase 6 must use the isolated Docker
    verifier for arbitrary repository execution.
    """

    def __init__(self, *, max_output_chars: int = 200_000) -> None:
        if max_output_chars < 1:
            raise ValueError("max_output_chars must be positive")
        self.max_output_chars = max_output_chars

    def run(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_seconds: int,
        hidden_tests: Path | None = None,
    ) -> CommandOutcome:
        arguments = shlex.split(command, posix=True)
        if not arguments:
            raise ValueError("qualification command cannot be empty")
        arguments = [
            str(hidden_tests) if value == "{hidden_tests}" and hidden_tests else value
            for value in arguments
        ]
        if "{hidden_tests}" in arguments:
            raise ValueError("hidden test command requires an evaluator path")
        if arguments[0] in {"python", "python3"}:
            arguments[0] = sys.executable
        elif arguments[0] in {"pytest", "py.test"}:
            arguments = [sys.executable, "-m", "pytest", *arguments[1:]]

        started_ns = monotonic_ns()
        try:
            completed = subprocess.run(
                arguments,
                cwd=cwd,
                env=_qualification_environment(cwd),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            duration_ms = (monotonic_ns() - started_ns) // 1_000_000
            return CommandOutcome(
                tuple(arguments),
                None,
                duration_ms,
                _bounded_text(error.stdout, self.max_output_chars),
                _bounded_text(error.stderr, self.max_output_chars),
                timed_out=True,
            )
        duration_ms = (monotonic_ns() - started_ns) // 1_000_000
        return CommandOutcome(
            tuple(arguments),
            completed.returncode,
            duration_ms,
            _bounded_text(completed.stdout, self.max_output_chars),
            _bounded_text(completed.stderr, self.max_output_chars),
        )


def _qualification_environment(repository: Path) -> dict[str, str]:
    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(repository),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    return environment


def _bounded_text(value: str | bytes | None, limit: int) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[output truncated by AgentTrace]"
