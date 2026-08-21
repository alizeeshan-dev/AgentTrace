"""Deterministically assemble canonical trace rows from persisted run evidence."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.artifacts import ArtifactStore
from app.db.models import (
    BenchmarkQuality,
    Counterexample,
    FaultLocalizationResult,
    PatchArtifact,
    Run,
    TraceEvent,
    VerificationResult,
)

from .models import TraceOperation
from .recorder import TraceRecorder
from .redaction import TraceRedactor

_TRACE_VERSION = 2


class TraceAssemblyError(RuntimeError):
    """Persisted evidence cannot be assembled into a canonical trace."""


class CanonicalTraceAssembler:
    """Finalize one run's observable records into a stable ordered trace.

    Older phases recorded some events only in content-addressed model artifacts.
    Assembly is a one-time finalization step before raw export, not a derived
    analysis rewrite.  Absent evidence is represented as ``skipped``.
    """

    def __init__(
        self,
        session: Session,
        artifact_store: ArtifactStore,
        *,
        redactor: TraceRedactor | None = None,
    ) -> None:
        self.session = session
        self.artifacts = artifact_store
        self.redactor = redactor or TraceRedactor(max_text_characters=2_000)

    def materialize(self, run_id: str) -> tuple[TraceEvent, ...]:
        run = self.session.get(Run, run_id)
        if run is None:
            raise TraceAssemblyError(f"run does not exist: {run_id}")
        if run.model_parameters.get("canonical_trace_version") == _TRACE_VERSION:
            return self._stored(run_id)

        existing = self._stored(run_id)
        legacy_operations = [event.operation for event in existing]
        patches = tuple(
            self.session.scalars(
                select(PatchArtifact)
                .where(PatchArtifact.run_id == run_id)
                .order_by(PatchArtifact.attempt_number)
            )
        )
        gates = tuple(
            self.session.scalars(
                select(VerificationResult)
                .where(VerificationResult.run_id == run_id)
                .order_by(VerificationResult.attempt_number, VerificationResult.gate)
            )
        )
        counterexamples = tuple(
            self.session.scalars(
                select(Counterexample)
                .where(Counterexample.run_id == run_id)
                .order_by(Counterexample.attempt_number, Counterexample.counterexample_id)
            )
        )
        benchmark_quality = self.session.get(BenchmarkQuality, run.task_id)
        localizations = self._localizations(run)
        initial_events = self._model_events(run, "model")
        repair_events = self._model_events(run, "repair_model")

        legacy_trace_reference = self._archive_legacy_events(run, existing)
        self.session.execute(delete(TraceEvent).where(TraceEvent.run_id == run_id))
        self.session.flush()
        recorder = TraceRecorder(self.session, run_id, redactor=self.redactor)
        fixed_time = run.started_at

        recorder.record(
            TraceOperation.PREPARE_WORKSPACE,
            "completed",
            output={"base_commit_bound": True, "source": "persisted run"},
            started_at=fixed_time,
            finished_at=fixed_time,
        )
        baseline = [gate for gate in gates if gate.gate.startswith("baseline_")]
        qualification_baseline = (
            {
                "artifact_reference": benchmark_quality.qualification_artifact,
                "baseline_status": benchmark_quality.baseline_status,
                "source": "benchmark_qualification",
            }
            if not baseline and benchmark_quality is not None
            else None
        )
        recorder.record(
            TraceOperation.BASELINE_VERIFICATION,
            "completed" if baseline or qualification_baseline is not None else "skipped",
            output=(
                {"gates": [_gate_summary(gate) for gate in baseline]}
                if baseline
                else qualification_baseline
                or {"reason": "no persisted baseline verification evidence"}
            ),
            started_at=fixed_time,
            finished_at=fixed_time,
        )
        coverage_references = [
            localization.coverage_artifact
            for localization in localizations
            if localization.coverage_artifact
        ]
        recorder.record(
            TraceOperation.COVERAGE_COLLECTION,
            "completed" if coverage_references else "skipped",
            output=(
                {"artifact_references": coverage_references}
                if coverage_references
                else {"reason": "no persisted coverage evidence for this run"}
            ),
            started_at=fixed_time,
            finished_at=fixed_time,
        )
        recorder.record(
            TraceOperation.FAULT_LOCALIZATION,
            "completed" if localizations else "skipped",
            output=(
                {
                    "results": [
                        {
                            "metric": item.metric,
                            "source_run_id": item.run_id,
                            "top_k": item.top_k,
                        }
                        for item in localizations
                    ]
                }
                if localizations
                else {"reason": "configuration produced no fault-localization evidence"}
            ),
            started_at=fixed_time,
            finished_at=fixed_time,
        )

        self._record_model_events(recorder, initial_events, fixed_time)
        if not initial_events:
            recorder.record(
                TraceOperation.MODEL_INFERENCE,
                "unavailable",
                output={"reason": "no readable persisted model-event artifact"},
                started_at=fixed_time,
                finished_at=fixed_time,
            )
        first_patch = next((item for item in patches if item.attempt_number == 1), None)
        self._record_patch(recorder, first_patch, 1, fixed_time)
        self._record_verification(recorder, gates, 1, fixed_time)
        first_counterexamples = [item for item in counterexamples if item.attempt_number == 1]
        self._record_counterexamples(recorder, first_counterexamples, fixed_time)

        second_patch = next((item for item in patches if item.attempt_number == 2), None)
        recorder.record(
            TraceOperation.REPAIR_ATTEMPT,
            "completed" if run.repair_attempted else "skipped",
            output=(
                {
                    "replacement_patch_present": second_patch is not None,
                    "workspace_reset_to_base": second_patch is not None,
                }
                if run.repair_attempted
                else {"reason": "repair was not attempted"}
            ),
            started_at=fixed_time,
            finished_at=fixed_time,
        )
        self._record_model_events(recorder, repair_events, fixed_time)
        if run.repair_attempted and not repair_events:
            recorder.record(
                TraceOperation.MODEL_INFERENCE,
                "unavailable",
                output={"reason": "no readable persisted repair model-event artifact"},
                started_at=fixed_time,
                finished_at=fixed_time,
            )
        if run.repair_attempted or second_patch is not None:
            self._record_patch(recorder, second_patch, 2, fixed_time)
            self._record_verification(recorder, gates, 2, fixed_time)

        recorder.record(
            TraceOperation.FINAL_RESULT,
            run.status,
            output={
                "failure_category": run.failure_category,
                "final_resolution": run.final_resolution,
                "legacy_trace_artifact": legacy_trace_reference,
                "legacy_operations_preserved_as_evidence": legacy_operations,
                "repair_attempted": run.repair_attempted,
            },
            started_at=run.finished_at or fixed_time,
            finished_at=run.finished_at or fixed_time,
        )
        parameters = dict(run.model_parameters)
        parameters["canonical_trace_version"] = _TRACE_VERSION
        run.model_parameters = parameters
        self.session.flush()
        return self._stored(run_id)

    def _archive_legacy_events(
        self, run: Run, events: tuple[TraceEvent, ...]
    ) -> str | None:
        if not events:
            return None
        payload = {
            "data_kind": "legacy_trace_raw_evidence",
            "events": [
                {
                    "error_type": (
                        self.redactor.redact_text(item.error_type) if item.error_type else None
                    ),
                    "event_id": item.event_id,
                    "finished_at": (
                        item.finished_at.isoformat() if item.finished_at is not None else None
                    ),
                    "input_summary": (
                        self.redactor.redact_text(item.input_summary)
                        if item.input_summary
                        else None
                    ),
                    "operation": item.operation,
                    "output_summary": (
                        self.redactor.redact_text(item.output_summary)
                        if item.output_summary
                        else None
                    ),
                    "parent_event_id": item.parent_event_id,
                    "run_id": item.run_id,
                    "sequence_number": item.sequence_number,
                    "started_at": item.started_at.isoformat(),
                    "status": self.redactor.redact_text(item.status),
                }
                for item in events
            ],
            "schema_version": 1,
        }
        reference = self.artifacts.store_text(
            run_id=run.run_id,
            kind="other",
            text=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            suffix=".json",
        )
        parameters = dict(run.model_parameters)
        raw_references = parameters.get("artifact_references")
        references = dict(raw_references) if isinstance(raw_references, dict) else {}
        references["legacy_trace"] = reference.relative_path
        parameters["artifact_references"] = references
        run.model_parameters = parameters
        self.session.flush()
        return reference.relative_path

    def _stored(self, run_id: str) -> tuple[TraceEvent, ...]:
        return tuple(
            self.session.scalars(
                select(TraceEvent)
                .where(TraceEvent.run_id == run_id)
                .order_by(TraceEvent.sequence_number)
            )
        )

    def _localizations(self, run: Run) -> tuple[FaultLocalizationResult, ...]:
        run_ids = {run.run_id}
        evidence = run.model_parameters.get("sbfl_evidence")
        if isinstance(evidence, dict):
            source = evidence.get("source_run_id")
            if isinstance(source, str):
                run_ids.add(source)
        return tuple(
            self.session.scalars(
                select(FaultLocalizationResult)
                .where(FaultLocalizationResult.run_id.in_(sorted(run_ids)))
                .order_by(FaultLocalizationResult.run_id)
            )
        )

    def _model_events(self, run: Run, name: str) -> tuple[dict[str, Any], ...]:
        references = run.model_parameters.get("artifact_references")
        if not isinstance(references, dict):
            return ()
        reference = references.get(name)
        if not isinstance(reference, str):
            return ()
        try:
            payload = json.loads(self.artifacts.read_bytes(reference))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return ()
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return ()
        return tuple(item for item in events if isinstance(item, dict))

    @staticmethod
    def _record_model_events(
        recorder: TraceRecorder,
        events: Iterable[dict[str, Any]],
        fixed_time: Any,
    ) -> None:
        for event in events:
            if "result" in event:
                action = event.get("action")
                tool = action.get("tool") if isinstance(action, dict) else None
                result = event.get("result")
                ok = result.get("ok") if isinstance(result, dict) else None
                recorder.record(
                    TraceOperation.TOOL_EXECUTION,
                    "completed" if ok is True else "error",
                    input={
                        "tool": tool,
                        "arguments": (
                            action.get("arguments") if isinstance(action, dict) else None
                        ),
                    },
                    output=result if isinstance(result, dict) else {"result": result},
                    started_at=fixed_time,
                    finished_at=fixed_time,
                )
            elif "action" in event:
                action = event.get("action")
                recorder.record(
                    TraceOperation.MODEL_INFERENCE,
                    "completed",
                    input={"model_parameters": event.get("model_parameters")},
                    output={
                        "action": action,
                        "finish_reason": event.get("finish_reason"),
                        "latency_ms": event.get("latency_ms"),
                        "model": event.get("model_identifier"),
                        "provider_request_id": event.get("provider_request_id"),
                        "usage": event.get("usage"),
                    },
                    started_at=fixed_time,
                    finished_at=fixed_time,
                )
            elif "provider_error" in event:
                recorder.record(
                    TraceOperation.MODEL_INFERENCE,
                    "error",
                    output={"provider_error": event.get("provider_error")},
                    error_type="MODEL_PROVIDER_FAILURE",
                    started_at=fixed_time,
                    finished_at=fixed_time,
                )

    @staticmethod
    def _record_patch(
        recorder: TraceRecorder,
        patch: PatchArtifact | None,
        attempt: int,
        fixed_time: Any,
    ) -> None:
        recorder.record(
            TraceOperation.PATCH_SUBMISSION,
            "completed" if patch is not None else "skipped",
            output=(
                {
                    "applied_successfully": patch.applied_successfully,
                    "attempt_number": attempt,
                    "files_changed": patch.files_changed,
                    "lines_added": patch.lines_added,
                    "lines_removed": patch.lines_removed,
                }
                if patch is not None
                else {"attempt_number": attempt, "reason": "no candidate patch was persisted"}
            ),
            started_at=fixed_time,
            finished_at=fixed_time,
        )

    @staticmethod
    def _record_verification(
        recorder: TraceRecorder,
        gates: tuple[VerificationResult, ...],
        attempt: int,
        fixed_time: Any,
    ) -> None:
        selected = [
            gate
            for gate in gates
            if gate.attempt_number == attempt and not gate.gate.startswith("baseline_")
        ]
        if not selected:
            recorder.record(
                TraceOperation.VERIFICATION_GATE,
                "skipped",
                output={
                    "attempt_number": attempt,
                    "reason": "no persisted verification gates",
                },
                started_at=fixed_time,
                finished_at=fixed_time,
            )
            return
        for gate in selected:
            recorder.record(
                TraceOperation.VERIFICATION_GATE,
                gate.status,
                input={
                    "attempt_number": attempt,
                    "gate": gate.gate,
                    "required": gate.required,
                },
                output=_gate_summary(gate),
                started_at=fixed_time,
                finished_at=fixed_time,
            )

    @staticmethod
    def _record_counterexamples(
        recorder: TraceRecorder,
        counterexamples: Iterable[Counterexample],
        fixed_time: Any,
    ) -> None:
        selected = tuple(counterexamples)
        if not selected:
            recorder.record(
                TraceOperation.COUNTEREXAMPLE,
                "skipped",
                output={"reason": "no counterexample was extracted"},
                started_at=fixed_time,
                finished_at=fixed_time,
            )
            return
        for item in selected:
            recorder.record(
                TraceOperation.COUNTEREXAMPLE,
                "completed",
                output={
                    "counterexample_id": item.counterexample_id,
                    "gate": item.gate,
                    "is_new_vs_baseline": item.is_new_vs_baseline,
                    "source": item.source,
                },
                started_at=fixed_time,
                finished_at=fixed_time,
            )


def _gate_summary(gate: VerificationResult) -> dict[str, Any]:
    return {
        "artifact_reference": gate.log_artifact,
        "baseline_difference": gate.baseline_difference,
        "duration_ms": gate.duration_ms,
        "exit_code": gate.exit_code,
        "gate": gate.gate,
        "required": gate.required,
        "status": gate.status,
        "summary": gate.summary,
    }
