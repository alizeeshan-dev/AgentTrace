from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.agent.budgets import AgentBudgets
from app.benchmark import load_benchmark_task
from app.config import Settings
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import PatchArtifact, Repository, Run, Task, VerificationResult
from app.verification import DockerExecution, DockerImageIdentity, VerificationService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"
MANIFEST = BENCHMARK_ROOT / "tasks" / "boundary-empty-input.yaml"
IMAGE = DockerImageIdentity("agentrace-verifier:phase6", f"sha256:{'a' * 64}")


class ScenarioDocker:
    """Container-result seam; it never imports or executes repository code."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls: list[tuple[str, ...]] = []

    def inspect_image(self, reference: str) -> DockerImageIdentity:
        assert reference == IMAGE.reference
        return IMAGE

    def run(self, **kwargs: Any) -> DockerExecution:
        command = tuple(kwargs["command"])
        workspace = kwargs["workspace"]
        output = Path(kwargs["output_root"])
        self.calls.append(command)
        baseline = workspace.run_id.endswith("-vbase")
        gate = _gate_name(command)
        status = _scenario_status(self.scenario, gate, baseline=baseline)
        if gate in {"visible_tests", "existing_tests", "hidden_tests", "hypothesis_properties"}:
            filename = {
                "visible_tests": "visible-tests.xml",
                "existing_tests": "existing-tests.xml",
                "hidden_tests": "hidden-tests.xml",
                "hypothesis_properties": "property-tests.xml",
            }[gate]
            test_name = "existing_regression" if gate == "existing_tests" else gate
            _write_junit(output / filename, test_name=test_name, failed=status == "failed")
        if gate == "hypothesis_properties" and status == "failed":
            (output / "property-counterexamples.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "counterexamples": [
                            {
                                "input": [0.0, 1.0],
                                "expected": 0.5,
                                "observed": 0.0,
                                "exception_type": "AssertionError",
                                "location_hints": ["ministats/summary.py:7"],
                                "shrunk": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        timed_out = status == "timed_out"
        return DockerExecution(
            container_name="agentrace-fixture",
            image=IMAGE,
            command=command,
            exit_code=None if timed_out else (0 if status == "passed" else 1),
            duration_ms=5,
            stdout="",
            stderr="",
            timed_out=timed_out,
        )


def _gate_name(command: tuple[str, ...]) -> str:
    if "compileall" in command:
        return "python_compile"
    if "agentrace_property_plugin" in command:
        return "hypothesis_properties"
    if any(value.endswith("hidden-tests.xml") for value in command):
        return "hidden_tests"
    if any(value.endswith("visible-tests.xml") for value in command):
        return "visible_tests"
    if any(value.endswith("existing-tests.xml") for value in command):
        return "existing_tests"
    for advisory in ("ruff", "mypy", "bandit"):
        if advisory in command:
            return advisory
    return "symbolic"


def _scenario_status(scenario: str, gate: str, *, baseline: bool) -> str:
    if baseline:
        return "failed" if gate in {"hidden_tests", "hypothesis_properties"} else "passed"
    failures = {
        "syntax": "python_compile",
        "regression": "existing_tests",
        "hidden": "hidden_tests",
        "property": "hypothesis_properties",
        "timeout": "visible_tests",
    }
    if failures.get(scenario) == gate:
        return "timed_out" if scenario == "timeout" else "failed"
    return "passed"


def _write_junit(path: Path, *, test_name: str, failed: bool) -> None:
    failure = '<failure message="fixture" />' if failed else ""
    body = (
        f'<testsuite tests="1"><testcase classname="tests" name="{test_name}">'
        f"{failure}</testcase></testsuite>"
    )
    path.write_text(
        body,
        encoding="utf-8",
    )


def _seed(tmp_path: Path, patch_name: str) -> tuple[Settings, object, object]:
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    assert loaded.repository_path is not None
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
    patch_path = (
        loaded.known_correct_patch_path
        if patch_name == "correct"
        else BENCHMARK_ROOT / "verification_patches" / f"boundary-{patch_name}.patch"
    )
    unified_diff = patch_path.read_text(encoding="utf-8")
    with sessions.begin() as session:
        session.add(
            Repository(
                repository_id="repo-boundary",
                name="boundary-empty-input",
                source=str(loaded.repository_path),
                base_commit=loaded.task.base_commit,
                python_version="3.12",
                test_command=loaded.task.visible_test_command,
            )
        )
        session.add(
            Task(
                task_id=loaded.task.task_id,
                repository_id="repo-boundary",
                title=loaded.task.title,
                description=loaded.task.description,
                task_category=loaded.task.task_category,
                difficulty=loaded.task.difficulty,
                allowed_paths=loaded.task.allowed_paths,
                forbidden_paths=loaded.task.forbidden_paths,
                visible_test_command=loaded.task.visible_test_command,
                hidden_test_command=loaded.task.hidden_test_command,
                property_profile=loaded.task.property_profile,
                symbolic_profile=loaded.task.symbolic_profile,
                known_correct_patch="qualification-ref",
            )
        )
        session.flush()
        session.add(
            Run(
                run_id=f"verify-{patch_name}",
                task_id=loaded.task.task_id,
                configuration_id="A",
                model="fixture",
                model_parameters={"agent_budgets": AgentBudgets().model_dump()},
                status="patch_submitted",
                started_at=datetime.now(UTC),
                input_tokens=0,
                output_tokens=0,
                tool_calls=0,
                files_read=0,
                lines_exposed=0,
                repair_attempted=False,
            )
        )
        session.flush()
        session.add(
            PatchArtifact(
                run_id=f"verify-{patch_name}",
                attempt_number=1,
                unified_diff=unified_diff,
                files_changed=["ministats/summary.py"],
                lines_added=1,
                lines_removed=0,
                applied_successfully=True,
            )
        )
    return Settings(state_dir=tmp_path / patch_name), engine, sessions


@pytest.mark.parametrize(
    ("patch_name", "scenario", "resolved", "failure"),
    [
        ("correct", "correct", True, None),
        ("syntax-error", "syntax", False, "REGRESSION"),
        ("regression", "regression", False, "REGRESSION"),
        ("hidden-failure", "hidden", False, "HIDDEN_TEST_FAILURE"),
        ("property-edge", "property", False, "HYPOTHESIS_COUNTEREXAMPLE"),
        ("timeout", "timeout", False, "TIMEOUT"),
    ],
)
def test_six_critical_verification_scenarios(
    tmp_path: Path,
    patch_name: str,
    scenario: str,
    resolved: bool,
    failure: str | None,
) -> None:
    settings, engine, sessions = _seed(tmp_path, patch_name)
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    assert loaded.repository_path is not None
    source_before = loaded.repository_path.read_bytes()
    fake = ScenarioDocker(scenario)
    with sessions.begin() as session:
        result = VerificationService(
            session,
            settings=settings,
            docker=fake,  # type: ignore[arg-type]
        ).verify(
            MANIFEST,
            run_id=f"verify-{patch_name}",
            benchmark_root=BENCHMARK_ROOT,
        )
        run = session.get(Run, f"verify-{patch_name}")
        rows = session.query(VerificationResult).filter_by(run_id=f"verify-{patch_name}").all()
        assert run is not None
        assert run.final_resolution is resolved
        assert run.failure_category == failure
        assert len(rows) == len(result.results)
    assert loaded.repository_path.read_bytes() == source_before
    assert not any(settings.effective_workspace_root.iterdir())
    if scenario == "regression":
        assert result.regression is True
        gate = next(item for item in result.results if item.gate == "existing_tests")
        assert gate.baseline_difference == {
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "new_failures": ["tests::existing_regression"],
            "fixed_failures": [],
            "remaining_failures": [],
            "baseline_status": "passed",
        }
    if scenario == "property":
        gate = next(item for item in result.results if item.gate == "hypothesis_properties")
        evidence = gate.baseline_difference
        assert evidence is not None
        assert evidence["counterexamples"][0]["input_summary"] == "[0.0,1.0]"
        assert "test_properties.py" not in json.dumps(evidence)
    if scenario == "timeout":
        assert (
            next(item for item in result.results if item.gate == "visible_tests").status
            == "timed_out"
        )
    engine.dispose()
