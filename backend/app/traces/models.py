"""Typed, provider-neutral records for canonical AgentTrace trace export."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from app.schemas.common import FilesystemIdentifier, ResearchSchema


def _no_derived_data() -> dict[str, JsonValue]:
    return {
        "included": False,
        "reason": "Derived analysis is stored separately from immutable raw runs.",
    }


class TraceOperation(StrEnum):
    """Stable operation names, aligned with GenAI terminology where useful.

    These names are an internal vocabulary.  They do not assert OpenTelemetry
    compliance and do not require a telemetry collector.
    """

    PREPARE_WORKSPACE = "workflow.prepare_workspace"
    BASELINE_VERIFICATION = "workflow.baseline_verification"
    COVERAGE_COLLECTION = "workflow.coverage_collection"
    FAULT_LOCALIZATION = "workflow.fault_localization"
    MODEL_INFERENCE = "gen_ai.client.inference"
    TOOL_EXECUTION = "gen_ai.tool.call"
    PATCH_SUBMISSION = "agent.patch.submit"
    VERIFICATION_GATE = "workflow.verification.gate"
    COUNTEREXAMPLE = "workflow.counterexample.create"
    REPAIR_ATTEMPT = "workflow.repair.attempt"
    FINAL_RESULT = "workflow.final_result"


class CanonicalTraceEvent(ResearchSchema):
    event_id: str
    sequence_number: int = Field(ge=0)
    parent_event_id: str | None = None
    operation: TraceOperation
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    input_summary: str | None = None
    output_summary: str | None = None
    error_type: str | None = None


class ArtifactDescriptor(ResearchSchema):
    relative_path: str
    sha256: str | None
    size_bytes: int | None = Field(default=None, ge=0)
    kind: str
    available: bool


class RawRunExport(ResearchSchema):
    """Self-contained immutable raw-run snapshot for later derivation."""

    schema_version: Literal[1] = 1
    data_kind: Literal["raw_run"] = "raw_run"
    telemetry_note: Literal[
        "OpenTelemetry GenAI terminology alignment only; not a compliance claim."
    ] = "OpenTelemetry GenAI terminology alignment only; not a compliance claim."
    run_id: FilesystemIdentifier
    run: dict[str, JsonValue]
    task: dict[str, JsonValue]
    repository: dict[str, JsonValue]
    benchmark_quality: dict[str, JsonValue] | None = None
    fault_localization: list[dict[str, JsonValue]] = Field(default_factory=list)
    patches: list[dict[str, JsonValue]] = Field(default_factory=list)
    verification_results: list[dict[str, JsonValue]] = Field(default_factory=list)
    counterexamples: list[dict[str, JsonValue]] = Field(default_factory=list)
    trace_events: list[CanonicalTraceEvent] = Field(default_factory=list)
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    derived_data: dict[str, JsonValue] = Field(default_factory=_no_derived_data)
