"""Deterministic standard verification-gate command primitives."""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.repositories.workspace import DisposableWorkspace

from .native import (
    WindowsExecution,
    WindowsExecutionEnvironment,
    WindowsVerificationRunner,
)

GateStatus = Literal["passed", "failed", "timed_out", "error"]
GateName = Literal[
    "python_compile",
    "visible_tests",
    "existing_tests",
    "hidden_tests",
    "ruff",
    "mypy",
    "bandit",
]
_SHELL_TOKENS = {"&&", "||", ";", "|", "<", ">", "`"}
_SAFE_OUTPUT_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,50}\.xml$")


@dataclass(frozen=True, slots=True)
class GateSpec:
    gate: GateName
    required: bool
    command: tuple[str, ...]
    timeout_seconds: int

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("gate command cannot be empty")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("gate timeout must be between 1 and 3600 seconds")


@dataclass(frozen=True, slots=True)
class GateOutcome:
    gate: GateName
    required: bool
    status: GateStatus
    exit_code: int | None
    duration_ms: int
    summary: str
    stdout: str
    stderr: str
    output_truncated: bool

    @property
    def passed(self) -> bool:
        return self.status == "passed"


class StandardGateFactory:
    """Build inspectable, non-shelling argv for standard Python gates."""

    @staticmethod
    def compile(*, timeout_seconds: int = 60) -> GateSpec:
        return GateSpec(
            "python_compile",
            True,
            ("python", "-I", "-m", "compileall", "-q", "-f", "."),
            timeout_seconds,
        )

    @staticmethod
    def visible_tests(command: str, *, timeout_seconds: int) -> GateSpec:
        return GateSpec(
            "visible_tests",
            True,
            _pytest_command(command, output_name="visible-tests.xml", hidden=False),
            timeout_seconds,
        )

    @staticmethod
    def existing_tests(command: str, *, timeout_seconds: int) -> GateSpec:
        return GateSpec(
            "existing_tests",
            True,
            _pytest_command(command, output_name="existing-tests.xml", hidden=False),
            timeout_seconds,
        )

    @staticmethod
    def hidden_tests(command: str, *, timeout_seconds: int) -> GateSpec:
        return GateSpec(
            "hidden_tests",
            True,
            _pytest_command(command, output_name="hidden-tests.xml", hidden=True),
            timeout_seconds,
        )

    @staticmethod
    def ruff(paths: Sequence[str] = (".",), *, timeout_seconds: int = 60) -> GateSpec:
        return GateSpec(
            "ruff",
            False,
            (
                "python",
                "-I",
                "-m",
                "ruff",
                "check",
                "--no-cache",
                "--output-format=concise",
                *paths,
            ),
            timeout_seconds,
        )

    @staticmethod
    def mypy(paths: Sequence[str] = (".",), *, timeout_seconds: int = 120) -> GateSpec:
        return GateSpec(
            "mypy",
            False,
            (
                "python",
                "-I",
                "-m",
                "mypy",
                "--no-incremental",
                "--cache-dir=/output/mypy-cache",
                *paths,
            ),
            timeout_seconds,
        )

    @staticmethod
    def bandit(paths: Sequence[str] = (".",), *, timeout_seconds: int = 120) -> GateSpec:
        return GateSpec(
            "bandit",
            False,
            (
                "python",
                "-I",
                "-m",
                "bandit",
                "-q",
                "-r",
                *paths,
                "-f",
                "json",
                "-o",
                "/output/bandit.json",
            ),
            timeout_seconds,
        )


class StandardGateRunner:
    """Map restricted native execution into normalized internal gate outcomes."""

    def __init__(
        self,
        runner: WindowsVerificationRunner,
        execution_environment: WindowsExecutionEnvironment,
    ) -> None:
        self.runner = runner
        self.execution_environment = execution_environment

    def run(
        self,
        spec: GateSpec,
        *,
        workspace: DisposableWorkspace,
        evaluator_root: str | Path | None = None,
        output_root: str | Path | None = None,
    ) -> GateOutcome:
        execution = self.runner.run(
            workspace=workspace,
            execution_environment=self.execution_environment,
            command=spec.command,
            timeout_seconds=spec.timeout_seconds,
            evaluator_root=evaluator_root,
            output_root=output_root,
        )
        status, summary = _status_and_summary(execution)
        return GateOutcome(
            gate=spec.gate,
            required=spec.required,
            status=status,
            exit_code=execution.exit_code,
            duration_ms=execution.duration_ms,
            summary=summary,
            stdout=execution.stdout,
            stderr=execution.stderr,
            output_truncated=execution.output_truncated,
        )


def _pytest_command(command: str, *, output_name: str, hidden: bool) -> tuple[str, ...]:
    if _SAFE_OUTPUT_NAME.fullmatch(output_name) is None:
        raise ValueError("pytest output filename is invalid")
    arguments = shlex.split(command, posix=True)
    if not arguments or any(argument in _SHELL_TOKENS for argument in arguments):
        raise ValueError("test command must be a non-shell pytest command")
    if arguments[0] in {"pytest", "py.test"}:
        pytest_arguments = arguments[1:]
    elif (
        len(arguments) >= 3
        and arguments[0] in {"python", "python3"}
        and arguments[1:3] == ["-m", "pytest"]
    ):
        pytest_arguments = arguments[3:]
    else:
        raise ValueError("verification test gates support pytest commands only")
    has_hidden_token = "{hidden_tests}" in pytest_arguments
    if hidden != has_hidden_token:
        raise ValueError("hidden test token does not match the gate type")
    if any(
        argument == "--junitxml" or argument.startswith("--junitxml=")
        for argument in pytest_arguments
    ):
        raise ValueError("test command cannot override verification result output")
    resolved = [
        "/evaluator/hidden_tests" if argument == "{hidden_tests}" else argument
        for argument in pytest_arguments
    ]
    return (
        "python",
        "-I",
        "-m",
        "pytest",
        *resolved,
        "-p",
        "no:cacheprovider",
        f"--junitxml=/output/{output_name}",
    )


def _status_and_summary(execution: WindowsExecution) -> tuple[GateStatus, str]:
    if execution.infrastructure_error is not None:
        return "error", "Native verification process was unavailable"
    if execution.timed_out:
        return "timed_out", "Verification gate exceeded its hard timeout"
    if execution.exit_code == 0:
        return "passed", "Verification gate completed successfully"
    return "failed", f"Verification gate exited with code {execution.exit_code}"
