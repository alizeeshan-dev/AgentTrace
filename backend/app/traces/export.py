"""Deterministic database-independent JSON export for one complete run."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts import ArtifactReference, ArtifactStore
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

from .assembler import CanonicalTraceAssembler
from .models import ArtifactDescriptor, CanonicalTraceEvent, RawRunExport, TraceOperation
from .redaction import TraceRedactor


class TraceExportError(RuntimeError):
    """A raw run export cannot be produced from the persisted records."""


class RunTraceExporter:
    """Export an immutable raw-run snapshot without requiring database access later."""

    def __init__(
        self,
        session: Session,
        artifact_store: ArtifactStore,
        *,
        redactor: TraceRedactor | None = None,
    ) -> None:
        self.session = session
        self.artifacts = artifact_store
        self.redactor = redactor or TraceRedactor()

    def build(self, run_id: str, *, materialize_trace: bool = True) -> RawRunExport:
        run = self.session.get(Run, run_id)
        if run is None:
            raise TraceExportError(f"run does not exist: {run_id}")
        task = self.session.get(Task, run.task_id)
        if task is None:
            raise TraceExportError("run task is missing")
        repository = self.session.get(Repository, task.repository_id)
        if repository is None:
            raise TraceExportError("task repository is missing")
        if materialize_trace:
            CanonicalTraceAssembler(
                self.session, self.artifacts, redactor=self.redactor
            ).materialize(run_id)

        quality = self.session.get(BenchmarkQuality, task.task_id)
        localization_records = self._localizations(run)
        patch_records = tuple(
            self.session.scalars(
                select(PatchArtifact)
                .where(PatchArtifact.run_id == run_id)
                .order_by(PatchArtifact.attempt_number)
            )
        )
        verification_records = tuple(
            self.session.scalars(
                select(VerificationResult)
                .where(VerificationResult.run_id == run_id)
                .order_by(VerificationResult.attempt_number, VerificationResult.gate)
            )
        )
        counterexample_records = tuple(
            self.session.scalars(
                select(Counterexample)
                .where(Counterexample.run_id == run_id)
                .order_by(Counterexample.attempt_number, Counterexample.counterexample_id)
            )
        )
        event_records = tuple(
            self.session.scalars(
                select(TraceEvent)
                .where(TraceEvent.run_id == run_id)
                .order_by(TraceEvent.sequence_number)
            )
        )
        references = self._artifact_references(
            run,
            quality,
            localization_records,
            verification_records,
        )
        descriptors = [self._artifact_descriptor(reference) for reference in sorted(references)]
        patch_reference_by_attempt = self._patch_references(run)

        raw = RawRunExport(
            run_id=run.run_id,
            run=self._redact_mapping(
                {
                    "configuration_id": run.configuration_id,
                    "estimated_cost": run.estimated_cost,
                    "failure_category": run.failure_category,
                    "files_read": run.files_read,
                    "final_resolution": run.final_resolution,
                    "finished_at": _timestamp(run.finished_at),
                    "input_tokens": run.input_tokens,
                    "latency_ms": run.latency_ms,
                    "lines_exposed": run.lines_exposed,
                    "model": run.model,
                    "model_parameters": run.model_parameters,
                    "output_tokens": run.output_tokens,
                    "repair_attempted": run.repair_attempted,
                    "run_id": run.run_id,
                    "started_at": _timestamp(run.started_at),
                    "status": run.status,
                    "task_id": run.task_id,
                    "tool_calls": run.tool_calls,
                }
            ),
            task=self._redact_mapping(
                {
                    "allowed_paths": task.allowed_paths,
                    "description": task.description,
                    "difficulty": task.difficulty,
                    "forbidden_paths": task.forbidden_paths,
                    "hidden_test_command": "[REDACTED:HIDDEN_TEST_COMMAND]",
                    "known_correct_patch_reference": task.known_correct_patch,
                    "property_profile": task.property_profile,
                    "repository_id": task.repository_id,
                    "symbolic_profile": task.symbolic_profile,
                    "task_category": task.task_category,
                    "task_id": task.task_id,
                    "title": task.title,
                    "visible_test_command": task.visible_test_command,
                }
            ),
            repository=self._redact_mapping(
                {
                    "base_commit": repository.base_commit,
                    "name": repository.name,
                    "python_version": repository.python_version,
                    "repository_id": repository.repository_id,
                    "source_fingerprint": hashlib.sha256(
                        repository.source.encode("utf-8", errors="replace")
                    ).hexdigest(),
                    "test_command": repository.test_command,
                }
            ),
            benchmark_quality=(
                self._redact_mapping(_quality_payload(quality)) if quality is not None else None
            ),
            fault_localization=[
                self._redact_mapping(
                    {
                        "coverage_artifact": item.coverage_artifact,
                        "fault_rank_if_known": item.fault_rank_if_known,
                        "metric": item.metric,
                        "ranked_locations": item.ranked_locations,
                        "source_run_id": item.run_id,
                        "top_k": item.top_k,
                    }
                )
                for item in localization_records
            ],
            patches=[
                self._redact_mapping(
                    {
                        "applied_successfully": item.applied_successfully,
                        "artifact_reference": patch_reference_by_attempt.get(item.attempt_number),
                        "attempt_number": item.attempt_number,
                        "files_changed": item.files_changed,
                        "lines_added": item.lines_added,
                        "lines_removed": item.lines_removed,
                        "unified_diff": item.unified_diff,
                    }
                )
                for item in patch_records
            ],
            verification_results=[
                self._redact_mapping(_verification_payload(item))
                for item in verification_records
            ],
            counterexamples=[
                self._redact_mapping(_counterexample_payload(item))
                for item in counterexample_records
            ],
            trace_events=[self._event_payload(item) for item in event_records],
            artifacts=descriptors,
        )
        return raw

    def export_json(self, run_id: str, *, materialize_trace: bool = True) -> bytes:
        payload = self.build(run_id, materialize_trace=materialize_trace).model_dump(mode="json")
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    def store_export(self, run_id: str, *, materialize_trace: bool = True) -> ArtifactReference:
        return self.artifacts.store_bytes(
            run_id=run_id,
            kind="other",
            data=self.export_json(run_id, materialize_trace=materialize_trace),
            suffix=".json",
        )

    def _redact_mapping(self, value: dict[str, Any]) -> dict[str, JsonValue]:
        redacted = self.redactor.redact(value)
        if not isinstance(redacted, dict):
            raise TraceExportError("redaction changed an object into a scalar")
        return redacted

    def _event_payload(self, item: TraceEvent) -> CanonicalTraceEvent:
        try:
            operation = TraceOperation(item.operation)
        except ValueError as error:
            raise TraceExportError("trace contains a non-canonical operation") from error
        return CanonicalTraceEvent(
            event_id=item.event_id,
            sequence_number=item.sequence_number,
            parent_event_id=item.parent_event_id,
            operation=operation,
            started_at=item.started_at,
            finished_at=item.finished_at,
            status=self.redactor.redact_text(item.status),
            input_summary=(
                self.redactor.redact_text(item.input_summary) if item.input_summary else None
            ),
            output_summary=(
                self.redactor.redact_text(item.output_summary) if item.output_summary else None
            ),
            error_type=(self.redactor.redact_text(item.error_type) if item.error_type else None),
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

    @staticmethod
    def _patch_references(run: Run) -> dict[int, str]:
        references = run.model_parameters.get("artifact_references")
        if not isinstance(references, dict):
            return {}
        result: dict[int, str] = {}
        if isinstance(references.get("patch"), str):
            result[1] = references["patch"]
        if isinstance(references.get("repair_patch"), str):
            result[2] = references["repair_patch"]
        return result

    @staticmethod
    def _artifact_references(
        run: Run,
        quality: BenchmarkQuality | None,
        localizations: tuple[FaultLocalizationResult, ...],
        verifications: tuple[VerificationResult, ...],
    ) -> set[str]:
        result: set[str] = set()
        references = run.model_parameters.get("artifact_references")
        if isinstance(references, dict):
            result.update(value for value in references.values() if isinstance(value, str))
        result.update(
            item.coverage_artifact for item in localizations if item.coverage_artifact is not None
        )
        result.update(item.log_artifact for item in verifications if item.log_artifact is not None)
        if quality is not None:
            if quality.mutation_artifact:
                result.add(quality.mutation_artifact)
            if quality.qualification_artifact:
                result.add(quality.qualification_artifact)
        return result

    def _artifact_descriptor(self, reference: str) -> ArtifactDescriptor:
        parts = PurePosixPath(reference).parts
        kind = parts[1] if len(parts) > 1 else "unknown"
        try:
            data = self.artifacts.read_bytes(reference)
        except (OSError, ValueError):
            return ArtifactDescriptor(
                relative_path=reference,
                sha256=None,
                size_bytes=None,
                kind=kind,
                available=False,
            )
        return ArtifactDescriptor(
            relative_path=reference,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            kind=kind,
            available=True,
        )


def _timestamp(value: Any) -> JsonValue:
    return value.isoformat() if value is not None else None


def _quality_payload(item: BenchmarkQuality) -> dict[str, Any]:
    return {
        "baseline_status": item.baseline_status,
        "execution_metadata": item.execution_metadata,
        "mutants_excluded": item.mutants_excluded,
        "mutants_generated": item.mutants_generated,
        "mutants_invalid": item.mutants_invalid,
        "mutants_killed": item.mutants_killed,
        "mutants_skipped": item.mutants_skipped,
        "mutants_survived": item.mutants_survived,
        "mutants_unusable": item.mutants_unusable,
        "mutation_artifact": item.mutation_artifact,
        "mutation_completed": item.mutation_completed,
        "mutation_duration_ms": item.mutation_duration_ms,
        "mutation_score": item.mutation_score,
        "mutation_tool": item.mutation_tool,
        "mutation_tool_version": item.mutation_tool_version,
        "qualification_artifact": item.qualification_artifact,
        "quality_notes": item.quality_notes,
        "task_id": item.task_id,
    }


def _verification_payload(item: VerificationResult) -> dict[str, Any]:
    return {
        "attempt_number": item.attempt_number,
        "baseline_difference": item.baseline_difference,
        "duration_ms": item.duration_ms,
        "exit_code": item.exit_code,
        "gate": item.gate,
        "log_artifact": item.log_artifact,
        "required": item.required,
        "status": item.status,
        "summary": item.summary,
    }


def _counterexample_payload(item: Counterexample) -> dict[str, Any]:
    return {
        "attempt_number": item.attempt_number,
        "counterexample_id": item.counterexample_id,
        "expected_summary": item.expected_summary,
        "failure_type": item.failure_type,
        "gate": item.gate,
        "input_summary": item.input_summary,
        "is_new_vs_baseline": item.is_new_vs_baseline,
        "location_hints": item.location_hints,
        "log_excerpt": item.log_excerpt,
        "observed_summary": item.observed_summary,
        "sanitized_feedback": item.sanitized_feedback,
        "source": item.source,
    }
