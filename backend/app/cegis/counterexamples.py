"""Typed, bounded counterexample extraction for the CEGIS repair protocol.

The extractor consumes only Phase 6's normalized verification evidence.  It
never reads evaluator source or raw hidden-test output.  In particular, hidden
test identifiers (including their opaque hashes) are deliberately omitted from
the feedback returned to a model.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Counterexample as CounterexampleRecord
from app.db.models import Run
from app.schemas.common import validate_repository_path
from app.schemas.research import Counterexample
from app.verification.service import NormalizedGate

if TYPE_CHECKING:
    from app.verification.service import VerificationRun

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PRIVATE_PATH = re.compile(
    r"(?i)(?:[a-z]:)?[/\\](?:[^\s:/\\]+[/\\])*(?:hidden_tests|\.agenttrace-evaluator)"
    r"(?:[/\\][^\s:]*)?"
)
_EXCEPTION_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,199}$")
_MAX_SUMMARY_CHARS = 1_000
_MAX_LOG_CHARS = 1_000
_MAX_HINTS = 10


class CounterexampleSource(StrEnum):
    """Stable source labels used by the research dataset."""

    PYTEST_FAILURE = "PYTEST_FAILURE"
    HIDDEN_TEST_FAILURE = "HIDDEN_TEST_FAILURE"
    HYPOTHESIS_COUNTEREXAMPLE = "HYPOTHESIS_COUNTEREXAMPLE"
    REGRESSION = "REGRESSION"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    TYPE_FAILURE = "TYPE_FAILURE"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    CROSSHAIR_COUNTEREXAMPLE = "CROSSHAIR_COUNTEREXAMPLE"


class CounterexampleExtractionError(RuntimeError):
    """Normalized evidence cannot be attached to the requested experiment run."""


class CounterexampleExtractor:
    """Extract and persist at most one sanitized counterexample per patch attempt."""

    def __init__(self, session: Session, *, max_feedback_chars: int = 4_000) -> None:
        if not 512 <= max_feedback_chars <= 20_000:
            raise ValueError("max_feedback_chars must be between 512 and 20000")
        self.session = session
        self.max_feedback_chars = max_feedback_chars

    def extract(
        self,
        run_id: str,
        attempt_number: int,
        verification: VerificationRun,
    ) -> Counterexample | None:
        """Persist the first genuine required candidate failure, if one exists.

        A verifier infrastructure result uses ``resolved=None``.  Such results,
        successful verification, baseline gates, skipped gates, and normalized
        ``error`` statuses cannot become software counterexamples.
        """

        if verification.run_id != run_id or verification.attempt_number != attempt_number:
            raise CounterexampleExtractionError("verification identity differs from the request")
        if verification.resolved is not False:
            return None
        failure = next(
            (
                gate
                for gate in verification.results
                if gate.required
                and not gate.gate.startswith("baseline_")
                and gate.status in {"counterexample_found", "failed", "timed_out"}
            ),
            None,
        )
        if failure is None:
            return None
        return self.extract_gate(run_id, attempt_number, failure)

    def extract_gate(
        self,
        run_id: str,
        attempt_number: int,
        failure: NormalizedGate,
    ) -> Counterexample | None:
        """Extract one explicit normalized failure.

        This narrower entry point supports optional symbolic/type evidence in a
        later configuration without allowing advisory gates to trigger repair
        implicitly in Configuration C.
        """

        if failure.gate.startswith("baseline_") or failure.status not in {
            "failed",
            "timed_out",
            "counterexample_found",
        }:
            return None
        if self.session.get(Run, run_id) is None:
            raise CounterexampleExtractionError("counterexample run does not exist")
        existing = self.session.scalar(
            select(CounterexampleRecord).where(
                CounterexampleRecord.run_id == run_id,
                CounterexampleRecord.attempt_number == attempt_number,
            )
        )
        if existing is not None:
            return Counterexample.model_validate(existing)

        evidence = _extract_evidence(failure)
        if evidence is None:
            return None
        source, input_summary, expected, observed, failure_type, hints, is_new, excerpt = evidence
        counterexample_id = _counterexample_id(run_id, attempt_number, failure.gate)
        feedback = _render_feedback(
            source=source,
            gate=failure.gate,
            input_summary=input_summary,
            expected_summary=expected,
            observed_summary=observed,
            failure_type=failure_type,
            location_hints=hints,
            is_new_vs_baseline=is_new,
            log_excerpt=excerpt,
            max_chars=self.max_feedback_chars,
        )
        counterexample = Counterexample(
            counterexample_id=counterexample_id,
            run_id=run_id,
            attempt_number=attempt_number,
            source=source.value,
            gate=failure.gate,
            input_summary=input_summary,
            expected_summary=expected,
            observed_summary=observed,
            failure_type=failure_type,
            location_hints=list(hints),
            is_new_vs_baseline=is_new,
            log_excerpt=excerpt,
            sanitized_feedback=feedback,
        )
        self.session.add(CounterexampleRecord(**counterexample.model_dump()))
        self.session.flush()
        return counterexample


def _extract_evidence(
    failure: NormalizedGate,
) -> (
    tuple[
        CounterexampleSource,
        str | None,
        str | None,
        str,
        str | None,
        tuple[str, ...],
        bool,
        str | None,
    ]
    | None
):
    detail = failure.baseline_difference or {}
    is_new = _is_new_vs_baseline(detail)

    if failure.gate == "hidden_tests":
        failed_count = _bounded_count(detail.get("failed"))
        observed = (
            "The hidden correctness gate timed out."
            if failure.status == "timed_out"
            else f"The hidden correctness gate failed ({failed_count} failing test(s))."
        )
        # Neither private identifiers nor caller-provided log content is used.
        return (
            CounterexampleSource.HIDDEN_TEST_FAILURE,
            None,
            "Satisfy the task's externally observable behavior without regressions.",
            observed,
            "Timeout" if failure.status == "timed_out" else "HiddenTestFailure",
            (),
            is_new,
            None,
        )

    if failure.gate == "hypothesis_properties":
        item = _first_mapping(detail.get("counterexamples"))
        if item is None:
            return (
                CounterexampleSource.HYPOTHESIS_COUNTEREXAMPLE,
                None,
                "Satisfy the configured behavioral property.",
                _clean_text(failure.summary, _MAX_SUMMARY_CHARS),
                "PropertyFailure",
                (),
                is_new,
                _safe_excerpt(failure.summary),
            )
        return (
            CounterexampleSource.HYPOTHESIS_COUNTEREXAMPLE,
            _optional_text(item.get("input_summary")),
            _optional_text(item.get("expected_summary")),
            _clean_text(item.get("observed_summary"), _MAX_SUMMARY_CHARS),
            _failure_type(item.get("exception_type"), "PropertyFailure"),
            _location_hints(item.get("location_hints")),
            is_new,
            _safe_excerpt(failure.summary),
        )

    if failure.gate == "symbolic":
        item = _first_mapping(detail.get("counterexamples"))
        if item is None:
            return None
        return (
            CounterexampleSource.CROSSHAIR_COUNTEREXAMPLE,
            None,
            "Satisfy the configured function contract.",
            _clean_text(item.get("observed_summary"), _MAX_SUMMARY_CHARS),
            "ContractViolation",
            _location_hints([item.get("location_hint")]),
            is_new,
            _safe_excerpt(failure.summary),
        )

    if failure.gate == "python_compile":
        return _ordinary_evidence(
            failure,
            CounterexampleSource.SYNTAX_ERROR,
            "Valid Python syntax",
            "SyntaxError",
            is_new,
        )
    if failure.gate == "mypy":
        return _ordinary_evidence(
            failure,
            CounterexampleSource.TYPE_FAILURE,
            "The configured type checks pass",
            "TypeCheckFailure",
            is_new,
        )
    if failure.gate in {"patch_applied", "bandit"}:
        # Patch-policy diagnostics are intentionally generic: a rejected patch
        # can mention evaluator-protected paths in its internal error.
        return (
            CounterexampleSource.POLICY_VIOLATION,
            None,
            "Submit a valid unified diff that obeys repository and patch policy.",
            "The candidate patch was rejected by an AgentTrace policy gate.",
            "PolicyViolation",
            (),
            is_new,
            None,
        )

    new_failures = _safe_public_test_ids(detail.get("new_failures"))
    if failure.gate == "existing_tests" or new_failures:
        observed = "A previously passing test now fails."
        if new_failures:
            observed = f"Previously passing tests now fail: {', '.join(new_failures)}"
        return (
            CounterexampleSource.REGRESSION,
            None,
            "Preserve behavior that passed at the base commit.",
            _clean_text(observed, _MAX_SUMMARY_CHARS),
            "Regression",
            (),
            True,
            _safe_excerpt(failure.summary),
        )

    if failure.gate in {"visible_tests", "ruff"}:
        return _ordinary_evidence(
            failure,
            CounterexampleSource.PYTEST_FAILURE,
            "The required test behavior passes",
            "Timeout" if failure.status == "timed_out" else "TestFailure",
            is_new,
        )
    return None


def _ordinary_evidence(
    failure: NormalizedGate,
    source: CounterexampleSource,
    expected: str,
    failure_type: str,
    is_new: bool,
) -> tuple[
    CounterexampleSource,
    None,
    str,
    str,
    str,
    tuple[()],
    bool,
    str | None,
]:
    observed = (
        "The verification gate exceeded its bounded execution time."
        if failure.status == "timed_out"
        else _clean_text(failure.summary, _MAX_SUMMARY_CHARS)
    )
    return (
        source,
        None,
        expected,
        observed,
        failure_type,
        (),
        is_new,
        _safe_excerpt(failure.summary),
    )


def _render_feedback(
    *,
    source: CounterexampleSource,
    gate: str,
    input_summary: str | None,
    expected_summary: str | None,
    observed_summary: str,
    failure_type: str | None,
    location_hints: tuple[str, ...],
    is_new_vs_baseline: bool,
    log_excerpt: str | None,
    max_chars: int,
) -> str:
    payload: dict[str, Any] = {
        "instruction": (
            "Produce one complete replacement patch against the original base commit; "
            "do not return an incremental patch against the failed candidate."
        ),
        "source": source.value,
        "failed_gate": gate,
        "input_summary": input_summary,
        "expected_behavior": expected_summary,
        "observed_behavior": observed_summary,
        "failure_type": failure_type,
        "location_hints": list(location_hints),
        "new_vs_baseline": is_new_vs_baseline,
        "safe_log_excerpt": log_excerpt,
    }
    rendered = _compact_json(payload)
    if len(rendered) <= max_chars:
        return rendered

    # Preserve the semantic core while deterministically dropping optional
    # detail.  The result remains valid JSON rather than a truncated fragment.
    payload["safe_log_excerpt"] = None
    payload["location_hints"] = list(location_hints[:3])
    for key in ("input_summary", "expected_behavior", "observed_behavior"):
        value = payload[key]
        if isinstance(value, str):
            payload[key] = _clean_text(value, 250)
    rendered = _compact_json(payload)
    if len(rendered) <= max_chars:
        return rendered
    payload["location_hints"] = []
    payload["input_summary"] = _optional_short(payload["input_summary"], 100)
    payload["expected_behavior"] = _optional_short(payload["expected_behavior"], 100)
    payload["observed_behavior"] = _clean_text(payload["observed_behavior"], 150)
    rendered = _compact_json(payload)
    if len(rendered) > max_chars:  # Defensive for unusually small configured bounds.
        payload = {
            "source": source.value,
            "failed_gate": gate,
            "observed_behavior": _clean_text(observed_summary, 100),
            "new_vs_baseline": is_new_vs_baseline,
            "instruction": "Submit one replacement patch against the original base commit.",
        }
        rendered = _compact_json(payload)
    if len(rendered) > max_chars:
        raise CounterexampleExtractionError("counterexample feedback cannot fit its bound")
    return rendered


def _counterexample_id(run_id: str, attempt_number: int, gate: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{attempt_number}\0{gate}".encode()).hexdigest()[:20]
    return f"ce-{digest}"


def _compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _is_new_vs_baseline(detail: dict[str, Any]) -> bool:
    if detail.get("baseline_status") == "passed":
        return True
    failures = detail.get("new_failures")
    return isinstance(failures, list) and bool(failures)


def _bounded_count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1_000_000:
        return value
    return 0


def _first_mapping(value: object) -> dict[str, Any] | None:
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        return None
    return value[0]


def _optional_text(value: object) -> str | None:
    return None if value is None else _clean_text(value, _MAX_SUMMARY_CHARS)


def _optional_short(value: object, limit: int) -> str | None:
    return None if value is None else _clean_text(value, limit)


def _failure_type(value: object, default: str) -> str:
    if isinstance(value, str) and _EXCEPTION_TYPE.fullmatch(value):
        return value
    return default


def _safe_excerpt(value: object) -> str | None:
    excerpt = _clean_text(value, _MAX_LOG_CHARS)
    return excerpt or None


def _clean_text(value: object, limit: int) -> str:
    text = value if isinstance(value, str) else str(value)
    text = _CONTROL_CHARACTERS.sub(" ", text).replace("\r", " ").replace("\n", " ")
    text = _PRIVATE_PATH.sub("[private evaluator path]", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _location_hints(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    hints: list[str] = []
    for item in value:
        if not isinstance(item, str) or len(item) > 500:
            continue
        path, separator, line = item.rpartition(":")
        lowered = path.casefold().replace("\\", "/")
        if (
            not separator
            or not line.isdigit()
            or int(line) < 1
            or lowered.startswith("/")
            or "hidden_tests" in lowered.split("/")
            or ".agenttrace-evaluator" in lowered.split("/")
        ):
            continue
        try:
            safe_path = validate_repository_path(path)
        except ValueError:
            continue
        normalized = f"{safe_path}:{int(line)}"
        if normalized not in hints:
            hints.append(normalized)
        if len(hints) == _MAX_HINTS:
            break
    return tuple(hints)


def _safe_public_test_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    identifiers: list[str] = []
    for item in value[:5]:
        if not isinstance(item, str):
            continue
        safe = _clean_text(item, 200)
        if safe and "hidden" not in safe.casefold():
            identifiers.append(safe)
    return tuple(identifiers)
