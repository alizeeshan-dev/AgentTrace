from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.cegis.counterexamples import CounterexampleExtractor
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import Counterexample as CounterexampleRecord
from app.db.models import Repository, Run, Task
from app.verification.service import NormalizedGate, VerificationRun


def _sessions():  # type: ignore[no-untyped-def]
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
    with sessions.begin() as session:
        session.add(
            Repository(
                repository_id="repo",
                name="fixture",
                source="fixture",
                base_commit="a" * 40,
                python_version="3.12",
                test_command="pytest -q",
            )
        )
        session.add(
            Task(
                task_id="task",
                repository_id="repo",
                title="fixture",
                description="fixture",
                task_category="bug_fix",
                difficulty="easy",
                allowed_paths=["src/"],
                forbidden_paths=["tests/"],
                visible_test_command="pytest -q",
                hidden_test_command="pytest -q {hidden_tests}",
            )
        )
        session.flush()
        session.add(
            Run(
                run_id="cegis-run",
                task_id="task",
                configuration_id="C",
                model="fixture",
                model_parameters={},
                status="verified_fail",
                started_at=datetime.now(UTC),
            )
        )
    return engine, sessions


def _verification(gate: NormalizedGate, *, resolved: bool | None = False) -> VerificationRun:
    return VerificationRun(
        run_id="cegis-run",
        attempt_number=1,
        resolved=resolved,
        regression=False,
        environment_kind=None,
        results=(gate,),
    )


def test_hypothesis_shrunk_input_is_preserved_and_persisted() -> None:
    engine, sessions = _sessions()
    gate = NormalizedGate(
        gate="hypothesis_properties",
        required=True,
        status="failed",
        exit_code=1,
        duration_ms=8,
        summary="Hypothesis found one shrunk counterexample.",
        baseline_difference={
            "baseline_status": "failed",
            "counterexamples": [
                {
                    "input_summary": "[0.0,1.0]",
                    "expected_summary": "0.5",
                    "observed_summary": "0.0",
                    "exception_type": "AssertionError",
                    "location_hints": ["src/summary.py:7"],
                    "shrunk": True,
                }
            ],
        },
    )
    with sessions.begin() as session:
        extractor = CounterexampleExtractor(session)
        counterexample = extractor.extract("cegis-run", 1, _verification(gate))
        assert counterexample is not None
        assert counterexample.source == "HYPOTHESIS_COUNTEREXAMPLE"
        assert counterexample.input_summary == "[0.0,1.0]"
        assert counterexample.expected_summary == "0.5"
        assert counterexample.observed_summary == "0.0"
        assert counterexample.failure_type == "AssertionError"
        assert counterexample.location_hints == ["src/summary.py:7"]
        feedback = json.loads(counterexample.sanitized_feedback)
        assert feedback["input_summary"] == "[0.0,1.0]"
        assert "replacement patch" in feedback["instruction"]

        # Re-extraction is idempotent and cannot create competing evidence.
        assert extractor.extract("cegis-run", 1, _verification(gate)) == counterexample
        assert len(session.scalars(select(CounterexampleRecord)).all()) == 1
    engine.dispose()


def test_hidden_failure_never_reveals_private_identifiers_or_logs() -> None:
    engine, sessions = _sessions()
    secret = "hidden_tests/test_secret_formula.py::test_internal_assertion"
    gate = NormalizedGate(
        gate="hidden_tests",
        required=True,
        status="failed",
        exit_code=1,
        duration_ms=4,
        summary=f"C:/evaluator/{secret} expected proprietary-value",
        baseline_difference={
            "baseline_status": "passed",
            "failed": 1,
            "new_failures": ["opaque-hidden-id-that-must-not-be-returned"],
            "counterexamples": [{"source": secret}],
        },
    )
    with sessions.begin() as session:
        counterexample = CounterexampleExtractor(session).extract(
            "cegis-run", 1, _verification(gate)
        )
        assert counterexample is not None
        assert counterexample.source == "HIDDEN_TEST_FAILURE"
        assert counterexample.input_summary is None
        assert counterexample.location_hints == []
        assert counterexample.log_excerpt is None
        assert counterexample.is_new_vs_baseline is True
        exposed = counterexample.model_dump_json()
        for private in (secret, "proprietary-value", "opaque-hidden-id"):
            assert private not in exposed
    engine.dispose()


def test_crosshair_concrete_evidence_is_supported_explicitly() -> None:
    engine, sessions = _sessions()
    gate = NormalizedGate(
        gate="symbolic",
        required=False,
        status="counterexample_found",
        exit_code=1,
        duration_ms=12,
        summary="CrossHair reported one potential contract counterexample.",
        baseline_difference={
            "conclusion": "counterexample",
            "counterexamples": [
                {
                    "location_hint": "src/contracts.py:12",
                    "observed_summary": "when x = -1, postcondition returned False",
                }
            ],
        },
    )
    with sessions.begin() as session:
        counterexample = CounterexampleExtractor(session).extract_gate("cegis-run", 1, gate)
        assert counterexample is not None
        assert counterexample.source == "CROSSHAIR_COUNTEREXAMPLE"
        assert counterexample.failure_type == "ContractViolation"
        assert counterexample.location_hints == ["src/contracts.py:12"]
        assert "x = -1" in counterexample.observed_summary
    engine.dispose()


def test_regression_identifies_public_new_failure_and_bounds_feedback() -> None:
    engine, sessions = _sessions()
    gate = NormalizedGate(
        gate="existing_tests",
        required=True,
        status="failed",
        exit_code=1,
        duration_ms=3,
        summary="x" * 10_000,
        baseline_difference={
            "baseline_status": "passed",
            "new_failures": ["tests.test_api::test_previously_working"],
        },
    )
    with sessions.begin() as session:
        counterexample = CounterexampleExtractor(session, max_feedback_chars=800).extract(
            "cegis-run", 1, _verification(gate)
        )
        assert counterexample is not None
        assert counterexample.source == "REGRESSION"
        assert counterexample.is_new_vs_baseline is True
        assert "test_previously_working" in counterexample.observed_summary
        assert len(counterexample.sanitized_feedback) <= 800
        json.loads(counterexample.sanitized_feedback)
    engine.dispose()


def test_infrastructure_failure_is_not_a_software_counterexample() -> None:
    engine, sessions = _sessions()
    gate = NormalizedGate(
        gate="verification_environment",
        required=True,
        status="error",
        exit_code=None,
        duration_ms=0,
        summary="Native verification environment unavailable",
    )
    with sessions.begin() as session:
        counterexample = CounterexampleExtractor(session).extract(
            "cegis-run", 1, _verification(gate, resolved=None)
        )
        assert counterexample is None
        assert session.scalar(select(CounterexampleRecord)) is None
    engine.dispose()
