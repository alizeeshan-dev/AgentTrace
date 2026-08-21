"""SQLAlchemy persistence entities for AgentTrace research records."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class Repository(Base):
    __tablename__ = "repositories"

    repository_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(Text)
    base_commit: Mapped[str] = mapped_column(String(40), index=True)
    python_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    test_command: Mapped[str] = mapped_column(Text)


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.repository_id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    task_category: Mapped[str] = mapped_column(String(50))
    difficulty: Mapped[str] = mapped_column(String(20))
    allowed_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    forbidden_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    visible_test_command: Mapped[str] = mapped_column(Text)
    hidden_test_command: Mapped[str] = mapped_column(Text)
    property_profile: Mapped[str | None] = mapped_column(String(200), nullable=True)
    symbolic_profile: Mapped[str | None] = mapped_column(String(200), nullable=True)
    known_correct_patch: Mapped[str | None] = mapped_column(Text, nullable=True)


class BenchmarkQuality(Base):
    __tablename__ = "benchmark_quality"
    __table_args__ = (
        CheckConstraint("mutation_score IS NULL OR mutation_score BETWEEN 0 AND 1"),
        CheckConstraint("mutants_generated >= 0"),
        CheckConstraint("mutants_killed >= 0"),
        CheckConstraint("mutants_survived >= 0"),
        CheckConstraint("mutants_excluded >= 0"),
        CheckConstraint("mutants_skipped >= 0"),
        CheckConstraint("mutants_invalid >= 0"),
        CheckConstraint("mutants_unusable >= 0"),
        CheckConstraint("mutants_invalid <= mutants_excluded"),
        CheckConstraint("mutation_duration_ms IS NULL OR mutation_duration_ms >= 0"),
    )

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), primary_key=True
    )
    baseline_status: Mapped[str] = mapped_column(String(50))
    mutation_tool: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mutation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    mutants_generated: Mapped[int] = mapped_column(Integer, default=0)
    mutants_killed: Mapped[int] = mapped_column(Integer, default=0)
    mutants_survived: Mapped[int] = mapped_column(Integer, default=0)
    mutants_excluded: Mapped[int] = mapped_column(Integer, default=0)
    mutants_skipped: Mapped[int] = mapped_column(Integer, default=0)
    mutants_invalid: Mapped[int] = mapped_column(Integer, default=0)
    mutants_unusable: Mapped[int] = mapped_column(Integer, default=0)
    mutation_tool_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mutation_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    mutation_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mutation_artifact: Mapped[str | None] = mapped_column(String(500), nullable=True)
    qualification_artifact: Mapped[str | None] = mapped_column(String(500), nullable=True)
    execution_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("run_id = lower(run_id)"),
        CheckConstraint(
            "substr(run_id, 1, instr(run_id || '.', '.') - 1) NOT IN "
            "('con', 'prn', 'aux', 'nul', 'com1', 'com2', 'com3', 'com4', 'com5', "
            "'com6', 'com7', 'com8', 'com9', 'lpt1', 'lpt2', 'lpt3', 'lpt4', 'lpt5', "
            "'lpt6', 'lpt7', 'lpt8', 'lpt9')"
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0"),
        CheckConstraint("input_tokens >= 0"),
        CheckConstraint("output_tokens >= 0"),
        CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0"),
        CheckConstraint("tool_calls >= 0"),
        CheckConstraint("files_read >= 0"),
        CheckConstraint("lines_exposed >= 0"),
    )

    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="RESTRICT"), index=True
    )
    configuration_id: Mapped[str] = mapped_column(String(50), index=True)
    model: Mapped[str] = mapped_column(String(200))
    model_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(50), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    files_read: Mapped[int] = mapped_column(Integer, default=0)
    lines_exposed: Mapped[int] = mapped_column(Integer, default=0)
    repair_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    final_resolution: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(100), nullable=True)


class FaultLocalizationResult(Base):
    __tablename__ = "fault_localization_results"
    __table_args__ = (
        CheckConstraint("top_k >= 1"),
        CheckConstraint("fault_rank_if_known IS NULL OR fault_rank_if_known >= 1"),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    metric: Mapped[str] = mapped_column(String(50))
    ranked_locations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    top_k: Mapped[int] = mapped_column(Integer)
    fault_rank_if_known: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coverage_artifact: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PatchArtifact(Base):
    __tablename__ = "patch_artifacts"
    __table_args__ = (
        CheckConstraint("attempt_number BETWEEN 1 AND 2"),
        CheckConstraint("lines_added >= 0"),
        CheckConstraint("lines_removed >= 0"),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    unified_diff: Mapped[str] = mapped_column(Text)
    files_changed: Mapped[list[str]] = mapped_column(JSON, default=list)
    lines_added: Mapped[int] = mapped_column(Integer, default=0)
    lines_removed: Mapped[int] = mapped_column(Integer, default=0)
    applied_successfully: Mapped[bool] = mapped_column(Boolean, default=False)


class VerificationResult(Base):
    __tablename__ = "verification_results"
    __table_args__ = (
        CheckConstraint("attempt_number BETWEEN 1 AND 2"),
        CheckConstraint("duration_ms >= 0"),
    )

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    gate: Mapped[str] = mapped_column(String(100), primary_key=True)
    required: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(50))
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    baseline_difference: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    log_artifact: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Counterexample(Base):
    __tablename__ = "counterexamples"
    __table_args__ = (CheckConstraint("attempt_number BETWEEN 1 AND 2"),)

    counterexample_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(100))
    gate: Mapped[str] = mapped_column(String(100))
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_summary: Mapped[str] = mapped_column(Text)
    failure_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    location_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_new_vs_baseline: Mapped[bool] = mapped_column(Boolean)
    log_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    sanitized_feedback: Mapped[str] = mapped_column(Text)


class TraceEvent(Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence_number"),
        CheckConstraint("sequence_number >= 0"),
    )

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    parent_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("trace_events.event_id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    status: Mapped[str] = mapped_column(String(50))
    input_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
