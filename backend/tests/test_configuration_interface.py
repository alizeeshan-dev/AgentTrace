from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent import FakeModelProvider, SubmitPatchAction
from app.agent.budgets import AgentBudgets
from app.benchmark import load_benchmark_task
from app.config import Settings
from app.configurations import (
    ConfigurationExecution,
    ConfigurationExecutionError,
    ConfigurationRunner,
    ExperimentalConfiguration,
    ExperimentCondition,
    ModelConfiguration,
    ResearchTechniques,
    resolve_research_techniques,
)
from app.configurations.enhanced import ConfigurationDService
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import FaultLocalizationResult, Repository, Run, Task
from app.fault_localization import localization_run_id
from app.verification import NormalizedGate, VerificationRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"
MANIFEST = BENCHMARK_ROOT / "tasks" / "boundary-empty-input.yaml"


def _sessions() -> object:
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    assert loaded.repository_path is not None
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
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
                known_correct_patch="qualified-patch-artifact",
            )
        )
    return sessions


def _correct_patch() -> str:
    return load_benchmark_task(
        MANIFEST, benchmark_root=BENCHMARK_ROOT
    ).known_correct_patch_path.read_text(encoding="utf-8")


class PassingVerifier:
    def verify(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        attempt_number: int = 1,
        benchmark_root: str | Path | None = None,
    ) -> VerificationRun:
        return VerificationRun(run_id, attempt_number, True, False, None, ())


class RepairVerifier:
    def verify(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        attempt_number: int = 1,
        benchmark_root: str | Path | None = None,
    ) -> VerificationRun:
        if attempt_number == 1:
            failure = NormalizedGate(
                gate="visible_tests",
                required=True,
                status="failed",
                exit_code=1,
                duration_ms=1,
                summary="One visible behavior check failed.",
                baseline_difference={"failed": 1},
            )
            return VerificationRun(run_id, attempt_number, False, False, None, (failure,))
        return VerificationRun(run_id, attempt_number, True, False, None, ())


class RecordingExecutor:
    def __init__(self, session: object) -> None:
        self.session = session
        self.executions: list[ConfigurationExecution] = []

    def execute(self, execution: ConfigurationExecution) -> object:
        self.executions.append(execution)
        now = datetime.now(UTC)
        self.session.add(  # type: ignore[attr-defined]
            Run(
                run_id=execution.run_id,
                task_id=execution.loaded_task.task.task_id,
                configuration_id=execution.configuration.configuration_id,
                model=execution.model.model,
                model_parameters={},
                status="smoke-complete",
                started_at=now,
                finished_at=now,
                latency_ms=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=None,
                tool_calls=0,
                files_read=0,
                lines_exposed=0,
                repair_attempted=execution.configuration.repair_allowance == 1,
                final_resolution=None,
                failure_category=None,
            )
        )
        self.session.flush()  # type: ignore[attr-defined]
        return object()


@pytest.mark.parametrize("condition", list(ExperimentCondition))
def test_common_runner_dispatches_every_named_condition(condition: ExperimentCondition) -> None:
    sessions = _sessions()
    configuration = ExperimentalConfiguration.preset(condition)
    model = ModelConfiguration(
        provider="fake",
        model="fake-model",
        model_version="fixture-v1",
        temperature=0.2,
        parameters={"seed": 7},
    )
    with sessions.begin() as session:  # type: ignore[attr-defined]
        executor = RecordingExecutor(session)
        runner = ConfigurationRunner(
            session,
            executors={configuration.configuration_id: executor},
        )
        result = runner.run(
            MANIFEST,
            run_id=f"common-{condition.value.casefold()}",
            configuration=configuration,
            model=model,
            benchmark_root=BENCHMARK_ROOT,
        )

        assert result.configuration_id == configuration.configuration_id
        assert result.condition is condition
        assert result.status == "smoke-complete"
        assert len(executor.executions) == 1
        stored = session.get(Run, result.run_id)
        assert stored is not None
        contract = stored.model_parameters["experiment_contract"]
        assert contract["model_version"] == "fixture-v1"
        assert contract["task_description"] == executor.executions[0].loaded_task.task.description
        assert contract["base_commit"] == executor.executions[0].loaded_task.task.base_commit


def test_direct_condition_records_its_protocol_budget_ceiling() -> None:
    sessions = _sessions()
    with sessions.begin() as session:  # type: ignore[attr-defined]
        executor = RecordingExecutor(session)
        result = ConfigurationRunner(session, executors={"A": executor}).run(
            MANIFEST,
            run_id="common-direct-budget",
            configuration=ExperimentalConfiguration.preset("A"),
            model=ModelConfiguration(
                provider="fake",
                model="fake-model",
                model_version="fixture-v1",
                temperature=0.0,
            ),
            budgets=AgentBudgets(max_model_turns=8, max_tool_calls=6),
            benchmark_root=BENCHMARK_ROOT,
        )

    assert result.experiment_contract.declared_budgets.max_model_turns == 8
    assert result.experiment_contract.effective_budgets.max_model_turns == 1
    assert result.experiment_contract.effective_budgets.max_tool_calls == 0


def test_standard_runner_rejects_a_different_provider_identity(tmp_path: Path) -> None:
    sessions = _sessions()
    with sessions.begin() as session:  # type: ignore[attr-defined]
        runner = ConfigurationRunner.from_services(
            session,
            settings=Settings(state_dir=tmp_path / "provider-mismatch"),
            provider=FakeModelProvider([]),
        )
        with pytest.raises(ConfigurationExecutionError, match="provider identity"):
            runner.run(
                MANIFEST,
                run_id="provider-mismatch",
                configuration=ExperimentalConfiguration.preset("A"),
                model=ModelConfiguration(
                    provider="different-provider",
                    model="fake-model",
                    model_version="fixture-v1",
                    temperature=0.0,
                ),
                benchmark_root=BENCHMARK_ROOT,
            )


def test_optional_task_features_are_disabled_with_recorded_reasons() -> None:
    task = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT).task
    resolved = resolve_research_techniques(ExperimentalConfiguration.preset("D"), task)
    sbfl_only = resolve_research_techniques(ExperimentalConfiguration.preset("D1"), task)
    hypothesis_only = resolve_research_techniques(ExperimentalConfiguration.preset("D2"), task)
    crosshair_only = resolve_research_techniques(ExperimentalConfiguration.preset("D3"), task)

    assert resolved.effective.enable_sbfl
    assert resolved.effective.enable_hypothesis
    assert not resolved.effective.enable_crosshair
    assert resolved.disabled_reasons == {"crosshair": "task has no symbolic_profile"}
    assert sbfl_only.effective == ResearchTechniques(enable_sbfl=True)
    assert hypothesis_only.effective == ResearchTechniques(enable_hypothesis=True)
    assert not crosshair_only.effective.enable_crosshair
    assert crosshair_only.disabled_reasons == {"crosshair": "task has no symbolic_profile"}


def test_named_condition_rejects_an_inconsistent_ablation() -> None:
    with pytest.raises(ValidationError, match="research techniques"):
        ExperimentalConfiguration(
            condition=ExperimentCondition.D1,
            repair_allowance=1,
            techniques=ResearchTechniques(enable_hypothesis=True),
        )


@pytest.mark.parametrize(
    ("condition", "with_patch"),
    [
        (ExperimentCondition.A, True),
        (ExperimentCondition.B, True),
        (ExperimentCondition.C, False),
        (ExperimentCondition.D2, False),
    ],
)
def test_standard_factory_reaches_actual_configuration_services(
    tmp_path: Path,
    condition: ExperimentCondition,
    with_patch: bool,
) -> None:
    sessions = _sessions()
    steps = (
        [
            SubmitPatchAction(
                unified_diff=_correct_patch(),
                rationale="Apply the deterministic fixture repair.",
            )
        ]
        if with_patch
        else []
    )
    provider = FakeModelProvider(steps)
    with sessions.begin() as session:  # type: ignore[attr-defined]
        runner = ConfigurationRunner.from_services(
            session,
            settings=Settings(state_dir=tmp_path / condition.value.casefold()),
            provider=provider,
        )
        result = runner.run(
            MANIFEST,
            run_id=f"actual-{condition.value.casefold()}",
            configuration=ExperimentalConfiguration.preset(condition),
            model=ModelConfiguration(
                provider="fake",
                model="fake-model",
                model_version="fixture-v1",
                temperature=0.0,
            ),
            benchmark_root=BENCHMARK_ROOT,
        )

    assert result.configuration_id == condition.configuration_id
    assert result.status == ("patch_submitted" if with_patch else "provider_error")


def test_configuration_d_injects_persisted_probabilistic_sbfl_evidence(
    tmp_path: Path,
) -> None:
    sessions = _sessions()
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    sbfl_run_id = localization_run_id(loaded.task.task_id, loaded.task.base_commit)
    provider = FakeModelProvider(
        [
            SubmitPatchAction(
                unified_diff=_correct_patch(),
                rationale="Repair the localized empty-input boundary.",
            )
        ]
    )
    now = datetime.now(UTC)
    with sessions.begin() as session:  # type: ignore[attr-defined]
        session.add(
            Run(
                run_id=sbfl_run_id,
                task_id=loaded.task.task_id,
                configuration_id="sbfl-only",
                model="none",
                model_parameters={},
                status="localized",
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
                final_resolution=None,
                failure_category=None,
            )
        )
        session.flush()
        session.add(
            FaultLocalizationResult(
                run_id=sbfl_run_id,
                metric="ochiai",
                ranked_locations=[
                    {
                        "rank": 1,
                        "file": "ministats/summary.py",
                        "line": 5,
                        "symbol": "mean",
                        "ochiai": 1.0,
                    }
                ],
                top_k=1,
                fault_rank_if_known=1,
                coverage_artifact="sbfl/raw-coverage.json",
            )
        )
        session.flush()
        result = ConfigurationDService(
            session,
            settings=Settings(state_dir=tmp_path / "d-runtime"),
            provider=provider,
            verifier=PassingVerifier(),
        ).run(
            MANIFEST,
            run_id="actual-d-sbfl",
            model_identifier="fake-model",
            techniques=ResearchTechniques(enable_sbfl=True),
            condition="D1",
            benchmark_root=BENCHMARK_ROOT,
        )

        stored = session.get(Run, "actual-d-sbfl")
        assert stored is not None
        assert stored.configuration_id == "D"
        assert stored.model_parameters["condition"] == "D1"

    assert result.cegis.metrics.repair_attempted is False
    assert result.fault_localization is not None
    assert len(provider.requests) == 1
    evidence_message = provider.requests[0].messages[-1].content
    assert '"category":"FAULT LOCALIZATION EVIDENCE"' in evidence_message
    assert '"interpretation":"Probabilistic suspiciousness ranking only' in evidence_message
    assert "hidden_test_command" not in evidence_message


def test_configuration_d_labels_repair_evidence_and_stops_after_one_repair(
    tmp_path: Path,
) -> None:
    sessions = _sessions()
    action = SubmitPatchAction(
        unified_diff=_correct_patch(),
        rationale="Apply the complete replacement patch against the clean base.",
    )
    provider = FakeModelProvider([action, action])
    with sessions.begin() as session:  # type: ignore[attr-defined]
        result = ConfigurationDService(
            session,
            settings=Settings(state_dir=tmp_path / "d-repair-runtime"),
            provider=provider,
            verifier=RepairVerifier(),
        ).run(
            MANIFEST,
            run_id="actual-d-repair",
            model_identifier="fake-model",
            techniques=ResearchTechniques(enable_hypothesis=True),
            condition="D2",
            benchmark_root=BENCHMARK_ROOT,
        )

    assert result.cegis.metrics.repair_attempted is True
    assert result.cegis.metrics.repair_success is True
    assert len(provider.requests) == 2
    repair_payload = provider.requests[1].messages[1].content
    assert '"DETERMINISTIC VERIFICATION EVIDENCE"' in repair_payload
    assert '"ADVISORY WARNINGS"' in repair_payload
    assert '"included_in_repair_feedback":false' in repair_payload
