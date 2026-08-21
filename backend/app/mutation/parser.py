"""Parse the stable mutmut 3.7 CI statistics and status interfaces."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from app.mutation.models import MutationCounts

MUTMUT_STATUSES = frozenset(
    {
        "not checked",
        "killed",
        "survived",
        "no tests",
        "skipped",
        "suspicious",
        "timeout",
        "check was interrupted by user",
        "segfault",
        "caught by type check",
    }
)

_EXPORTED_FIELDS = frozenset(
    {
        "killed",
        "survived",
        "total",
        "no_tests",
        "skipped",
        "suspicious",
        "timeout",
        "check_was_interrupted_by_user",
        "segfault",
        # Accepted for forward-compatible evidence without changing semantics.
        "not_checked",
        "caught_by_type_check",
    }
)
_REQUIRED_EXPORTED_FIELDS = frozenset(
    {
        "killed",
        "survived",
        "total",
        "no_tests",
        "skipped",
        "suspicious",
        "timeout",
        "check_was_interrupted_by_user",
        "segfault",
    }
)
_STATUS_LINE = re.compile(r"^\s*(?P<name>\S.+?):\s+(?P<status>[^:\r\n]+?)\s*$")
_UNUSABLE_STATUSES = frozenset(
    {
        "not checked",
        "no tests",
        "suspicious",
        "timeout",
        "check was interrupted by user",
        "segfault",
    }
)


class MutationParseError(ValueError):
    """Raised when mutation evidence is incomplete or internally inconsistent."""


def calculate_mutation_score(killed: int, survived: int) -> float | None:
    """Return ``killed / (killed + survived)`` or ``None`` without usable mutants."""

    if isinstance(killed, bool) or isinstance(survived, bool):
        raise TypeError("Mutation counts must be integers, not booleans")
    if not isinstance(killed, int) or not isinstance(survived, int):
        raise TypeError("Mutation counts must be integers")
    if killed < 0 or survived < 0:
        raise ValueError("Mutation counts cannot be negative")
    denominator = killed + survived
    return None if denominator == 0 else killed / denominator


def parse_exported_stats(raw_json: str | bytes) -> dict[str, int]:
    """Parse ``mutmut export-cicd-stats`` output for the pinned 3.7 interface."""

    try:
        parsed = json.loads(raw_json)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise MutationParseError("mutmut CI statistics are not valid JSON") from error
    if not isinstance(parsed, dict):
        raise MutationParseError("mutmut CI statistics must be a JSON object")

    keys = set(parsed)
    missing = _REQUIRED_EXPORTED_FIELDS - keys
    unknown = keys - _EXPORTED_FIELDS
    if missing:
        raise MutationParseError(f"mutmut CI statistics omit fields: {sorted(missing)}")
    if unknown:
        raise MutationParseError(
            f"mutmut CI statistics contain unsupported fields: {sorted(unknown)}"
        )

    result: dict[str, int] = {}
    for key, value in parsed.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise MutationParseError(f"mutmut statistic {key!r} must be a non-negative integer")
        result[str(key)] = value
    return result


def parse_status_output(output: str) -> dict[str, str]:
    """Parse ``mutmut results --all`` into mutant-name/status pairs."""

    mutants: dict[str, str] = {}
    for line_number, line in enumerate(output.splitlines(), start=1):
        if not line.strip():
            continue
        match = _STATUS_LINE.fullmatch(line)
        if match is None:
            raise MutationParseError(f"Unrecognized mutmut status line {line_number}")
        name = match.group("name").strip()
        status = _normalize_status(match.group("status"))
        if status not in MUTMUT_STATUSES:
            raise MutationParseError(f"Unsupported mutmut status {status!r}")
        if name in mutants:
            raise MutationParseError(f"Duplicate mutmut result for {name!r}")
        mutants[name] = status
    if not mutants:
        raise MutationParseError("mutmut returned no per-mutant status evidence")
    return mutants


def parse_mutation_result(
    raw_stats_json: str | bytes,
    status_output: str,
    *,
    manual_exclusions: Mapping[str, str] | None = None,
) -> MutationCounts:
    """Reconcile mutmut's JSON totals with per-mutant classifications.

    Manual exclusions are intended for reviewed equivalent mutants. They must
    name a killed or survived mutant and include a non-empty reason, ensuring
    exclusions remain auditable rather than silently changing the score.
    """

    exported = parse_exported_stats(raw_stats_json)
    statuses = parse_status_output(status_output)
    if exported["total"] != len(statuses):
        raise MutationParseError("mutmut total does not match per-mutant status evidence")

    status_counts = {status: 0 for status in sorted(MUTMUT_STATUSES)}
    for status in statuses.values():
        status_counts[status] += 1
    for exported_key, status in _exported_statuses().items():
        if exported_key in exported and exported[exported_key] != status_counts[status]:
            raise MutationParseError(
                f"mutmut field {exported_key!r} conflicts with per-mutant status evidence"
            )

    reasons = _validate_exclusions(manual_exclusions or {}, statuses)
    explicitly_excluded = set(reasons)
    invalid_names = {
        name for name, status in statuses.items() if status == "caught by type check"
    }
    for name in invalid_names:
        reasons.setdefault(name, "mutmut type-check filter rejected the mutant")

    killed = status_counts["killed"] - sum(
        statuses[name] == "killed" for name in explicitly_excluded
    )
    survived = status_counts["survived"] - sum(
        statuses[name] == "survived" for name in explicitly_excluded
    )
    unusable = sum(status_counts[status] for status in _UNUSABLE_STATUSES)
    completed = (
        status_counts["not checked"] == 0
        and status_counts["check was interrupted by user"] == 0
    )

    return MutationCounts(
        generated=exported["total"],
        killed=killed,
        survived=survived,
        excluded=len(reasons),
        skipped=status_counts["skipped"],
        invalid=len(invalid_names),
        unusable=unusable,
        mutation_score=calculate_mutation_score(killed, survived),
        completed=completed,
        status_counts=status_counts,
        exclusion_reasons=reasons,
    )


def _normalize_status(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def _exported_statuses() -> dict[str, str]:
    return {
        "killed": "killed",
        "survived": "survived",
        "no_tests": "no tests",
        "skipped": "skipped",
        "suspicious": "suspicious",
        "timeout": "timeout",
        "check_was_interrupted_by_user": "check was interrupted by user",
        "segfault": "segfault",
        "not_checked": "not checked",
        "caught_by_type_check": "caught by type check",
    }


def _validate_exclusions(
    requested: Mapping[str, str], statuses: Mapping[str, str]
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for name, reason in requested.items():
        if name not in statuses:
            raise MutationParseError(f"Excluded mutant {name!r} does not exist")
        if statuses[name] not in {"killed", "survived"}:
            raise MutationParseError(
                f"Mutant {name!r} already has non-score status {statuses[name]!r}"
            )
        if not isinstance(reason, str) or not reason.strip() or "\x00" in reason:
            raise MutationParseError(f"Excluded mutant {name!r} needs a reason")
        validated[name] = reason.strip()
    return validated
