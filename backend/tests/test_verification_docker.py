from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.repositories.workspace import DisposableWorkspace
from app.verification.docker import (
    DockerEnvironmentError,
    DockerImageIdentity,
    DockerRunner,
    ProcessOutcome,
)

_IMAGE_ID = f"sha256:{'a' * 64}"


class FakeProcessExecutor:
    def __init__(self, outcomes: list[ProcessOutcome]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessOutcome:
        del timeout_seconds, max_output_chars
        self.calls.append(tuple(argv))
        return self.outcomes.pop(0)


def _workspace(tmp_path: Path) -> tuple[Path, DisposableWorkspace]:
    root = tmp_path / "workspaces"
    path = root / "verification-run"
    (path / ".git").mkdir(parents=True)
    (path / "package.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root, DisposableWorkspace(
        run_id="verification-run",
        path=path,
        base_commit="a" * 40,
    )


def test_docker_argv_enforces_isolation_and_read_only_repository(tmp_path: Path) -> None:
    workspace_root, workspace = _workspace(tmp_path)
    evaluator = tmp_path / "evaluator"
    output = tmp_path / "output"
    evaluator.mkdir()
    output.mkdir()
    executor = FakeProcessExecutor(
        [
            ProcessOutcome(1, "", "not found"),
            ProcessOutcome(0, "ok", ""),
            ProcessOutcome(1, "", "already removed"),
        ]
    )
    runner = DockerRunner(workspace_root, executor=executor)

    result = runner.run(
        workspace=workspace,
        image=DockerImageIdentity("agenttrace-verifier:local", _IMAGE_ID),
        command=("python", "-m", "pytest", "-q"),
        timeout_seconds=15,
        evaluator_root=evaluator,
        output_root=output,
    )

    argv = executor.calls[1]
    assert result.succeeded
    assert argv[:3] == ("docker", "run", "--rm")
    for required in (
        "--init",
        "--read-only",
        "--cpus",
        "--memory",
        "--memory-swap",
        "--pids-limit",
        "--cap-drop",
        "--security-opt",
    ):
        assert required in argv
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--ipc") + 1] == "none"
    ulimits = [argv[index + 1] for index, value in enumerate(argv) if value == "--ulimit"]
    assert "nofile=1024:1024" in ulimits
    assert "fsize=16777216:16777216" in ulimits
    assert argv[argv.index("--user") + 1] == "65532:65532"
    assert "ALL" in argv
    assert "no-new-privileges=true" in argv
    mounts = [argv[index + 1] for index, value in enumerate(argv) if value == "--mount"]
    assert any("target=/workspace,readonly" in mount for mount in mounts)
    assert any("target=/evaluator,readonly" in mount for mount in mounts)
    assert any("target=/output" in mount and "readonly" not in mount for mount in mounts)
    assert all("docker.sock" not in mount for mount in mounts)
    assert argv[-4:] == ("--entrypoint", "python", _IMAGE_ID, "-m", "pytest", "-q")[-4:]
    assert executor.calls[0][1:3] == ("rm", "--force")
    assert executor.calls[2][1:3] == ("rm", "--force")


def test_timeout_is_controlled_and_forces_named_container_cleanup(tmp_path: Path) -> None:
    workspace_root, workspace = _workspace(tmp_path)
    executor = FakeProcessExecutor(
        [
            ProcessOutcome(1, "", "not found"),
            ProcessOutcome(None, "partial", "", timed_out=True),
            ProcessOutcome(0, "", ""),
        ]
    )
    runner = DockerRunner(workspace_root, executor=executor)

    result = runner.run(
        workspace=workspace,
        image=DockerImageIdentity("agenttrace-verifier:local", _IMAGE_ID),
        command=("python", "infinite.py"),
        timeout_seconds=1,
    )

    assert result.timed_out
    container_name = result.container_name
    assert executor.calls[0] == ("docker", "rm", "--force", container_name)
    assert executor.calls[2] == ("docker", "rm", "--force", container_name)


def test_controlled_property_environment_is_allowed_but_overrides_are_rejected(
    tmp_path: Path,
) -> None:
    workspace_root, workspace = _workspace(tmp_path)
    executor = FakeProcessExecutor(
        [
            ProcessOutcome(1, "", "not found"),
            ProcessOutcome(0, "ok", ""),
            ProcessOutcome(1, "", "already removed"),
        ]
    )
    runner = DockerRunner(workspace_root, executor=executor)
    runner.run(
        workspace=workspace,
        image=DockerImageIdentity("agenttrace-verifier:local", _IMAGE_ID),
        command=("python", "-m", "pytest"),
        timeout_seconds=10,
        environment=(
            ("AGENTTRACE_COUNTEREXAMPLE_FILE", "/output/counterexample.json"),
            ("HYPOTHESIS_PROFILE", "agentrace"),
            ("PYTHONHASHSEED", "0"),
            ("PYTHONPATH", "/workspace:/evaluator/runtime"),
        ),
    )
    run_argv = executor.calls[1]
    assert "AGENTTRACE_COUNTEREXAMPLE_FILE=/output/counterexample.json" in run_argv
    assert "PYTHONPATH=/workspace:/evaluator/runtime" in run_argv

    with pytest.raises(ValueError, match="cannot override HOME"):
        runner.run(
            workspace=workspace,
            image=DockerImageIdentity("agenttrace-verifier:local", _IMAGE_ID),
            command=("python", "-m", "pytest"),
            timeout_seconds=10,
            environment={"HOME": "/workspace"},
        )


def test_image_identity_is_inspected_and_unavailable_docker_is_explicit(tmp_path: Path) -> None:
    workspace_root, _ = _workspace(tmp_path)
    available = FakeProcessExecutor([ProcessOutcome(0, f"{_IMAGE_ID}\n", "")])
    identity = DockerRunner(workspace_root, executor=available).inspect_image(
        "agenttrace-verifier:local"
    )
    assert identity.image_id == _IMAGE_ID
    assert available.calls[0][1:3] == ("image", "inspect")

    unavailable = FakeProcessExecutor(
        [ProcessOutcome(None, "", "", launch_error="Docker unavailable: not found")]
    )
    with pytest.raises(DockerEnvironmentError, match="Docker unavailable"):
        DockerRunner(workspace_root, executor=unavailable).inspect_image(
            "agenttrace-verifier:local"
        )


def test_original_or_unmanaged_repository_cannot_be_mounted(tmp_path: Path) -> None:
    workspace_root, _ = _workspace(tmp_path)
    original = tmp_path / "original"
    (original / ".git").mkdir(parents=True)
    executor = FakeProcessExecutor([])
    runner = DockerRunner(workspace_root, executor=executor)

    with pytest.raises(ValueError, match="managed disposable workspace"):
        runner.run(
            workspace=DisposableWorkspace("original", original, "a" * 40),
            image=DockerImageIdentity("agenttrace-verifier:local", _IMAGE_ID),
            command=("python", "-m", "pytest"),
            timeout_seconds=10,
        )
    assert executor.calls == []


def test_output_mount_must_be_a_dedicated_empty_directory(tmp_path: Path) -> None:
    workspace_root, workspace = _workspace(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "unrelated-secret.txt").write_text("do not mount", encoding="utf-8")
    executor = FakeProcessExecutor([])

    with pytest.raises(ValueError, match="dedicated empty directory"):
        DockerRunner(workspace_root, executor=executor).run(
            workspace=workspace,
            image=DockerImageIdentity("agenttrace-verifier:local", _IMAGE_ID),
            command=("python", "-m", "pytest"),
            timeout_seconds=10,
            output_root=output,
        )
    assert executor.calls == []
