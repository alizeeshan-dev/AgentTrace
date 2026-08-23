"""Typed, evidence-only representation of one completed AgentTrace run."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue

from app.schemas.common import FilesystemIdentifier, ResearchSchema


class ReportIdentity(ResearchSchema):
    repository_id: str
    repository_name: str
    repository_url: str | None = None
    repository_commit: str
    task_id: str
    task_title: str
    task_description: str
    task_source: Literal["benchmark", "external"]
    task_category: str
    difficulty: str
    configuration: str
    model: str


class ReportOutcome(ResearchSchema):
    final_status: str
    resolved: bool | None
    repair_attempted: bool
    repair_successful: bool | None
    final_verification_status: str
    failure_category: str | None = None


class ToolCallReport(ResearchSchema):
    sequence_number: int = Field(ge=0)
    tool: str
    arguments_summary: str | None = None
    status: str


class SuspiciousLocationReport(ResearchSchema):
    rank: int = Field(ge=1)
    file: str
    line: int = Field(ge=1)
    score: float = Field(ge=0, le=1)
    symbol: str | None = None


class FaultLocalizationReport(ResearchSchema):
    metric: str
    source_run_id: str
    top_k: int = Field(ge=1)
    suspicious_locations: list[SuspiciousLocationReport] = Field(default_factory=list)


class InvestigationReport(ResearchSchema):
    files_inspected: int = Field(ge=0)
    inspected_paths: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallReport] = Field(default_factory=list)
    fault_localization: FaultLocalizationReport | None = None


class PatchReport(ResearchSchema):
    attempt_number: Literal[1, 2]
    files_changed: list[str] = Field(default_factory=list)
    lines_added: int = Field(ge=0)
    lines_removed: int = Field(ge=0)
    applied_successfully: bool
    patch_sha256: str
    unified_diff: str
    rationale: str | None = None
    expected_behavioral_change: str | None = None
    verification_outcome: str


class CounterexampleReport(ResearchSchema):
    source: str
    failed_gate: str
    input_summary: str | None = None
    expected_behavior: str | None = None
    observed_behavior: str
    failure_type: str | None = None
    location_hints: list[str] = Field(default_factory=list)
    new_vs_baseline: bool
    safe_feedback: str


class RepairReport(ResearchSchema):
    attempted: Literal[True] = True
    replacement_patch: PatchReport | None = None
    verification_outcome: str
    added_input_tokens: int | None = Field(default=None, ge=0)
    added_output_tokens: int | None = Field(default=None, ge=0)
    added_cost: float | None = Field(default=None, ge=0)
    added_latency_ms: int | None = Field(default=None, ge=0)
    successful: bool | None = None


class VerificationGateReport(ResearchSchema):
    attempt_number: Literal[1, 2]
    gate: str
    required: bool
    status: str
    concise_result: str
    baseline_difference: dict[str, JsonValue] | None = None
    duration_ms: int = Field(ge=0)


class VerificationReport(ResearchSchema):
    final_attempt: Literal[1, 2] | None = None
    final_status: str
    required_gates: list[VerificationGateReport] = Field(default_factory=list)
    advisory_gates: list[VerificationGateReport] = Field(default_factory=list)
    baseline_gates: list[VerificationGateReport] = Field(default_factory=list)
    regression_detected: bool | None = None


class EfficiencyReport(ResearchSchema):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    total_latency_ms: int | None = Field(default=None, ge=0)
    tool_calls: int = Field(ge=0)
    files_inspected: int = Field(ge=0)
    lines_exposed: int = Field(ge=0)


class AssessmentDimension(ResearchSchema):
    value: str
    basis: list[str] = Field(min_length=1)


class AssessmentReport(ResearchSchema):
    final_resolution: AssessmentDimension
    verification_outcome: AssessmentDimension
    test_oracle_strength: AssessmentDimension
    regression_evidence: AssessmentDimension
    patch_scope: AssessmentDimension
    fault_localization_evidence: AssessmentDimension
    repair_requirement: AssessmentDimension
    static_analysis: AssessmentDimension


class SourceArtifactReport(ResearchSchema):
    reference: str
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    available: bool


class RunReport(ResearchSchema):
    """Deterministic report of a run, not a repository-wide audit."""

    schema_version: Literal[1] = 1
    report_version: Literal["run-report-v1"] = "run-report-v1"
    report_status: Literal["ready"] = "ready"
    report_id: FilesystemIdentifier
    run_id: FilesystemIdentifier
    generated_at: datetime
    identity: ReportIdentity
    outcome: ReportOutcome
    issue_summary: str
    investigation: InvestigationReport
    initial_patch: PatchReport | None = None
    counterexamples: list[CounterexampleReport] = Field(default_factory=list)
    repair: RepairReport | None = None
    verification: VerificationReport
    efficiency: EfficiencyReport
    assessment: AssessmentReport
    limitations: list[str] = Field(min_length=1)
    source_artifacts: list[SourceArtifactReport] = Field(default_factory=list)
    evidence_sha256: str
    markdown_artifact: str | None = None
    markdown_sha256: str | None = None


class RunReportMetadata(ResearchSchema):
    report_status: Literal["ready"] = "ready"
    report_id: FilesystemIdentifier
    run_id: FilesystemIdentifier
    report_version: str
    generated_at: datetime
    evidence_sha256: str
    markdown_artifact: str
    markdown_sha256: str
