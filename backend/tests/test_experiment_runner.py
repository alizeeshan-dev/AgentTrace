from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.benchmark import load_benchmark_task
from app.config import Settings
from app.configurations import ConfigurationExecution, ConfigurationRunner
from app.db import create_database_engine, init_database, make_session_factory
from app.db.models import Repository, Run, Task, VerificationResult
from app.experiments import (
    ExperimentConfig,
    ExperimentDataLayout,
    ExperimentRunner,
    FailureCategory,
    ManualFailureAnnotation,
    SlotStatus,
    classify_run,
    load_experiment_config,
)
from app.experiments.cli import main as experiment_cli_main
from app.verification import VerificationRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"
MANIFEST = BENCHMARK_ROOT / "tasks" / "boundary-empty-input.yaml"
PILOT = PROJECT_ROOT / "experiments" / "pilot.yaml"


def _sessions() -> object:
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    assert loaded.repository_path is not None
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        session.add(
            Repository(
                repository_id="experiment-repo",
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
                repository_id="experiment-repo",
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
                known_correct_patch="fixture",
            )
        )
    return sessions


def _single_config(*, condition: str = "A", experiment_id: str = "runner-test") -> ExperimentConfig:
    pilot = load_experiment_config(PILOT).model_dump(mode="json")
    selected = next(item for item in pilot["configurations"] if item["condition"] == condition)
    pilot.update(
        {
            "experiment_id": experiment_id,
            "tasks": ["tasks/boundary-empty-input.yaml"],
            "configurations": [selected],
        }
    )
    return ExperimentConfig.model_validate(pilot)


class PatchSubmittedExecutor:
    def __init__(self, session: Session, calls: list[str]) -> None:
        self.session = session
        self.calls = calls

    def execute(self, execution: ConfigurationExecution) -> object:
        self.calls.append(execution.run_id)
        now = datetime.now(UTC)
        self.session.add(
            Run(
                run_id=execution.run_id,
                task_id=execution.loaded_task.task.task_id,
                configuration_id=execution.configuration.configuration_id,
                model=execution.model.model,
                model_parameters={},
                status="patch_submitted",
                started_at=now,
                finished_at=now,
                latency_ms=1,
                input_tokens=2,
                output_tokens=3,
                estimated_cost=None,
                tool_calls=0,
                files_read=1,
                lines_exposed=10,
                repair_attempted=False,
                final_resolution=None,
                failure_category=None,
            )
        )
        self.session.flush()
        return object()


class PassingPostHocVerifier:
    def __init__(self, session: Session, calls: list[str]) -> None:
        self.session = session
        self.calls = calls

    def verify(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        attempt_number: int = 1,
        benchmark_root: str | Path | None = None,
    ) -> VerificationRun:
        del manifest_path, benchmark_root
        self.calls.append(run_id)
        run = self.session.get(Run, run_id)
        assert run is not None
        run.status = "verified_pass"
        run.final_resolution = True
        return VerificationRun(run_id, attempt_number, True, False, None, ())


def test_pilot_schema_is_strict_and_expands_to_twelve_stable_slots(tmp_path: Path) -> None:
    config = load_experiment_config(PILOT)
    runner = ExperimentRunner(
        _sessions(),  # type: ignore[arg-type]
        settings=Settings(state_dir=tmp_path / "state"),
        benchmark_root=BENCHMARK_ROOT,
        configuration_runner_factory=lambda session, slot: ConfigurationRunner(
            session, executors={}
        ),
    )

    first = runner.plan(config)
    second = runner.plan(config)

    assert len(first) == 12
    assert [slot.run_id for slot in first] == [slot.run_id for slot in second]
    assert len({slot.run_id for slot in first}) == 12


def test_cli_canonicalizes_a_safe_parent_relative_state_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)

    result = experiment_cli_main(
        [
            "--config",
            str(PILOT),
            "--benchmark-root",
            str(BENCHMARK_ROOT),
            "--state-dir",
            "../state",
            "--fake-known-correct",
            "--dry-run",
        ]
    )

    assert result == 0
    assert (tmp_path / "state" / "agenttrace.sqlite3").is_file()


def test_a_is_verified_post_hoc_exported_once_and_then_resumed(tmp_path: Path) -> None:
    sessions = _sessions()
    execution_calls: list[str] = []
    verification_calls: list[str] = []
    export_calls: list[str] = []

    def runner_factory(session: Session, slot: object) -> ConfigurationRunner:
        del slot
        return ConfigurationRunner(
            session,
            executors={"A": PatchSubmittedExecutor(session, execution_calls)},
        )

    def export_factory(session: Session, slot: object, run: Run) -> dict[str, object]:
        del session, slot
        export_calls.append(run.run_id)
        return {"schema": "complete-test-export-v1", "run_id": run.run_id}

    runner = ExperimentRunner(
        sessions,  # type: ignore[arg-type]
        settings=Settings(state_dir=tmp_path / "state"),
        benchmark_root=BENCHMARK_ROOT,
        configuration_runner_factory=runner_factory,
        verifier_factory=lambda session, slot: PassingPostHocVerifier(session, verification_calls),
        raw_export_factory=export_factory,
    )
    config = _single_config()

    first = runner.run(config).slots[0]
    second = runner.run(config).slots[0]

    assert first.status is SlotStatus.EXECUTED
    assert first.final_resolution is True
    assert first.raw_record is not None
    assert first.raw_sha256 is not None
    assert second.status is SlotStatus.SKIPPED_COMPLETED
    assert second.raw_record == first.raw_record
    assert second.raw_sha256 == first.raw_sha256
    assert len(execution_calls) == len(verification_calls) == len(export_calls) == 1
    with sessions() as session:  # type: ignore[operator]
        stored = session.get(Run, first.run_id)
        assert stored is not None
        identity = stored.model_parameters["experiment"]
        assert identity["experiment_id"] == "runner-test"
        assert identity["benchmark_version"] == "pilot-v1"
        assert len(identity["experiment_config_sha256"]) == 64
        task = session.get(Task, stored.task_id)
        assert task is not None
        assert task.property_profile == "boundary-empty-input"


def test_runner_distinguishes_infrastructure_failure_and_does_not_retry(tmp_path: Path) -> None:
    sessions = _sessions()
    calls: list[str] = []

    class BrokenExecutor:
        def execute(self, execution: ConfigurationExecution) -> object:
            calls.append(execution.run_id)
            raise RuntimeError("controlled fixture infrastructure failure")

    runner = ExperimentRunner(
        sessions,  # type: ignore[arg-type]
        settings=Settings(state_dir=tmp_path / "state"),
        benchmark_root=BENCHMARK_ROOT,
        configuration_runner_factory=lambda session, slot: ConfigurationRunner(
            session, executors={"A": BrokenExecutor()}
        ),
    )
    config = _single_config(experiment_id="infra-test")

    first = runner.run(config).slots[0]
    second = runner.run(config).slots[0]

    assert first.status is SlotStatus.INFRASTRUCTURE_FAILURE
    assert first.failure is not None
    assert first.failure.primary is FailureCategory.INFRASTRUCTURE_FAILURE
    assert second.status is SlotStatus.SKIPPED_INFRASTRUCTURE_FAILURE
    assert calls == [first.run_id]


def test_terminal_row_without_raw_export_is_integration_incomplete(tmp_path: Path) -> None:
    sessions = _sessions()
    config = _single_config(experiment_id="missing-export")
    runner = ExperimentRunner(
        sessions,  # type: ignore[arg-type]
        settings=Settings(state_dir=tmp_path / "state"),
        benchmark_root=BENCHMARK_ROOT,
        configuration_runner_factory=lambda session, slot: ConfigurationRunner(
            session, executors={}
        ),
    )
    slot = runner.plan(config)[0]
    now = datetime.now(UTC)
    with sessions.begin() as session:  # type: ignore[union-attr]
        session.add(
            Run(
                run_id=slot.run_id,
                task_id=slot.task_id,
                configuration_id="A",
                model="fake-model",
                model_parameters={},
                status="verified_pass",
                started_at=now,
                finished_at=now,
                latency_ms=1,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=None,
                tool_calls=0,
                files_read=0,
                lines_exposed=0,
                repair_attempted=False,
                final_resolution=True,
                failure_category=None,
            )
        )

    outcome = runner.run(config).slots[0]

    assert outcome.status is SlotStatus.BLOCKED_INCOMPLETE
    assert outcome.raw_record is None


def test_failure_classifier_uses_actual_gate_names_and_repair_precedence() -> None:
    sessions = _sessions()
    now = datetime.now(UTC)
    with sessions.begin() as session:  # type: ignore[union-attr]
        run = Run(
            run_id="classification-test",
            task_id="boundary-empty-input",
            configuration_id="C",
            model="fake-model",
            model_parameters={"repair_metrics": {"repair_induced_regression": True}},
            status="repair_failed",
            started_at=now,
            finished_at=now,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=None,
            tool_calls=0,
            files_read=0,
            lines_exposed=0,
            repair_attempted=True,
            final_resolution=False,
            failure_category=None,
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                VerificationResult(
                    run_id=run.run_id,
                    attempt_number=2,
                    gate="existing_tests",
                    required=True,
                    status="failed",
                    exit_code=1,
                    duration_ms=1,
                    baseline_difference={"regression": True},
                    summary="regression",
                    log_artifact=None,
                ),
                VerificationResult(
                    run_id=run.run_id,
                    attempt_number=2,
                    gate="hypothesis_properties",
                    required=True,
                    status="failed",
                    exit_code=1,
                    duration_ms=1,
                    baseline_difference=None,
                    summary="property failure",
                    log_artifact=None,
                ),
            ]
        )
        session.flush()

        classified = classify_run(session, run)

    assert classified.primary is FailureCategory.REPAIR_INTRODUCED_REGRESSION
    assert FailureCategory.REGRESSION in classified.secondary
    assert FailureCategory.PROPERTY_FAILURE in classified.secondary


def test_manual_annotations_and_raw_storage_remain_derived_and_immutable(tmp_path: Path) -> None:
    annotation = ManualFailureAnnotation(
        run_id="manual-run",
        annotator="reviewer-1",
        secondary=[FailureCategory.MISUNDERSTOOD_REQUIREMENT],
        evidence=["trace event 12 and task requirement 3"],
        note="Causal interpretation after terminal deterministic classification.",
    )
    layout = ExperimentDataLayout.create(tmp_path / "state", "manual-study")
    raw = layout.write_raw_once("manual-run", {"result": "raw"})
    derived = layout.write_derived(
        "manual-run",
        annotation.model_dump(mode="json"),
    )

    assert raw.parent.name == "raw"
    assert derived.parent.name == "derived"
    with pytest.raises(FileExistsError):
        layout.write_raw_once("manual-run", {"result": "rewritten"})
