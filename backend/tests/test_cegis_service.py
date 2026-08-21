from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.agent import (
    FakeModelProvider,
    ModelUsage,
    ReadFileArguments,
    SubmitPatchAction,
    ToolCallAction,
)
from app.benchmark import load_benchmark_task
from app.cegis.service import ConfigurationCService
from app.config import Settings
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import Counterexample, PatchArtifact, Repository, Run, Task, TraceEvent
from app.verification.service import NormalizedGate, VerificationRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"
MANIFEST = BENCHMARK_ROOT / "tasks" / "boundary-empty-input.yaml"


class ScriptedVerifier:
    def __init__(self, resolutions: list[bool | None], *, final_regression: bool = False) -> None:
        self.resolutions = resolutions
        self.final_regression = final_regression
        self.attempts: list[int] = []

    def verify(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        attempt_number: int = 1,
        benchmark_root: str | Path | None = None,
    ) -> VerificationRun:
        del manifest_path, benchmark_root
        self.attempts.append(attempt_number)
        resolution = self.resolutions.pop(0)
        if resolution is None:
            gate = NormalizedGate(
                "verification_environment", True, "error", None, 1, "Docker unavailable"
            )
        elif resolution:
            gate = NormalizedGate("hidden_tests", True, "passed", 0, 2, "passed")
        else:
            gate = NormalizedGate(
                "hidden_tests",
                True,
                "failed",
                1,
                2,
                "hidden correctness failure",
                {"baseline_status": "failed", "failed": 1},
            )
        return VerificationRun(
            run_id,
            attempt_number,
            resolution,
            self.final_regression and attempt_number == 2,
            None,
            (gate,),
        )


def _setup(tmp_path: Path) -> tuple[Settings, object, object]:
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
                known_correct_patch="qualified",
            )
        )
    return Settings(state_dir=tmp_path / "state"), engine, sessions


def _patch(name: str) -> str:
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    path = (
        loaded.known_correct_patch_path
        if name == "correct"
        else BENCHMARK_ROOT / "verification_patches" / f"boundary-{name}.patch"
    )
    return path.read_text(encoding="utf-8")


def _action(name: str) -> SubmitPatchAction:
    return SubmitPatchAction(unified_diff=_patch(name), rationale=f"Fixture candidate: {name}.")


def test_p0_success_stops_without_counterexample_or_repair(tmp_path: Path) -> None:
    settings, engine, sessions = _setup(tmp_path)
    provider = FakeModelProvider([_action("correct")])
    verifier = ScriptedVerifier([True])
    with sessions.begin() as session:
        result = ConfigurationCService(
            session, settings=settings, provider=provider, verifier=verifier
        ).run(
            MANIFEST,
            run_id="cegis-p0-pass",
            model_identifier="fake-model",
            benchmark_root=BENCHMARK_ROOT,
        )
        assert result.metrics.repair_attempted is False
        assert result.metrics.final_resolution is True
        assert session.get(Run, "cegis-p0-pass").configuration_id == "C"  # type: ignore[union-attr]
        assert len(session.scalars(select(PatchArtifact)).all()) == 1
        assert session.scalar(select(Counterexample)) is None
    assert len(provider.requests) == 1
    assert verifier.attempts == [1]
    engine.dispose()


def test_p0_failure_then_p1_success_records_repair_metrics(tmp_path: Path) -> None:
    settings, engine, sessions = _setup(tmp_path)
    provider = FakeModelProvider(
        [
            _action("hidden-failure"),
            ToolCallAction(
                tool="read_file",
                arguments=ReadFileArguments(path="ministats/summary.py"),
            ),
            _action("correct"),
        ],
        usage_per_action=ModelUsage(input_tokens=5, output_tokens=3),
        latency_ms=4,
    )
    verifier = ScriptedVerifier([False, True])
    with sessions.begin() as session:
        result = ConfigurationCService(
            session, settings=settings, provider=provider, verifier=verifier
        ).run(
            MANIFEST,
            run_id="cegis-repair-pass",
            model_identifier="fake-model",
            benchmark_root=BENCHMARK_ROOT,
        )
        run = session.get(Run, "cegis-repair-pass")
        assert run is not None and run.final_resolution is True
        assert result.metrics.repair_success is True
        assert result.metrics.added_input_tokens == 10
        assert result.metrics.added_output_tokens == 6
        assert result.metrics.counterexample_source == "HIDDEN_TEST_FAILURE"
        assert run.model_parameters["repair_metrics"]["repair_success"] is True
        assert run.tool_calls == 1
    assert verifier.attempts == [1, 2]
    engine.dispose()


def test_p0_failure_then_p1_failure_stops(tmp_path: Path) -> None:
    settings, engine, sessions = _setup(tmp_path)
    provider = FakeModelProvider([_action("hidden-failure"), _action("property-edge")])
    verifier = ScriptedVerifier([False, False], final_regression=True)
    with sessions.begin() as session:
        result = ConfigurationCService(
            session, settings=settings, provider=provider, verifier=verifier
        ).run(
            MANIFEST,
            run_id="cegis-repair-fail",
            model_identifier="fake-model",
            benchmark_root=BENCHMARK_ROOT,
        )
        run = session.get(Run, "cegis-repair-fail")
        assert run is not None and run.status == "repair_failed"
        assert run.failure_category == "REPAIR_INTRODUCED_REGRESSION"
        assert result.metrics.repair_success is False
        assert result.metrics.repair_induced_regression is True
    assert len(provider.requests) == 2
    engine.dispose()


def test_infrastructure_failure_never_triggers_repair(tmp_path: Path) -> None:
    settings, engine, sessions = _setup(tmp_path)
    provider = FakeModelProvider([_action("hidden-failure"), _action("correct")])
    verifier = ScriptedVerifier([None])
    with sessions.begin() as session:
        result = ConfigurationCService(
            session, settings=settings, provider=provider, verifier=verifier
        ).run(
            MANIFEST,
            run_id="cegis-infra",
            model_identifier="fake-model",
            benchmark_root=BENCHMARK_ROOT,
        )
        assert result.counterexample is None
        assert result.metrics.repair_attempted is False
        assert session.scalar(select(Counterexample)) is None
    assert len(provider.requests) == 1
    engine.dispose()


def test_repair_count_cannot_exceed_one_even_when_p1_fails(tmp_path: Path) -> None:
    settings, engine, sessions = _setup(tmp_path)
    provider = FakeModelProvider(
        [_action("hidden-failure"), _action("property-edge"), _action("correct")]
    )
    with sessions.begin() as session:
        ConfigurationCService(
            session,
            settings=settings,
            provider=provider,
            verifier=ScriptedVerifier([False, False]),
        ).run(
            MANIFEST,
            run_id="cegis-one-repair",
            model_identifier="fake-model",
            benchmark_root=BENCHMARK_ROOT,
        )
        attempts = session.scalars(
            select(PatchArtifact).order_by(PatchArtifact.attempt_number)
        ).all()
        assert [item.attempt_number for item in attempts] == [1, 2]
    assert len(provider.requests) == 2
    engine.dispose()


def test_p1_is_validated_on_clean_base_and_trace_records_reset(tmp_path: Path) -> None:
    settings, engine, sessions = _setup(tmp_path)
    # The correct patch conflicts with P0's changed docstring if applied on top
    # of P0, but applies cleanly after reset to the recorded base commit.
    provider = FakeModelProvider([_action("hidden-failure"), _action("correct")])
    with sessions.begin() as session:
        ConfigurationCService(
            session,
            settings=settings,
            provider=provider,
            verifier=ScriptedVerifier([False, True]),
        ).run(
            MANIFEST,
            run_id="cegis-clean-base",
            model_identifier="fake-model",
            benchmark_root=BENCHMARK_ROOT,
        )
        p1 = session.get(PatchArtifact, ("cegis-clean-base", 2))
        assert p1 is not None and p1.applied_successfully is True
        events = session.scalars(
            select(TraceEvent)
            .where(TraceEvent.run_id == "cegis-clean-base")
            .order_by(TraceEvent.sequence_number)
        ).all()
        operations = [event.operation for event in events]
        assert operations == [
            "p0_generation",
            "p0_verification",
            "counterexample_creation",
            "repair_start",
            "p1_generation",
            "workspace_reset",
            "p1_verification",
            "final_state",
        ]
    assert not any(settings.effective_workspace_root.iterdir())
    engine.dispose()
