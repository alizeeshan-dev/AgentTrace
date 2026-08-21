"""Typed mutation-qualification results independent of mutmut internals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MutationCounts:
    """Normalized mutant classifications used by AgentTrace.

    ``excluded`` includes explicit equivalent-mutant exclusions and mutants
    rejected by mutmut's optional type-check filter. ``unusable`` covers
    statuses that cannot contribute to the research score, such as timeouts,
    crashes, missing test associations, and interrupted/not-run mutants.
    These categories are deliberately not folded into ``survived``.
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
    """A completed mutmut run plus the evidence needed for reproduction."""

    counts: MutationCounts
    tool: str
    tool_version: str
    commands: tuple[tuple[str, ...], ...]
    config_sha256: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    platform: str
    python_version: str
    run_stdout: str
    run_stderr: str
    export_stdout: str
    export_stderr: str
    results_output: str
    raw_stats_json: str


@dataclass(frozen=True, slots=True)
class MutationEnvironment:
    """Availability of the fork-capable mutmut qualification environment."""

    available: bool
    executable: str | None
    reason: str | None

