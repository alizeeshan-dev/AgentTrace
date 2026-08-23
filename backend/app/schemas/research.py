"""Typed representations of AgentTrace's small research data model."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from app.schemas.common import (
    CommitSha,
    FilesystemIdentifier,
    Identifier,
    ResearchSchema,
    validate_repository_path,
)

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Command = Annotated[str, StringConstraints(min_length=1, max_length=500, pattern=r"^[^\x00\r\n]+$")]
ArtifactReference = Annotated[str, StringConstraints(min_length=1, max_length=500)]
AttemptNumber = Annotated[int, Field(ge=1, le=2)]
NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]


class Repository(ResearchSchema):
    repository_id: Identifier
    name: ShortText
    source: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    base_commit: CommitSha
    python_version: Annotated[str, StringConstraints(min_length=1, max_length=50)] | None = None
    test_command: Command | None = None
    source_type: Literal["local", "benchmark", "external_git"] = "local"
    repository_url: str | None = None
    default_branch: ShortText | None = None
    primary_language: ShortText | None = None
    registered_at: datetime | None = None
    managed_source: str | None = None
    trusted_for_local_execution: bool = False
    trust_confirmed_at: datetime | None = None
    repository_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Task(ResearchSchema):
    task_id: Identifier
    repository_id: Identifier
    title: ShortText
    description: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
    task_category: Literal["bug_fix", "refactor"]
    difficulty: Literal["easy", "medium", "hard", "unspecified"]
    allowed_paths: Annotated[list[str], Field(max_length=30)]
    forbidden_paths: Annotated[list[str], Field(max_length=30)]
    visible_test_command: Command | None = None
    hidden_test_command: Command | None = None
    property_profile: ShortText | None = None
    symbolic_profile: ShortText | None = None
    known_correct_patch: str | None = None
    task_source: Literal["benchmark", "external"] = "benchmark"
    verification_configured: bool = True
    definition_path: str | None = None
    created_at: datetime | None = None

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [validate_repository_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("repository paths must be unique")
        return normalized


class BenchmarkQuality(ResearchSchema):
    task_id: Identifier
    baseline_status: ShortText
    mutation_tool: ShortText | None = None
    mutation_score: Annotated[float, Field(ge=0, le=1)] | None = None
    mutants_generated: NonNegativeInt = 0
    mutants_killed: NonNegativeInt = 0
    mutants_survived: NonNegativeInt = 0
    mutants_excluded: NonNegativeInt = 0
    mutants_skipped: NonNegativeInt = 0
    mutants_invalid: NonNegativeInt = 0
    mutants_unusable: NonNegativeInt = 0
    mutation_tool_version: ShortText | None = None
    mutation_completed: bool = False
    mutation_duration_ms: NonNegativeInt | None = None
    mutation_artifact: ArtifactReference | None = None
    qualification_artifact: ArtifactReference | None = None
    execution_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    quality_notes: str | None = None

    @model_validator(mode="after")
    def mutation_counts_are_consistent(self) -> BenchmarkQuality:
        if self.mutants_invalid > self.mutants_excluded:
            raise ValueError("invalid mutants must be included in excluded mutants")
        if self.mutation_completed:
            classified = (
                self.mutants_killed
                + self.mutants_survived
                + self.mutants_excluded
                + self.mutants_skipped
                + self.mutants_unusable
            )
            if classified != self.mutants_generated:
                raise ValueError("completed mutation counts must classify every mutant")
            expected = (
                None
                if self.mutants_killed + self.mutants_survived == 0
                else self.mutants_killed / (self.mutants_killed + self.mutants_survived)
            )
            if expected is None and self.mutation_score is not None:
                raise ValueError("mutation score requires killed or survived mutants")
            if (
                expected is not None
                and self.mutation_score is not None
                and abs(expected - self.mutation_score) > 1e-12
            ):
                raise ValueError("mutation score does not match killed and survived counts")
        return self


class Run(ResearchSchema):
    run_id: FilesystemIdentifier
    task_id: Identifier
    configuration_id: Identifier
    model: ShortText
    model_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    status: ShortText
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: NonNegativeInt | None = None
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0
    estimated_cost: NonNegativeFloat | None = None
    tool_calls: NonNegativeInt = 0
    files_read: NonNegativeInt = 0
    lines_exposed: NonNegativeInt = 0
    repair_attempted: bool = False
    final_resolution: bool | None = None
    failure_category: Annotated[str, StringConstraints(max_length=100)] | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamps must include a UTC offset")
        return value

    @model_validator(mode="after")
    def finished_at_is_not_earlier(self) -> Run:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        return self


class FaultLocalizationResult(ResearchSchema):
    run_id: FilesystemIdentifier
    metric: ShortText
    ranked_locations: list[dict[str, JsonValue]] = Field(default_factory=list)
    top_k: Annotated[int, Field(ge=1)]
    fault_rank_if_known: Annotated[int, Field(ge=1)] | None = None
    coverage_artifact: ArtifactReference | None = None


class PatchArtifact(ResearchSchema):
    run_id: FilesystemIdentifier
    attempt_number: AttemptNumber
    unified_diff: str
    files_changed: list[str] = Field(default_factory=list)
    lines_added: NonNegativeInt = 0
    lines_removed: NonNegativeInt = 0
    applied_successfully: bool = False

    @field_validator("files_changed")
    @classmethod
    def validate_changed_paths(cls, values: list[str]) -> list[str]:
        normalized = [validate_repository_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("changed file paths must be unique")
        return normalized


class VerificationResult(ResearchSchema):
    run_id: FilesystemIdentifier
    attempt_number: AttemptNumber
    gate: ShortText
    required: bool
    status: ShortText
    exit_code: int | None = None
    duration_ms: NonNegativeInt
    baseline_difference: dict[str, JsonValue] | None = None
    summary: str
    log_artifact: ArtifactReference | None = None


class Counterexample(ResearchSchema):
    counterexample_id: Identifier
    run_id: FilesystemIdentifier
    attempt_number: AttemptNumber
    source: ShortText
    gate: ShortText
    input_summary: str | None = None
    expected_summary: str | None = None
    observed_summary: str
    failure_type: Annotated[str, StringConstraints(max_length=200)] | None = None
    location_hints: list[str] = Field(default_factory=list)
    is_new_vs_baseline: bool
    log_excerpt: Annotated[str, StringConstraints(max_length=4_000)] | None = None
    sanitized_feedback: str

    @field_validator("location_hints")
    @classmethod
    def validate_location_hints(cls, values: list[str]) -> list[str]:
        for value in values:
            path, separator, line = value.rpartition(":")
            if not separator or not line.isdigit() or int(line) < 1:
                raise ValueError("location hints must use repository/path.py:line syntax")
            validate_repository_path(path)
        return values


class TraceEvent(ResearchSchema):
    event_id: Identifier
    run_id: FilesystemIdentifier
    sequence_number: Annotated[int, Field(ge=0)]
    parent_event_id: Identifier | None = None
    operation: ShortText
    started_at: datetime
    finished_at: datetime | None = None
    status: ShortText
    input_summary: str | None = None
    output_summary: str | None = None
    error_type: ShortText | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamps must include a UTC offset")
        return value

    @model_validator(mode="after")
    def finished_at_is_not_earlier(self) -> TraceEvent:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        return self
