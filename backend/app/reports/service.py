"""Deterministic assembly and idempotent persistence of completed-run reports."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts import ArtifactStore
from app.config import Settings
from app.db.models import (
    BenchmarkQuality,
    Counterexample,
    FaultLocalizationResult,
    PatchArtifact,
    Repository,
    Run,
    RunReportRecord,
    Task,
    TraceEvent,
    VerificationResult,
)
from app.traces.assembler import CanonicalTraceAssembler
from app.traces.models import TraceOperation
from app.traces.redaction import TraceRedactor

from .markdown import render_markdown
from .models import (
    AssessmentDimension,
    AssessmentReport,
    CounterexampleReport,
    EfficiencyReport,
    FaultLocalizationReport,
    InvestigationReport,
    PatchReport,
    RepairReport,
    ReportIdentity,
    ReportOutcome,
    RunReport,
    RunReportMetadata,
    SourceArtifactReport,
    SuspiciousLocationReport,
    ToolCallReport,
    VerificationGateReport,
    VerificationReport,
)

_REPORT_VERSION = "run-report-v1"
_ACTIVE_STATUSES = {"preparing", "running"}
_STATIC_GATES = {"ruff", "mypy", "bandit"}


class RunReportError(RuntimeError):
    """Controlled report failure suitable for the local HTTP API."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RunReportService:
    """Build one evidence-grounded report without invoking a model provider."""

    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.artifacts = ArtifactStore(
            settings.effective_artifact_root,
            max_artifact_bytes=settings.max_artifact_size_bytes,
        )
        self.redactor = TraceRedactor(max_text_characters=100_000)

    def generate(self, run_id: str) -> RunReport:
        existing = self._record(run_id)
        if existing is not None:
            return RunReport.model_validate(existing.structured_report)

        run = self.session.get(Run, run_id)
        if run is None:
            raise RunReportError("run_not_found", "Run not found.")
        if run.finished_at is None or run.status.casefold() in _ACTIVE_STATUSES:
            raise RunReportError(
                "run_not_complete", "A report can be generated only for a completed run."
            )
        task = self.session.get(Task, run.task_id)
        if task is None:
            raise RunReportError("run_evidence_missing", "Run task evidence is missing.")
        repository = self.session.get(Repository, task.repository_id)
        if repository is None:
            raise RunReportError("run_evidence_missing", "Run repository evidence is missing.")

        # Canonical trace assembly is the existing deterministic bridge from
        # phase-specific evidence artifacts to stable observable tool events.
        CanonicalTraceAssembler(self.session, self.artifacts).materialize(run_id)
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
        events = tuple(
            self.session.scalars(
                select(TraceEvent)
                .where(TraceEvent.run_id == run_id)
                .order_by(TraceEvent.sequence_number)
            )
        )
        quality = self.session.get(BenchmarkQuality, task.task_id)
        localizations = self._localizations(run)
        source_artifacts = self._source_artifacts(run, quality, localizations, gates)
        evidence_sha256 = _evidence_hash(
            run,
            task,
            repository,
            quality,
            localizations,
            patches,
            gates,
            counterexamples,
            events,
            source_artifacts,
        )
        report_id = _report_id(run_id, evidence_sha256)
        generated_at = datetime.now(UTC)
        verification = _verification_report(gates)
        rationales = _patch_narratives(events)
        patch_reports = {
            patch.attempt_number: _patch_report(
                patch,
                gates,
                narrative=rationales.get(patch.attempt_number),
            )
            for patch in patches
        }
        counterexample_reports = [_counterexample_report(item) for item in counterexamples]
        investigation = _investigation_report(run, events, localizations)
        repair_metrics = _repair_metrics(run)
        repair = _repair_report(
            run,
            patch_reports.get(2),
            gates,
            repair_metrics,
        )
        outcome = _outcome_report(run, verification, repair_metrics)
        assessment = _assessment_report(
            run,
            task,
            quality,
            investigation,
            patch_reports.get(2) or patch_reports.get(1),
            verification,
            repair_metrics,
        )
        report = RunReport(
            report_id=report_id,
            run_id=run_id,
            generated_at=generated_at,
            identity=ReportIdentity(
                repository_id=repository.repository_id,
                repository_name=repository.name,
                repository_url=repository.repository_url,
                repository_commit=repository.base_commit,
                task_id=task.task_id,
                task_title=task.title,
                task_description=task.description,
                task_source=task.task_source,  # type: ignore[arg-type]
                task_category=task.task_category,
                difficulty=task.difficulty,
                configuration=run.configuration_id,
                model=run.model,
            ),
            outcome=outcome,
            issue_summary=_issue_summary(task, counterexample_reports, gates, investigation),
            investigation=investigation,
            initial_patch=patch_reports.get(1),
            counterexamples=counterexample_reports,
            repair=repair,
            verification=verification,
            efficiency=EfficiencyReport(
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                total_tokens=run.input_tokens + run.output_tokens,
                estimated_cost=run.estimated_cost,
                total_latency_ms=run.latency_ms,
                tool_calls=run.tool_calls,
                files_inspected=run.files_read,
                lines_exposed=run.lines_exposed,
            ),
            assessment=assessment,
            limitations=_limitations(run, task, quality, investigation, gates),
            source_artifacts=source_artifacts,
            evidence_sha256=evidence_sha256,
        )
        report = RunReport.model_validate(
            self.redactor.redact(report.model_dump(mode="json"))
        )
        markdown = render_markdown(report)
        markdown_reference = self.artifacts.store_text(
            run_id=run_id,
            kind="other",
            text=markdown,
            suffix=".md",
        )
        report = report.model_copy(
            update={
                "markdown_artifact": markdown_reference.relative_path,
                "markdown_sha256": markdown_reference.sha256,
            }
        )
        self.session.add(
            RunReportRecord(
                report_id=report.report_id,
                run_id=run_id,
                generation_version=_REPORT_VERSION,
                generated_at=generated_at,
                evidence_sha256=evidence_sha256,
                structured_report=report.model_dump(mode="json"),
                markdown_artifact=markdown_reference.relative_path,
                markdown_sha256=markdown_reference.sha256,
                source_artifacts=[item.model_dump(mode="json") for item in source_artifacts],
            )
        )
        self.session.flush()
        return report

    def get(self, run_id: str) -> RunReport:
        record = self._record(run_id)
        if record is None:
            raise RunReportError("report_not_found", "Run report has not been generated.")
        return RunReport.model_validate(record.structured_report)

    def metadata(self, run_id: str) -> RunReportMetadata:
        record = self._record(run_id)
        if record is None:
            raise RunReportError("report_not_found", "Run report has not been generated.")
        return RunReportMetadata(
            report_id=record.report_id,
            run_id=record.run_id,
            report_version=record.generation_version,
            generated_at=record.generated_at,
            evidence_sha256=record.evidence_sha256,
            markdown_artifact=record.markdown_artifact,
            markdown_sha256=record.markdown_sha256,
        )

    def markdown(self, run_id: str) -> str:
        record = self._record(run_id)
        if record is None:
            raise RunReportError("report_not_found", "Run report has not been generated.")
        try:
            data = self.artifacts.read_bytes(record.markdown_artifact)
        except (OSError, ValueError) as error:
            raise RunReportError(
                "report_artifact_unavailable", "The persisted Markdown report is unavailable."
            ) from error
        if hashlib.sha256(data).hexdigest() != record.markdown_sha256:
            raise RunReportError(
                "report_artifact_invalid", "The persisted Markdown report hash does not match."
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RunReportError(
                "report_artifact_invalid", "The persisted Markdown report is not UTF-8."
            ) from error

    def _record(self, run_id: str) -> RunReportRecord | None:
        return self.session.scalar(
            select(RunReportRecord).where(RunReportRecord.run_id == run_id)
        )

    def _localizations(self, run: Run) -> tuple[FaultLocalizationResult, ...]:
        run_ids = {run.run_id}
        evidence = run.model_parameters.get("sbfl_evidence")
        if isinstance(evidence, dict) and isinstance(evidence.get("source_run_id"), str):
            run_ids.add(evidence["source_run_id"])
        return tuple(
            self.session.scalars(
                select(FaultLocalizationResult)
                .where(FaultLocalizationResult.run_id.in_(sorted(run_ids)))
                .order_by(FaultLocalizationResult.run_id)
            )
        )

    def _source_artifacts(
        self,
        run: Run,
        quality: BenchmarkQuality | None,
        localizations: tuple[FaultLocalizationResult, ...],
        gates: tuple[VerificationResult, ...],
    ) -> list[SourceArtifactReport]:
        references: set[str] = set()
        raw_references = run.model_parameters.get("artifact_references")
        if isinstance(raw_references, dict):
            references.update(value for value in raw_references.values() if isinstance(value, str))
        references.update(item.log_artifact for item in gates if item.log_artifact)
        references.update(
            item.coverage_artifact for item in localizations if item.coverage_artifact
        )
        if quality is not None:
            references.update(
                value
                for value in (
                    quality.mutation_artifact,
                    quality.qualification_artifact,
                )
                if value
            )
        result: list[SourceArtifactReport] = []
        for reference in sorted(references):
            try:
                data = self.artifacts.read_bytes(reference)
            except (OSError, ValueError):
                result.append(SourceArtifactReport(reference=reference, available=False))
            else:
                result.append(
                    SourceArtifactReport(
                        reference=reference,
                        sha256=hashlib.sha256(data).hexdigest(),
                        size_bytes=len(data),
                        available=True,
                    )
                )
        return result


def _verification_report(
    gates: tuple[VerificationResult, ...],
) -> VerificationReport:
    reports = [_gate_report(item) for item in gates]
    baseline = [item for item in reports if item.gate.startswith("baseline_")]
    candidate = [item for item in reports if not item.gate.startswith("baseline_")]
    final_attempt = max((item.attempt_number for item in candidate), default=None)
    final = [item for item in candidate if item.attempt_number == final_attempt]
    required = [item for item in final if item.required]
    advisory = [item for item in final if not item.required]
    status = _gate_set_status(required, advisory)
    regression = (
        any(_is_regression(item.baseline_difference) for item in final)
        if final and not any(item.status == "not_configured" for item in final)
        else None
    )
    return VerificationReport(
        final_attempt=final_attempt,
        final_status=status,
        required_gates=required,
        advisory_gates=advisory,
        baseline_gates=baseline,
        regression_detected=regression,
    )


def _gate_report(item: VerificationResult) -> VerificationGateReport:
    return VerificationGateReport(
        attempt_number=item.attempt_number,  # type: ignore[arg-type]
        gate=item.gate,
        required=item.required,
        status=item.status,
        concise_result=item.summary,
        baseline_difference=item.baseline_difference,
        duration_ms=item.duration_ms,
    )


def _gate_set_status(
    required: list[VerificationGateReport], advisory: list[VerificationGateReport]
) -> str:
    all_gates = [*required, *advisory]
    if not all_gates:
        return "Not Available"
    if any(item.status == "not_configured" for item in all_gates):
        return "Not Fully Configured"
    if any(item.status == "error" for item in required):
        return "Infrastructure Failure"
    if any(
        item.status in {"failed", "timed_out", "counterexample_found"} for item in required
    ):
        return "Required Checks Failed"
    if required and all(item.status == "passed" for item in required):
        return "All Required Checks Passed"
    return "Partially Verified"


def _patch_report(
    patch: PatchArtifact,
    gates: tuple[VerificationResult, ...],
    *,
    narrative: tuple[str | None, str | None] | None,
) -> PatchReport:
    attempt_gates = tuple(
        item
        for item in gates
        if item.attempt_number == patch.attempt_number and not item.gate.startswith("baseline_")
    )
    verification = _gate_set_status(
        [_gate_report(item) for item in attempt_gates if item.required],
        [_gate_report(item) for item in attempt_gates if not item.required],
    )
    rationale, expected = narrative or (None, None)
    return PatchReport(
        attempt_number=patch.attempt_number,  # type: ignore[arg-type]
        files_changed=list(patch.files_changed),
        lines_added=patch.lines_added,
        lines_removed=patch.lines_removed,
        applied_successfully=patch.applied_successfully,
        patch_sha256=hashlib.sha256(patch.unified_diff.encode()).hexdigest(),
        unified_diff=patch.unified_diff,
        rationale=rationale,
        expected_behavioral_change=expected,
        verification_outcome=verification,
    )


def _counterexample_report(item: Counterexample) -> CounterexampleReport:
    return CounterexampleReport(
        source=item.source,
        failed_gate=item.gate,
        input_summary=item.input_summary,
        expected_behavior=item.expected_summary,
        observed_behavior=item.observed_summary,
        failure_type=item.failure_type,
        location_hints=list(item.location_hints),
        new_vs_baseline=item.is_new_vs_baseline,
        safe_feedback=item.sanitized_feedback,
    )


def _investigation_report(
    run: Run,
    events: tuple[TraceEvent, ...],
    localizations: tuple[FaultLocalizationResult, ...],
) -> InvestigationReport:
    calls: list[ToolCallReport] = []
    paths: set[str] = set()
    for event in events:
        if event.operation != TraceOperation.TOOL_EXECUTION.value:
            continue
        payload = _json_object(event.input_summary)
        raw_tool = payload.get("tool")
        tool = raw_tool if isinstance(raw_tool, str) else "unknown"
        arguments = payload.get("arguments")
        if isinstance(arguments, dict):
            path = arguments.get("path")
            if isinstance(path, str):
                paths.add(path)
        calls.append(
            ToolCallReport(
                sequence_number=event.sequence_number,
                tool=tool,
                arguments_summary=_compact_json(arguments),
                status=event.status,
            )
        )
    localization = _localization_report(localizations)
    return InvestigationReport(
        files_inspected=run.files_read,
        inspected_paths=sorted(paths),
        tool_calls=calls,
        fault_localization=localization,
    )


def _localization_report(
    localizations: tuple[FaultLocalizationResult, ...],
) -> FaultLocalizationReport | None:
    if not localizations:
        return None
    record = localizations[-1]
    locations: list[SuspiciousLocationReport] = []
    for item in record.ranked_locations:
        rank, file, line = item.get("rank"), item.get("file"), item.get("line")
        score = item.get("ochiai", item.get("score"))
        symbol = item.get("symbol")
        if (
            isinstance(rank, int)
            and not isinstance(rank, bool)
            and rank >= 1
            and isinstance(file, str)
            and isinstance(line, int)
            and not isinstance(line, bool)
            and line >= 1
            and isinstance(score, int | float)
            and not isinstance(score, bool)
            and 0 <= float(score) <= 1
        ):
            locations.append(
                SuspiciousLocationReport(
                    rank=rank,
                    file=file,
                    line=line,
                    score=float(score),
                    symbol=symbol if isinstance(symbol, str) else None,
                )
            )
    if not locations:
        return None
    return FaultLocalizationReport(
        metric=record.metric,
        source_run_id=record.run_id,
        top_k=record.top_k,
        suspicious_locations=locations,
    )


def _repair_report(
    run: Run,
    replacement_patch: PatchReport | None,
    gates: tuple[VerificationResult, ...],
    metrics: dict[str, Any],
) -> RepairReport | None:
    if not run.repair_attempted:
        return None
    final_gates = tuple(
        item for item in gates if item.attempt_number == 2 and not item.gate.startswith("baseline_")
    )
    outcome = _gate_set_status(
        [_gate_report(item) for item in final_gates if item.required],
        [_gate_report(item) for item in final_gates if not item.required],
    )
    return RepairReport(
        replacement_patch=replacement_patch,
        verification_outcome=outcome,
        added_input_tokens=_nonnegative_int(metrics.get("added_input_tokens")),
        added_output_tokens=_nonnegative_int(metrics.get("added_output_tokens")),
        added_cost=_nonnegative_float(metrics.get("added_cost")),
        added_latency_ms=_nonnegative_int(metrics.get("added_latency_ms")),
        successful=_optional_bool(metrics.get("repair_success")),
    )


def _outcome_report(
    run: Run, verification: VerificationReport, metrics: dict[str, Any]
) -> ReportOutcome:
    return ReportOutcome(
        final_status=run.status,
        resolved=run.final_resolution,
        repair_attempted=run.repair_attempted,
        repair_successful=(
            _optional_bool(metrics.get("repair_success")) if run.repair_attempted else None
        ),
        final_verification_status=verification.final_status,
        failure_category=run.failure_category,
    )


def _assessment_report(
    run: Run,
    task: Task,
    quality: BenchmarkQuality | None,
    investigation: InvestigationReport,
    final_patch: PatchReport | None,
    verification: VerificationReport,
    repair_metrics: dict[str, Any],
) -> AssessmentReport:
    if (
        "infrastructure" in run.status.casefold()
        or run.failure_category == "INFRASTRUCTURE_FAILURE"
    ):
        resolution_value = "Infrastructure Failure"
    elif run.final_resolution is True:
        resolution_value = "Resolved"
    elif run.final_resolution is False:
        resolution_value = "Unresolved"
    else:
        resolution_value = "Not Assessed"
    verification_basis = [
        f"{len(verification.required_gates)} final required gate(s) and "
        f"{len(verification.advisory_gates)} final advisory gate(s) were stored."
    ]
    if quality is not None and quality.mutation_completed and quality.mutation_score is not None:
        oracle_value = f"Mutation score {quality.mutation_score:.1%}"
        oracle_basis = [
            f"pytest-gremlins killed {quality.mutants_killed} and left "
            f"{quality.mutants_survived} mutation(s) surviving.",
            "Mutation score measures the benchmark oracle, not overall repository quality.",
        ]
    else:
        oracle_value = "Not Assessed"
        oracle_basis = [
            "No completed mutation-qualification evidence is stored for this task."
        ]
    if verification.regression_detected is True:
        regression_value = "Regression Detected"
        regression_basis = ["A final gate recorded a regression relative to baseline evidence."]
    elif verification.regression_detected is False:
        regression_value = "No Regression Detected"
        regression_basis = [
            "No configured final verification gate recorded a regression relative to baseline."
        ]
    else:
        regression_value = "Insufficient Evidence"
        regression_basis = ["No complete comparable final verification evidence is available."]
    patch_value, patch_basis = _patch_scope(final_patch)
    if investigation.fault_localization is not None:
        top = investigation.fault_localization.suspicious_locations[0]
        localization_value = "Available"
        localization_basis = [
            f"Top probabilistic location: {top.file}:{top.line} with "
            f"{investigation.fault_localization.metric}={top.score:.6f}."
        ]
    elif run.configuration_id.startswith("D"):
        localization_value = "Unavailable"
        localization_basis = ["No compatible persisted fault-localization evidence was used."]
    else:
        localization_value = "Not Used"
        localization_basis = ["This run configuration did not use SBFL evidence."]
    if run.repair_attempted:
        if _optional_bool(repair_metrics.get("repair_success")) is True:
            repair_value = "Repair Successful"
        else:
            repair_value = "Repair Failed"
        repair_basis = ["The run stored a bounded CEGIS repair attempt."]
    elif run.final_resolution is True:
        repair_value = "No Repair Needed"
        repair_basis = ["The initial candidate satisfied the configured required checks."]
    else:
        repair_value = "No Repair Attempted"
        repair_basis = ["No replacement patch attempt is stored for this run."]
    static = [
        item
        for item in verification.advisory_gates
        if item.gate.casefold() in _STATIC_GATES
    ]
    if static:
        static_value = ", ".join(f"{item.gate}: {item.status}" for item in static)
        static_basis = [
            "Ruff, mypy, and Bandit are advisory evidence and are not correctness proofs."
        ]
    else:
        static_value = "Not Configured or Not Available"
        static_basis = ["No final Ruff, mypy, or Bandit gate evidence is stored."]
    return AssessmentReport(
        final_resolution=AssessmentDimension(
            value=resolution_value,
            basis=[f"Run status={run.status}; final_resolution={run.final_resolution}."],
        ),
        verification_outcome=AssessmentDimension(
            value=verification.final_status,
            basis=verification_basis,
        ),
        test_oracle_strength=AssessmentDimension(value=oracle_value, basis=oracle_basis),
        regression_evidence=AssessmentDimension(
            value=regression_value, basis=regression_basis
        ),
        patch_scope=AssessmentDimension(value=patch_value, basis=patch_basis),
        fault_localization_evidence=AssessmentDimension(
            value=localization_value, basis=localization_basis
        ),
        repair_requirement=AssessmentDimension(value=repair_value, basis=repair_basis),
        static_analysis=AssessmentDimension(value=static_value, basis=static_basis),
    )


def _patch_scope(patch: PatchReport | None) -> tuple[str, list[str]]:
    if patch is None:
        return "Not Available", ["No submitted patch artifact is stored."]
    files = len(patch.files_changed)
    changed = patch.lines_added + patch.lines_removed
    if files <= 2 and changed <= 50:
        value = "Focused"
    elif files <= 5 and changed <= 200:
        value = "Moderate"
    else:
        value = "Broad"
    return value, [
        f"The final candidate changed {files} file(s), adding {patch.lines_added} and "
        f"removing {patch.lines_removed} line(s).",
        "Rule: Focused <=2 files and <=50 changed lines; Moderate <=5 files and "
        "<=200 changed lines; otherwise Broad.",
    ]


def _issue_summary(
    task: Task,
    counterexamples: list[CounterexampleReport],
    gates: tuple[VerificationResult, ...],
    investigation: InvestigationReport,
) -> str:
    task_text = _bounded_text(task.description, 500)
    if counterexamples:
        evidence = counterexamples[0]
        observed = _bounded_text(evidence.observed_behavior, 300)
        return (
            f"The run investigated the task: {task_text} Verification observed "
            f"{evidence.failed_gate}: {observed}"
        )
    failed = next(
        (
            item
            for item in gates
            if not item.gate.startswith("baseline_")
            and item.required
            and item.status in {"failed", "timed_out", "error", "counterexample_found"}
        ),
        None,
    )
    if failed is not None:
        return (
            f"The run investigated the task: {task_text} Verification recorded "
            f"{failed.gate}: {_bounded_text(failed.summary, 300)}"
        )
    if investigation.fault_localization is not None:
        top = investigation.fault_localization.suspicious_locations[0]
        return (
            f"The run investigated the task: {task_text} Probabilistic fault localization "
            f"ranked {top.file}:{top.line} highest; no concrete failure symptom was stored."
        )
    return (
        f"The run investigated the task: {task_text} No concrete counterexample or failed "
        "required verification gate was stored."
    )


def _limitations(
    run: Run,
    task: Task,
    quality: BenchmarkQuality | None,
    investigation: InvestigationReport,
    gates: tuple[VerificationResult, ...],
) -> list[str]:
    limitations = [
        "This is a deterministic report of one AgentTrace run, not a comprehensive "
        "repository audit or proof of correctness."
    ]
    gate_names = {item.gate.removeprefix("baseline_") for item in gates}
    if task.task_source == "external":
        limitations.extend(
            [
                "External task mode has no AgentTrace benchmark ground truth or "
                "known-correct patch.",
                "External task mode has no evaluator-owned hidden tests unless "
                "separately curated.",
            ]
        )
        if quality is None or not quality.mutation_completed:
            limitations.append(
                "Mutation-test oracle strength was not assessed for this external task."
            )
    elif quality is None or not quality.mutation_completed:
        limitations.append("Benchmark mutation qualification evidence is not available.")
    if not task.verification_configured or "verification_configuration" in gate_names:
        limitations.append("Verification was not fully configured for this task.")
    if "hidden_tests" not in gate_names:
        limitations.append("No hidden-test gate result is stored for this run.")
    if "hypothesis_properties" not in gate_names:
        limitations.append("No task-specific Hypothesis property result is stored.")
    if "symbolic" not in gate_names:
        limitations.append("No CrossHair/Z3 symbolic result is stored.")
    if investigation.fault_localization is None and run.configuration_id.startswith("D"):
        limitations.append("Configuration D had no compatible persisted SBFL evidence.")
    if run.estimated_cost is None:
        limitations.append("Exact or configured model cost evidence is unavailable.")
    return list(dict.fromkeys(limitations))


def _patch_narratives(
    events: tuple[TraceEvent, ...],
) -> dict[int, tuple[str | None, str | None]]:
    result: dict[int, tuple[str | None, str | None]] = {}
    attempt = 0
    for event in events:
        if event.operation != TraceOperation.MODEL_INFERENCE.value:
            continue
        payload = _json_object(event.output_summary)
        action = payload.get("action")
        if not isinstance(action, dict) or action.get("action_type") != "submit_patch":
            continue
        attempt += 1
        if attempt > 2:
            break
        explanation = action.get("explanation")
        expected = (
            explanation.get("expected_behavioral_change")
            if isinstance(explanation, dict)
            and isinstance(explanation.get("expected_behavioral_change"), str)
            else None
        )
        rationale = action.get("rationale")
        result[attempt] = (
            rationale if isinstance(rationale, str) else None,
            expected,
        )
    return result


def _repair_metrics(run: Run) -> dict[str, Any]:
    value = run.model_parameters.get("repair_metrics")
    return value if isinstance(value, dict) else {}


def _evidence_hash(
    run: Run,
    task: Task,
    repository: Repository,
    quality: BenchmarkQuality | None,
    localizations: tuple[FaultLocalizationResult, ...],
    patches: tuple[PatchArtifact, ...],
    gates: tuple[VerificationResult, ...],
    counterexamples: tuple[Counterexample, ...],
    events: tuple[TraceEvent, ...],
    source_artifacts: list[SourceArtifactReport],
) -> str:
    payload = {
        "run": _columns(run, Run),
        "task": _columns(task, Task, exclude={"definition_path", "hidden_test_command"}),
        "repository": _columns(
            repository,
            Repository,
            exclude={"source", "managed_source", "repository_metadata"},
        ),
        "quality": _columns(quality, BenchmarkQuality) if quality is not None else None,
        "localizations": [_columns(item, FaultLocalizationResult) for item in localizations],
        "patches": [_columns(item, PatchArtifact) for item in patches],
        "gates": [_columns(item, VerificationResult) for item in gates],
        "counterexamples": [_columns(item, Counterexample) for item in counterexamples],
        "events": [_columns(item, TraceEvent) for item in events],
        "source_artifacts": [item.model_dump(mode="json") for item in source_artifacts],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _columns(value: Any, model: type[Any], *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = exclude or set()
    return {
        column.name: getattr(value, column.name)
        for column in model.__table__.columns
        if column.name not in excluded
    }


def _report_id(run_id: str, evidence_sha256: str) -> str:
    digest = hashlib.sha256(
        f"{_REPORT_VERSION}\0{run_id}\0{evidence_sha256}".encode()
    ).hexdigest()[:32]
    return f"report-{digest}"


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _compact_json(value: Any) -> str | None:
    if value is None:
        return None
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _bounded_text(rendered, 500)


def _bounded_text(value: str, limit: int) -> str:
    flattened = " ".join(value.splitlines()).strip()
    if len(flattened) <= limit:
        return flattened
    digest = hashlib.sha256(flattened.encode()).hexdigest()[:12]
    return f"{flattened[: limit - 35]}...[truncated sha256={digest}]"


def _is_regression(value: dict[str, Any] | None) -> bool:
    return isinstance(value, dict) and value.get("regression") is True


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _nonnegative_float(value: Any) -> float | None:
    return (
        float(value)
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
        else None
    )


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")
