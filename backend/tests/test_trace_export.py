from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.artifacts import ArtifactStore
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import (
    BenchmarkQuality,
    Counterexample,
    FaultLocalizationResult,
    PatchArtifact,
    Repository,
    Run,
    Task,
    TraceEvent,
    VerificationResult,
)
from app.traces import CanonicalTraceAssembler, RunTraceExporter, TraceOperation, TraceRedactor


def _records(tmp_path: Path) -> tuple[object, ArtifactStore]:
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
    artifacts = ArtifactStore(tmp_path / "artifacts")
    model_artifact = artifacts.store_text(
        run_id="pilot-d",
        kind="model",
        text=json.dumps(
            {
                "events": [
                    {
                        "action": {
                            "action_type": "tool_call",
                            "arguments": {"path": "src/parser.py"},
                            "tool": "read_file",
                        },
                        "finish_reason": "tool_call",
                        "latency_ms": 3,
                        "model_identifier": "fake-v1",
                        "model_parameters": {"api_key": "should-not-export"},
                        "provider_request_id": "request-1",
                        "usage": {"input_tokens": 4, "output_tokens": 2},
                    },
                    {
                        "action": {
                            "arguments": {"path": "src/parser.py"},
                            "tool": "read_file",
                        },
                        "result": {
                            "content": "hidden_tests/test_private.py contains secret behavior",
                            "ok": True,
                            "paths": ["src/parser.py"],
                            "tool": "read_file",
                        },
                    },
                ]
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        suffix=".json",
    )
    patch_text = (
        "diff --git a/src/parser.py b/src/parser.py\n"
        "--- a/src/parser.py\n"
        "+++ b/src/parser.py\n"
        "@@ -1 +1 @@\n"
        "-API_KEY='old'\n"
        "+API_KEY='sk-abcdefghijklmnop'\n"
    )
    patch_artifact = artifacts.store_text(
        run_id="pilot-d", kind="patches", text=patch_text, suffix=".patch"
    )
    coverage_artifact = artifacts.store_text(
        run_id="localization-pilot", kind="coverage", text="{}", suffix=".json"
    )
    verification_artifact = artifacts.store_text(
        run_id="pilot-d", kind="verification", text="{}", suffix=".json"
    )
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    with sessions.begin() as session:
        session.add(
            Repository(
                repository_id="repo-pilot",
                name="pilot",
                source="D:/private/source/repository",
                base_commit="a" * 40,
                python_version="3.12",
                test_command="pytest -q",
            )
        )
        session.add(
            Task(
                task_id="task-pilot",
                repository_id="repo-pilot",
                title="Fix parser",
                description="Handle empty input.",
                task_category="bug_fix",
                difficulty="easy",
                allowed_paths=["src/"],
                forbidden_paths=["hidden_tests/"],
                visible_test_command="pytest -q tests",
                hidden_test_command="pytest -q hidden_tests",
                known_correct_patch="benchmark/patches/correct.patch",
            )
        )
        session.flush()
        session.add(
            BenchmarkQuality(
                task_id="task-pilot",
                baseline_status="reproduced",
                mutation_score=0.75,
                mutants_generated=4,
                mutants_killed=3,
                mutants_survived=1,
                mutation_completed=True,
                execution_metadata={"authorization": "Bearer not-safe"},
            )
        )
        session.add(
            Run(
                run_id="localization-pilot",
                task_id="task-pilot",
                configuration_id="sbfl-only",
                model="coverage.py",
                model_parameters={},
                status="localized",
                started_at=now,
                finished_at=now,
            )
        )
        session.add(
            Run(
                run_id="pilot-d",
                task_id="task-pilot",
                configuration_id="D",
                model="fake-v1",
                model_parameters={
                    "artifact_references": {
                        "model": model_artifact.relative_path,
                        "patch": patch_artifact.relative_path,
                    },
                    "experiment_contract": {
                        "benchmark_version": "pilot-v1",
                        "temperature": 0,
                    },
                    "sbfl_evidence": {"source_run_id": "localization-pilot"},
                },
                status="repair_failed",
                started_at=now,
                finished_at=now,
                latency_ms=20,
                input_tokens=4,
                output_tokens=2,
                tool_calls=1,
                files_read=1,
                lines_exposed=10,
                repair_attempted=False,
                final_resolution=False,
                failure_category="HIDDEN_TEST_FAILURE",
            )
        )
        session.flush()
        session.add(
            FaultLocalizationResult(
                run_id="localization-pilot",
                metric="ochiai",
                ranked_locations=[
                    {"file": "src/parser.py", "line": 1, "ochiai": 1.0, "rank": 1}
                ],
                top_k=1,
                fault_rank_if_known=1,
                coverage_artifact=coverage_artifact.relative_path,
            )
        )
        session.add(
            PatchArtifact(
                run_id="pilot-d",
                attempt_number=1,
                unified_diff=patch_text,
                files_changed=["src/parser.py"],
                lines_added=1,
                lines_removed=1,
                applied_successfully=True,
            )
        )
        session.add(
            VerificationResult(
                run_id="pilot-d",
                attempt_number=1,
                gate="baseline_existing_tests",
                required=True,
                status="passed",
                exit_code=0,
                duration_ms=2,
                summary="baseline passed",
                log_artifact=verification_artifact.relative_path,
            )
        )
        session.add(
            VerificationResult(
                run_id="pilot-d",
                attempt_number=1,
                gate="hidden_tests",
                required=True,
                status="failed",
                exit_code=1,
                duration_ms=5,
                baseline_difference={"new_failures": 1},
                summary="hidden_tests/test_private.py failed",
                log_artifact=verification_artifact.relative_path,
            )
        )
        session.add(
            Counterexample(
                counterexample_id="cx-pilot",
                run_id="pilot-d",
                attempt_number=1,
                source="HIDDEN_TEST_FAILURE",
                gate="hidden_tests",
                input_summary=None,
                expected_summary="private expected value",
                observed_summary="hidden_tests/test_private.py returned the wrong value",
                failure_type="AssertionError",
                location_hints=["src/parser.py:1"],
                is_new_vs_baseline=True,
                log_excerpt="Authorization: Bearer abcdefghijklmnop",
                sanitized_feedback="safe symptom",
            )
        )
        session.add(
            TraceEvent(
                event_id="legacy-event",
                run_id="pilot-d",
                sequence_number=0,
                operation="p0_generation",
                started_at=now,
                finished_at=now,
                status="generated",
                input_summary='{"candidate":"P0"}',
                output_summary='{"patch_present":true}',
            )
        )
    return sessions, artifacts


def test_canonical_trace_assembly_is_ordered_and_idempotent(tmp_path) -> None:
    sessions, artifacts = _records(tmp_path)
    with sessions.begin() as session:  # type: ignore[attr-defined]
        assembler = CanonicalTraceAssembler(session, artifacts)
        first = assembler.materialize("pilot-d")
        second = assembler.materialize("pilot-d")

        assert [item.sequence_number for item in first] == list(range(len(first)))
        assert [item.event_id for item in second] == [item.event_id for item in first]
        operations = [item.operation for item in first]
        assert operations[0] == TraceOperation.PREPARE_WORKSPACE.value
        assert TraceOperation.BASELINE_VERIFICATION.value in operations
        assert TraceOperation.COVERAGE_COLLECTION.value in operations
        assert TraceOperation.FAULT_LOCALIZATION.value in operations
        assert TraceOperation.MODEL_INFERENCE.value in operations
        assert TraceOperation.TOOL_EXECUTION.value in operations
        assert TraceOperation.PATCH_SUBMISSION.value in operations
        assert TraceOperation.VERIFICATION_GATE.value in operations
        assert TraceOperation.COUNTEREXAMPLE.value in operations
        assert TraceOperation.REPAIR_ATTEMPT.value in operations
        assert operations[-1] == TraceOperation.FINAL_RESULT.value

        run = session.get(Run, "pilot-d")
        assert run is not None
        legacy_reference = run.model_parameters["artifact_references"]["legacy_trace"]
        legacy_payload = json.loads(artifacts.read_bytes(legacy_reference))
        assert legacy_payload["data_kind"] == "legacy_trace_raw_evidence"
        assert legacy_payload["events"] == [
            {
                "error_type": None,
                "event_id": "legacy-event",
                "finished_at": "2026-08-21T12:00:00+00:00",
                "input_summary": '{"candidate":"P0"}',
                "operation": "p0_generation",
                "output_summary": '{"patch_present":true}',
                "parent_event_id": None,
                "run_id": "pilot-d",
                "sequence_number": 0,
                "started_at": "2026-08-21T12:00:00+00:00",
                "status": "generated",
            }
        ]

        stored = tuple(
            session.scalars(
                select(TraceEvent)
                .where(TraceEvent.run_id == "pilot-d")
                .order_by(TraceEvent.sequence_number)
            )
        )
        assert len(stored) == len(first)


def test_json_export_is_deterministic_complete_redacted_and_hashed(tmp_path) -> None:
    sessions, artifacts = _records(tmp_path)
    with sessions.begin() as session:  # type: ignore[attr-defined]
        exporter = RunTraceExporter(session, artifacts)
        first = exporter.export_json("pilot-d")
        second = exporter.export_json("pilot-d")
        stored = exporter.store_export("pilot-d")

        assert first == second
        assert artifacts.read_bytes(stored) == first
        decoded = json.loads(first)
        rendered = first.decode("utf-8")
        assert decoded["data_kind"] == "raw_run"
        assert decoded["derived_data"]["included"] is False
        assert decoded["run"]["model_parameters"]["experiment_contract"][
            "benchmark_version"
        ] == "pilot-v1"
        assert decoded["run"]["input_tokens"] == 4
        assert decoded["benchmark_quality"]["mutation_score"] == 0.75
        assert decoded["fault_localization"][0]["metric"] == "ochiai"
        assert len(decoded["patches"]) == 1
        assert len(decoded["verification_results"]) == 2
        assert len(decoded["counterexamples"]) == 1
        assert decoded["trace_events"][-1]["operation"] == "workflow.final_result"
        assert all(item["sha256"] for item in decoded["artifacts"] if item["available"])
        legacy_reference = decoded["run"]["model_parameters"]["artifact_references"][
            "legacy_trace"
        ]
        legacy = next(
            item for item in decoded["artifacts"] if item["relative_path"] == legacy_reference
        )
        assert legacy["available"] is True
        assert "should-not-export" not in rendered
        assert "sk-abcdefghijklmnop" not in rendered
        assert "D:/private/source/repository" not in rendered
        assert "test_private.py" not in rendered
        assert "Bearer abcdefghijklmnop" not in rendered
        assert "[REDACTED" in rendered


def test_redactor_bounds_oversized_output_with_stable_hash() -> None:
    redactor = TraceRedactor(max_text_characters=100)

    first = redactor.redact_text("x" * 500)
    second = redactor.redact_text("x" * 500)

    assert first == second
    assert len(first) <= 100
    assert "TRUNCATED length=500 sha256=" in first


def test_trace_uses_qualified_benchmark_baseline_when_run_has_no_baseline_gate(
    tmp_path: Path,
) -> None:
    sessions, artifacts = _records(tmp_path)
    with sessions.begin() as session:  # type: ignore[attr-defined]
        baseline = session.get(VerificationResult, ("pilot-d", 1, "baseline_existing_tests"))
        assert baseline is not None
        session.delete(baseline)
        session.flush()

        events = CanonicalTraceAssembler(session, artifacts).materialize("pilot-d")

    baseline_event = next(
        item for item in events if item.operation == TraceOperation.BASELINE_VERIFICATION.value
    )
    assert baseline_event.status == "completed"
    assert baseline_event.output_summary is not None
    payload = json.loads(baseline_event.output_summary)
    assert payload == {
        "artifact_reference": None,
        "baseline_status": "reproduced",
        "source": "benchmark_qualification",
    }
