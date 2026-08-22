"""Typed mutation-qualification results independent of tool internals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MutationCounts:
    """Normalized mutant classifications used by AgentTrace.

    ``excluded`` includes explicit equivalent-mutation exclusions, tool-level
    pardons, and invalid mutations. ``unusable`` covers statuses that cannot
    contribute to the normalized research score, such as timeouts. These
    categories are deliberately not folded into ``survived``.
    """

    generated: int
    killed: int
    survived: int
    excluded: int
    skipped: int
    invalid: int
    unusable: int
    mutation_score: float | None
    completed: bool
    status_counts: dict[str, int]
    exclusion_reasons: dict[str, str]


@dataclass(frozen=True, slots=True)
class MutationExecution:
    """A completed mutation-tool run plus reproducibility evidence."""

    counts: MutationCounts
    tool: str
    tool_version: str
    tool_reported_score: float | None
    commands: tuple[tuple[str, ...], ...]
    config_sha256: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    platform: str
    python_version: str
    run_stdout: str
    run_stderr: str
    report_relative_path: str
    raw_report_json: str


@dataclass(frozen=True, slots=True)
class MutationEnvironment:
    """Availability of the configured mutation qualification environment."""

    available: bool
    executable: str | None
    reason: str | None
