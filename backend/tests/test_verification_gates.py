from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.repositories.workspace import DisposableWorkspace
from app.verification.docker import DockerExecution, DockerImageIdentity
from app.verification.gates import StandardGateFactory, StandardGateRunner

_IDENTITY = DockerImageIdentity("agenttrace-verifier:local", f"sha256:{'b' * 64}")


class StubDockerRunner:
    def __init__(self, result: DockerExecution) -> None:
        self.result = result
        self.arguments: dict[str, Any] | None = None

    def run(self, **arguments: Any) -> DockerExecution:
        self.arguments = arguments
        return self.result


def test_pytest_gate_commands_are_direct_and_write_junit_sidecars() -> None:
    visible = StandardGateFactory.visible_tests("python -m pytest -q tests", timeout_seconds=30)
    hidden = StandardGateFactory.hidden_tests(
        "python -m pytest -q {hidden_tests}", timeout_seconds=30
    )

    assert visible.command[:4] == ("python", "-I", "-m", "pytest")
    assert "-p" in visible.command
    assert "no:cacheprovider" in visible.command
    assert "--junitxml=/output/visible-tests.xml" in visible.command
    assert "/evaluator/hidden_tests" in hidden.command
    assert "{hidden_tests}" not in hidden.command


def test_test_gate_rejects_shell_and_mismatched_hidden_commands() -> None:
    with pytest.raises(ValueError, match="non-shell"):
        StandardGateFactory.visible_tests(
            "python -m pytest tests && python steal.py", timeout_seconds=30
        )
    with pytest.raises(ValueError, match="hidden test token"):
        StandardGateFactory.visible_tests("python -m pytest {hidden_tests}", timeout_seconds=30)


def test_gate_runner_normalizes_timeout_without_treating_it_as_test_failure(
    tmp_path: Path,
) -> None:
    execution = DockerExecution(
        container_name="agentrace-deadbeef",
        image=_IDENTITY,
        command=("python", "-m", "pytest"),
        exit_code=None,
        duration_ms=1_000,
        stdout="partial",
        stderr="",
        timed_out=True,
    )
    docker = StubDockerRunner(execution)
    runner = StandardGateRunner(docker, _IDENTITY)  # type: ignore[arg-type]
    workspace = DisposableWorkspace("test-run", tmp_path / "workspace", "a" * 40)

    result = runner.run(
        StandardGateFactory.compile(timeout_seconds=1),
        workspace=workspace,
    )

    assert result.status == "timed_out"
    assert result.exit_code is None
    assert "hard timeout" in result.summary
    assert docker.arguments is not None
    assert docker.arguments["command"] == StandardGateFactory.compile(timeout_seconds=1).command
