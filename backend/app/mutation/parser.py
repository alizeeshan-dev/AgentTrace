"""Normalize pytest-gremlins JSON reports for AgentTrace research metrics."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.mutation.models import MutationCounts

PYTEST_GREMLINS_STATUSES = frozenset(
    {"zapped", "survived", "timeout", "error", "pardoned"}
)
_REQUIRED_SUMMARY_FIELDS = frozenset(
    {"total", "zapped", "survived", "timeout", "error", "pardoned", "percentage"}
)


class MutationParseError(ValueError):
    """Raised when mutation evidence is incomplete or internally inconsistent."""


def calculate_mutation_score(killed: int, survived: int) -> float | None:
    """Return AgentTrace's normalized ``killed / (killed + survived)`` score."""

    if isinstance(killed, bool) or isinstance(survived, bool):
        raise TypeError("Mutation counts must be integers, not booleans")
    if not isinstance(killed, int) or not isinstance(survived, int):
        raise TypeError("Mutation counts must be integers")
    if killed < 0 or survived < 0:
        raise ValueError("Mutation counts cannot be negative")
    denominator = killed + survived
    return None if denominator == 0 else killed / denominator


def parse_gremlins_report(
    raw_report_json: str | bytes,
    *,
    manual_exclusions: Mapping[str, str] | None = None,
) -> MutationCounts:
    """Convert one pytest-gremlins JSON report into normalized classifications.

    AgentTrace deliberately scores only ordinary detected (``zapped``) and
    surviving mutations. ``pardoned`` mutations and reviewed manual
    exclusions are excluded; timeouts are unusable; execution errors are
    invalid exclusions. The tool-reported percentage is preserved in the raw
    artifact but is not substituted for this normalized research score.
    """

    report = _parse_report_object(raw_report_json)
    summary = _parse_summary(report.get("summary"))
    results = _parse_results(report.get("results"))

    if summary["total"] != len(results):
        raise MutationParseError(
            "pytest-gremlins total does not match per-gremlin result evidence"
        )

    status_counts = {status: 0 for status in sorted(PYTEST_GREMLINS_STATUSES)}
    statuses: dict[str, str] = {}
    for gremlin_id, status in results:
        statuses[gremlin_id] = status
        status_counts[status] += 1

    for status in PYTEST_GREMLINS_STATUSES:
        if summary[status] != status_counts[status]:
            raise MutationParseError(
                f"pytest-gremlins summary field {status!r} conflicts with result evidence"
            )

    reasons = _validate_exclusions(manual_exclusions or {}, statuses)
    manually_excluded = set(reasons)
    invalid_ids = {name for name, status in statuses.items() if status == "error"}
    pardoned_ids = {name for name, status in statuses.items() if status == "pardoned"}
    for name in sorted(pardoned_ids):
        reasons.setdefault(name, "pytest-gremlins pardoned this mutation")
    for name in sorted(invalid_ids):
        reasons.setdefault(name, "pytest-gremlins reported an execution error")

    killed = status_counts["zapped"] - sum(
        statuses[name] == "zapped" for name in manually_excluded
    )
    survived = status_counts["survived"] - sum(
        statuses[name] == "survived" for name in manually_excluded
    )
    excluded = len(manually_excluded | pardoned_ids | invalid_ids)

    return MutationCounts(
        generated=int(summary["total"]),
        killed=killed,
        survived=survived,
        excluded=excluded,
        skipped=0,
        invalid=len(invalid_ids),
        unusable=status_counts["timeout"],
        mutation_score=calculate_mutation_score(killed, survived),
        completed=True,
        status_counts=status_counts,
        exclusion_reasons=reasons,
    )


def _parse_report_object(raw_report_json: str | bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_report_json)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise MutationParseError("pytest-gremlins report is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise MutationParseError("pytest-gremlins report must be a JSON object")
    return parsed


def _parse_summary(value: object) -> dict[str, int | float]:
    if not isinstance(value, dict):
        raise MutationParseError("pytest-gremlins report must contain a summary object")
    missing = _REQUIRED_SUMMARY_FIELDS - set(value)
    if missing:
        raise MutationParseError(
            f"pytest-gremlins summary omits fields: {sorted(missing)}"
        )

    parsed: dict[str, int | float] = {}
    for field in _REQUIRED_SUMMARY_FIELDS - {"percentage"}:
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise MutationParseError(
                f"pytest-gremlins summary field {field!r} must be a non-negative integer"
            )
        parsed[field] = item
    percentage = value["percentage"]
    if (
        isinstance(percentage, bool)
        or not isinstance(percentage, (int, float))
        or not 0 <= percentage <= 100
    ):
        raise MutationParseError(
            "pytest-gremlins summary field 'percentage' must be between 0 and 100"
        )
    parsed["percentage"] = float(percentage)
    return parsed


def _parse_results(value: object) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        raise MutationParseError("pytest-gremlins report must contain a results array")
    parsed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise MutationParseError(
                f"pytest-gremlins result {index} must be a JSON object"
            )
        gremlin_id = item.get("gremlin_id")
        status = item.get("status")
        if (
            not isinstance(gremlin_id, str)
            or not gremlin_id.strip()
            or any(character in gremlin_id for character in "\x00\r\n")
        ):
            raise MutationParseError(
                f"pytest-gremlins result {index} has an invalid gremlin_id"
            )
        gremlin_id = gremlin_id.strip()
        if gremlin_id in seen:
            raise MutationParseError(
                f"pytest-gremlins report duplicates gremlin {gremlin_id!r}"
            )
        normalized_status = status.strip().lower() if isinstance(status, str) else ""
        if normalized_status not in PYTEST_GREMLINS_STATUSES:
            raise MutationParseError(
                f"pytest-gremlins result {gremlin_id!r} has unsupported status {status!r}"
            )
        seen.add(gremlin_id)
        parsed.append((gremlin_id, normalized_status))
    return parsed


def _validate_exclusions(
    requested: Mapping[str, str], statuses: Mapping[str, str]
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for name, reason in requested.items():
        if name not in statuses:
            raise MutationParseError(f"Excluded mutation {name!r} does not exist")
        if statuses[name] not in {"zapped", "survived"}:
            raise MutationParseError(
                f"Mutation {name!r} already has non-score status {statuses[name]!r}"
            )
        if not isinstance(reason, str) or not reason.strip() or "\x00" in reason:
            raise MutationParseError(f"Excluded mutation {name!r} needs a reason")
        validated[name] = reason.strip()
    return validated
