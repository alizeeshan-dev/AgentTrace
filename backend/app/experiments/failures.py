"""Frozen Phase 9 failure labels and conservative terminal classification."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Run, VerificationResult
from app.schemas.common import ResearchSchema


class FailureCategory(StrEnum):
    MISUNDERSTOOD_REQUIREMENT = "MISUNDERSTOOD_REQUIREMENT"
    INSUFFICIENT_REPOSITORY_INSPECTION = "INSUFFICIENT_REPOSITORY_INSPECTION"
    FAULT_LOCALIZATION_MISLEADING = "FAULT_LOCALIZATION_MISLEADING"
    HALLUCINATED_PATH_OR_SYMBOL = "HALLUCINATED_PATH_OR_SYMBOL"
    INVALID_PATCH = "INVALID_PATCH"
    PATCH_DID_NOT_APPLY = "PATCH_DID_NOT_APPLY"
    VISIBLE_TEST_FAILURE = "VISIBLE_TEST_FAILURE"
    HIDDEN_TEST_FAILURE = "HIDDEN_TEST_FAILURE"
    PROPERTY_FAILURE = "PROPERTY_FAILURE"
    REGRESSION = "REGRESSION"
    LINT_FAILURE = "LINT_FAILURE"
    TYPE_FAILURE = "TYPE_FAILURE"
    SECURITY_WARNING = "SECURITY_WARNING"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    CROSSHAIR_COUNTEREXAMPLE = "CROSSHAIR_COUNTEREXAMPLE"
    REPAIR_FAILED = "REPAIR_FAILED"
    REPAIR_INTRODUCED_REGRESSION = "REPAIR_INTRODUCED_REGRESSION"
    TOOL_MISUSE = "TOOL_MISUSE"
    TOOL_BUDGET_EXHAUSTED = "TOOL_BUDGET_EXHAUSTED"
    EXCESSIVE_CHANGE = "EXCESSIVE_CHANGE"
    TIMEOUT = "TIMEOUT"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    MODEL_PROVIDER_FAILURE = "MODEL_PROVIDER_FAILURE"


class FailureClassification(ResearchSchema):
    """One primary terminal label plus independently supported secondary labels."""

    primary: FailureCategory | None
    secondary: list[FailureCategory] = Field(default_factory=list)
    source: str = "automatic"
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def labels_are_distinct(self) -> FailureClassification:
        if len(set(self.secondary)) != len(self.secondary):
            raise ValueError("secondary failure labels must be unique")
        if self.primary is not None and self.primary in self.secondary:
            raise ValueError("the primary label cannot also be secondary")
        return self


class ManualFailureAnnotation(ResearchSchema):
    """Derived human interpretation; it never mutates immutable raw run output."""

    run_id: str
    annotator: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ]
    primary: FailureCategory | None = None
    secondary: list[FailureCategory] = Field(default_factory=list)
    evidence: Annotated[list[str], Field(min_length=1, max_length=50)]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)]

    @model_validator(mode="after")
    def at_least_one_label(self) -> ManualFailureAnnotation:
        if self.primary is None and not self.secondary:
            raise ValueError("manual annotation must assign at least one label")
        if len(set(self.secondary)) != len(self.secondary):
            raise ValueError("manual secondary labels must be unique")
        if self.primary is not None and self.primary in self.secondary:
            raise ValueError("manual primary cannot also be secondary")
        return self


_GATE_CATEGORY = {
    "patch_applied": FailureCategory.PATCH_DID_NOT_APPLY,
    "python_compile": FailureCategory.INVALID_PATCH,
    "visible_tests": FailureCategory.VISIBLE_TEST_FAILURE,
    "existing_tests": FailureCategory.REGRESSION,
    "hidden_tests": FailureCategory.HIDDEN_TEST_FAILURE,
    "hypothesis_properties": FailureCategory.PROPERTY_FAILURE,
    "symbolic": FailureCategory.CROSSHAIR_COUNTEREXAMPLE,
    "ruff": FailureCategory.LINT_FAILURE,
    "mypy": FailureCategory.TYPE_FAILURE,
    "bandit": FailureCategory.SECURITY_WARNING,
}

_PRIMARY_PRECEDENCE = (
    FailureCategory.INFRASTRUCTURE_FAILURE,
    FailureCategory.MODEL_PROVIDER_FAILURE,
    FailureCategory.POLICY_VIOLATION,
    FailureCategory.REPAIR_INTRODUCED_REGRESSION,
    FailureCategory.REGRESSION,
    FailureCategory.PATCH_DID_NOT_APPLY,
    FailureCategory.INVALID_PATCH,
    FailureCategory.TIMEOUT,
    FailureCategory.HIDDEN_TEST_FAILURE,
    FailureCategory.PROPERTY_FAILURE,
    FailureCategory.CROSSHAIR_COUNTEREXAMPLE,
    FailureCategory.VISIBLE_TEST_FAILURE,
    FailureCategory.REPAIR_FAILED,
    FailureCategory.TOOL_BUDGET_EXHAUSTED,
    FailureCategory.TOOL_MISUSE,
    FailureCategory.LINT_FAILURE,
    FailureCategory.TYPE_FAILURE,
    FailureCategory.SECURITY_WARNING,
)


def classify_run(session: Session, run: Run) -> FailureClassification:
    """Classify from terminal structured evidence without speculative diagnosis."""

    if run.final_resolution is True:
        labels = _gate_labels(session, run.run_id, advisory_only=True)
        return FailureClassification(primary=None, secondary=labels, evidence=[])

    supported: set[FailureCategory] = set()
    evidence: list[str] = []
    if run.failure_category is not None:
        try:
            supported.add(FailureCategory(run.failure_category))
            evidence.append("runs.failure_category")
        except ValueError:
            # Older free-form labels remain visible in raw data but are not
            # silently converted into a research taxonomy label.
            pass
    status = run.status.casefold()
    if "infrastructure" in status:
        supported.add(FailureCategory.INFRASTRUCTURE_FAILURE)
    if "provider" in status:
        supported.add(FailureCategory.MODEL_PROVIDER_FAILURE)
    if "budget" in status:
        supported.add(FailureCategory.TOOL_BUDGET_EXHAUSTED)
    repair_metrics = run.model_parameters.get("repair_metrics")
    if isinstance(repair_metrics, dict) and repair_metrics.get("repair_induced_regression") is True:
        supported.add(FailureCategory.REPAIR_INTRODUCED_REGRESSION)
    elif run.repair_attempted and run.final_resolution is False:
        supported.add(FailureCategory.REPAIR_FAILED)
    supported.update(_gate_labels(session, run.run_id, advisory_only=False))
    gates = session.scalars(
        select(VerificationResult).where(VerificationResult.run_id == run.run_id)
    ).all()
    if any(gate.status == "timed_out" for gate in gates):
        supported.add(FailureCategory.TIMEOUT)
    if any(
        gate.baseline_difference and bool(gate.baseline_difference.get("regression"))
        for gate in gates
    ):
        if any(gate.attempt_number == 2 for gate in gates) and run.repair_attempted:
            supported.add(FailureCategory.REPAIR_INTRODUCED_REGRESSION)
        else:
            supported.add(FailureCategory.REGRESSION)
    ordered = [label for label in _PRIMARY_PRECEDENCE if label in supported]
    if not ordered:
        return FailureClassification(primary=None, evidence=evidence)
    return FailureClassification(
        primary=ordered[0],
        secondary=ordered[1:],
        evidence=[*evidence, "verification_results"] if gates else evidence,
    )


def _gate_labels(
    session: Session,
    run_id: str,
    *,
    advisory_only: bool,
) -> list[FailureCategory]:
    records = session.scalars(
        select(VerificationResult).where(VerificationResult.run_id == run_id)
    ).all()
    labels: set[FailureCategory] = set()
    for record in records:
        failed = record.status in {"failed", "timed_out", "counterexample_found"}
        if not failed or (advisory_only and record.required):
            continue
        label = _GATE_CATEGORY.get(record.gate)
        if label is not None:
            labels.add(label)
    return [label for label in _PRIMARY_PRECEDENCE if label in labels]
