from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import (
    FakeModelProvider,
    ModelUsage,
    ReadFileArguments,
    SubmitPatchAction,
    ToolCallAction,
)
from app.agent.service import AgentRunService
from app.artifacts import ArtifactStore
from app.benchmark import load_benchmark_task
from app.cegis import ConfigurationCService
from app.config import Settings
from app.configurations import (
    ConfigurationExecution,
    ConfigurationRunner,
    ExperimentCondition,
)
from app.configurations.enhanced import ConfigurationDService
from app.db import create_database_engine, init_database, make_session_factory
from app.db.models import (
    FaultLocalizationResult,
    PatchArtifact,
    Repository,
    Run,
    Task,
    VerificationResult,
)
from app.experiments import ExperimentConfig, ExperimentRunner, ExperimentSlot, SlotStatus
from app.fault_localization import localization_run_id
from app.traces import RunTraceExporter, TraceOperation
from app.verification import NormalizedGate, VerificationRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"
MANIFEST = BENCHMARK_ROOT / "tasks" / "boundary-empty-input.yaml"


def _qualified_sessions() -> object:
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    assert loaded.repository_path is not None
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
    source_run_id = localization_run_id(loaded.task.task_id, loaded.task.base_commit)
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.add(
            Repository(
                repository_id="phase10-integration-repo",
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
                repository_id="phase10-integration-repo",
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
                known_correct_patch="qualified-fixture",
            )
        )
        session.flush()
        session.add(
            Run(
                run_id=source_run_id,
                task_id=loaded.task.task_id,
                configuration_id="sbfl-only",
                model="coverage.py",
                model_parameters={"collector": "deterministic-fixture"},
                status="localized",
                started_at=now,
                finished_at=now,
                latency_ms=1,
            )
        )
        session.flush()
        session.add(
            FaultLocalizationResult(
                run_id=source_run_id,
                metric="ochiai",
                ranked_locations=[
                    {
                        "rank": 1,
                        "file": "ministats/summary.py",
                        "line": 7,
                        "symbol": "mean",
                        "ochiai": 1.0,
                    }
                ],
                top_k=1,
                fault_rank_if_known=1,
                coverage_artifact=None,
            )
        )
    return sessions


def _patch(name: str) -> str:
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    if name == "correct":
        path = loaded.known_correct_patch_path
    else:
        path = BENCHMARK_ROOT / "verification_patches" / "boundary-hidden-failure.patch"
    return path.read_text(encoding="utf-8")


class PersistingVerifier:
    """Deterministic oracle seam; it does not execute repository code."""

    def __init__(self, session: Session, calls: list[tuple[str, int]]) -> None:
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
        self.calls.append((run_id, attempt_number))
        run = self.session.get(Run, run_id)
        assert run is not None
        repaired_condition = run.configuration_id in {"C", "D"}
        passed = not repaired_condition or attempt_number == 2
        gate = NormalizedGate(
            gate="hidden_tests",
            required=True,
            status="passed" if passed else "failed",
            exit_code=0 if passed else 1,
            duration_ms=2,
            summary=(
                "Evaluator-owned correctness checks passed."
                if passed
                else "One evaluator-owned correctness behavior failed."
            ),
            baseline_difference=None if passed else {"baseline_status": "failed", "failed": 1},
        )
        self.session.add(
            VerificationResult(
                run_id=run_id,
                attempt_number=attempt_number,
                gate=gate.gate,
                required=gate.required,
                status=gate.status,
                exit_code=gate.exit_code,
                duration_ms=gate.duration_ms,
                baseline_difference=gate.baseline_difference,
                summary=gate.summary,
                log_artifact=None,
            )
        )
        if not repaired_condition:
            run.status = "verified_pass"
            run.final_resolution = True
            run.failure_category = None
        self.session.flush()
        return VerificationRun(run_id, attempt_number, passed, False, None, (gate,))


@dataclass
class _Executor:
    session: Session
    settings: Settings
    provider: FakeModelProvider
    verifier_calls: list[tuple[str, int]]

    def execute(self, execution: ConfigurationExecution) -> object:
        condition = execution.configuration.condition
        if condition is ExperimentCondition.A:
            return AgentRunService(
                self.session, settings=self.settings, provider=self.provider
            ).run_direct(
                execution.manifest_path,
                run_id=execution.run_id,
                model_identifier=execution.model.model,
                model_parameters=execution.provider_parameters,
                budgets=execution.budgets,
                benchmark_root=execution.benchmark_root,
            )
        if condition is ExperimentCondition.B:
            return AgentRunService(
                self.session, settings=self.settings, provider=self.provider
            ).run_tool_agent(
                execution.manifest_path,
                run_id=execution.run_id,
                model_identifier=execution.model.model,
                model_parameters=execution.provider_parameters,
                budgets=execution.budgets,
                benchmark_root=execution.benchmark_root,
            )
        verifier = PersistingVerifier(self.session, self.verifier_calls)
        if condition is ExperimentCondition.C:
            return ConfigurationCService(
                self.session,
                settings=self.settings,
                provider=self.provider,
                verifier=verifier,
            ).run(
                execution.manifest_path,
                run_id=execution.run_id,
                model_identifier=execution.model.model,
                model_parameters=execution.provider_parameters,
                budgets=execution.budgets,
                benchmark_root=execution.benchmark_root,
            )
        return ConfigurationDService(
            self.session,
            settings=self.settings,
            provider=self.provider,
            verifier=verifier,
        ).run(
            execution.manifest_path,
            run_id=execution.run_id,
            model_identifier=execution.model.model,
            model_parameters=execution.provider_parameters,
            budgets=execution.budgets,
            benchmark_root=execution.benchmark_root,
            techniques=execution.research_techniques.effective,
            condition=cast(
                str, execution.configuration.condition.value
            ),  # runtime value is constrained by ExperimentalConfiguration
        )


def _config() -> ExperimentConfig:
    configurations = []
    for condition in ExperimentCondition:
        preset = {
            "D": {"enable_sbfl": True, "enable_hypothesis": True, "enable_crosshair": True},
            "D1": {"enable_sbfl": True, "enable_hypothesis": False, "enable_crosshair": False},
            "D2": {"enable_sbfl": False, "enable_hypothesis": True, "enable_crosshair": False},
            "D3": {"enable_sbfl": False, "enable_hypothesis": False, "enable_crosshair": True},
        }.get(
            condition.value,
            {"enable_sbfl": False, "enable_hypothesis": False, "enable_crosshair": False},
        )
        configurations.append({"condition": condition.value, "feature_flags": preset})
    return ExperimentConfig.model_validate(
        {
            "experiment_id": "phase10-seven-condition-integration",
            "benchmark_version": "integration-fixture-v1",
            "tasks": ["tasks/boundary-empty-input.yaml"],
            "configurations": configurations,
            "model": {
                "provider": "fake",
                "model": "fake-model",
                "model_version": "deterministic-integration-v1",
                "temperature": 0.0,
                "parameters": {"seed": 10},
            },
            "budgets": {
                "max_model_turns": 4,
                "max_tool_calls": 2,
                "max_files_read": 4,
                "max_files_exposed": 8,
                "max_content_characters": 20_000,
                "max_file_bytes": 100_000,
                "max_tree_entries": 100,
                "max_search_matches": 20,
                "max_search_result_characters": 4_000,
                "max_patch_bytes": 10_000,
                "max_patch_lines": 100,
                "max_changed_files": 2,
                "wall_clock_seconds": 30.0,
            },
            "max_repairs": 1,
            "verification_profile": "deterministic-integration-v1",
        }
    )


def _provider(condition: str) -> FakeModelProvider:
    usage = ModelUsage(input_tokens=5, output_tokens=3)
    correct = SubmitPatchAction(
        unified_diff=_patch("correct"), rationale="Apply the complete fixture repair."
    )
    if condition == "A":
        return FakeModelProvider([correct], usage_per_action=usage, latency_ms=2)
    if condition == "B":
        return FakeModelProvider(
            [
                ToolCallAction(
                    tool="read_file",
                    arguments=ReadFileArguments(path="ministats/summary.py"),
                ),
                correct,
            ],
            usage_per_action=usage,
            latency_ms=2,
        )
    return FakeModelProvider(
        [
            SubmitPatchAction(
                unified_diff=_patch("incorrect"),
                rationale="Initial candidate intentionally misses the empty-input behavior.",
            ),
            correct,
        ],
        usage_per_action=usage,
        latency_ms=2,
    )


def test_all_conditions_share_export_contract_and_preserve_cegis_invariants(
    tmp_path: Path,
) -> None:
    sessions = _qualified_sessions()
    settings = Settings(state_dir=tmp_path / "clean-state")
    calls: list[tuple[str, int]] = []

    def runner_factory(session: Session, slot: ExperimentSlot) -> ConfigurationRunner:
        condition = slot.condition
        executor = _Executor(session, settings, _provider(condition), calls)
        return ConfigurationRunner(
            session,
            executors={"A": executor, "B": executor, "C": executor, "D": executor},
        )

    artifacts = ArtifactStore(settings.effective_artifact_root)

    def export_factory(session: Session, slot: object, run: Run) -> dict[str, object]:
        del slot
        return RunTraceExporter(session, artifacts).build(run.run_id).model_dump(mode="json")

    runner = ExperimentRunner(
        sessions,  # type: ignore[arg-type]
        settings=settings,
        benchmark_root=BENCHMARK_ROOT,
        configuration_runner_factory=runner_factory,
        verifier_factory=lambda session, slot: PersistingVerifier(session, calls),
        raw_export_factory=export_factory,
    )
    config = _config()
    expected_base_commit = load_benchmark_task(
        MANIFEST, benchmark_root=BENCHMARK_ROOT
    ).task.base_commit

    first = runner.run(config)
    first_hashes = {slot.run_id: slot.raw_sha256 for slot in first.slots}
    second = runner.run(config)

    assert len(first.slots) == len(second.slots) == 7
    assert all(slot.status is SlotStatus.EXECUTED for slot in first.slots)
    assert all(slot.final_resolution is True for slot in first.slots)
    assert all(slot.status is SlotStatus.SKIPPED_COMPLETED for slot in second.slots)
    assert {slot.run_id: slot.raw_sha256 for slot in second.slots} == first_hashes

    raw_root = settings.state_dir / "experiments" / config.experiment_id / "raw"
    exports: dict[str, dict[str, object]] = {}
    run_key_sets: list[set[str]] = []
    for slot in first.slots:
        payload = json.loads((raw_root / f"{slot.run_id}.json").read_text(encoding="utf-8"))
        export = cast(dict[str, object], payload["run_export"])
        exports[slot.condition] = export
        run = cast(dict[str, object], export["run"])
        run_key_sets.append(set(run))
        contract = cast(dict[str, object], cast(dict[str, object], run["model_parameters"])[
            "experiment_contract"
        ])
        trace = cast(list[dict[str, object]], export["trace_events"])
        operations = [item["operation"] for item in trace]
        assert contract["condition"] == slot.condition
        assert contract["base_commit"] == expected_base_commit
        assert contract["verification_profile"] == config.verification_profile
        assert run["input_tokens"] > 0
        assert run["output_tokens"] > 0
        assert "estimated_cost" in run and run["estimated_cost"] is None
        assert operations[0] == TraceOperation.PREPARE_WORKSPACE.value
        assert operations[-1] == TraceOperation.FINAL_RESULT.value
        assert TraceOperation.PATCH_SUBMISSION.value in operations
        assert TraceOperation.VERIFICATION_GATE.value in operations
        inference = next(
            item for item in trace if item["operation"] == TraceOperation.MODEL_INFERENCE.value
        )
        inference_output = json.loads(cast(str, inference["output_summary"]))
        assert inference_output["provider_request_id"] == "fake-request-0001"
        assert inference_output["usage"] == {"input_tokens": 5, "output_tokens": 3}

    assert all(keys == run_key_sets[0] for keys in run_key_sets)

    assert len(cast(list[object], exports["A"]["patches"])) == 1
    assert len(cast(list[object], exports["B"]["patches"])) == 1
    assert len(cast(list[object], exports["A"]["verification_results"])) == 1
    assert len(cast(list[object], exports["B"]["verification_results"])) == 1

    for condition in ("C", "D", "D1", "D2", "D3"):
        export = exports[condition]
        patches = cast(list[dict[str, object]], export["patches"])
        gates = cast(list[dict[str, object]], export["verification_results"])
        counterexamples = cast(list[dict[str, object]], export["counterexamples"])
        assert [item["attempt_number"] for item in patches] == [1, 2]
        assert patches[0]["unified_diff"] == _patch("incorrect")
        assert patches[1]["unified_diff"] == _patch("correct")
        assert patches[1]["applied_successfully"] is True
        assert [item["attempt_number"] for item in gates] == [1, 2]
        assert len(counterexamples) == 1
        assert counterexamples[0]["source"] == "HIDDEN_TEST_FAILURE"
        assert cast(dict[str, object], export["run"])["repair_attempted"] is True

    assert len(cast(list[object], exports["D"]["fault_localization"])) == 1
    assert len(cast(list[object], exports["D1"]["fault_localization"])) == 1
    assert cast(list[object], exports["D2"]["fault_localization"]) == []
    assert cast(list[object], exports["D3"]["fault_localization"]) == []

    effective: dict[str, dict[str, object]] = {}
    for condition in ("D", "D1", "D2", "D3"):
        run = cast(dict[str, object], exports[condition]["run"])
        parameters = cast(dict[str, object], run["model_parameters"])
        contract = cast(dict[str, object], parameters["experiment_contract"])
        techniques = cast(dict[str, object], contract["research_techniques"])
        effective[condition] = cast(dict[str, object], techniques["effective"])
    assert effective["D"] == {
        "enable_sbfl": True,
        "enable_hypothesis": True,
        "enable_crosshair": False,
    }
    assert effective["D1"] == {
        "enable_sbfl": True,
        "enable_hypothesis": False,
        "enable_crosshair": False,
    }
    assert effective["D2"] == {
        "enable_sbfl": False,
        "enable_hypothesis": True,
        "enable_crosshair": False,
    }
    assert effective["D3"] == {
        "enable_sbfl": False,
        "enable_hypothesis": False,
        "enable_crosshair": False,
    }

    repaired_ids = {
        slot.run_id for slot in first.slots if slot.condition in {"C", "D", "D1", "D2", "D3"}
    }
    assert sorted(calls) == sorted(
        [(run_id, attempt) for run_id in repaired_ids for attempt in (1, 2)]
        + [
            (slot.run_id, 1)
            for slot in first.slots
            if slot.condition in {"A", "B"}
        ]
    )
    with sessions() as session:  # type: ignore[operator]
        for run_id in repaired_ids:
            attempts = session.scalars(
                select(PatchArtifact)
                .where(PatchArtifact.run_id == run_id)
                .order_by(PatchArtifact.attempt_number)
            ).all()
            assert [item.attempt_number for item in attempts] == [1, 2]
