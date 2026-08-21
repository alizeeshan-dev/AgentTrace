"""Reproducible experiment planning, execution, and failure classification."""

from .failures import (
    FailureCategory,
    FailureClassification,
    ManualFailureAnnotation,
    classify_run,
)
from .loader import load_experiment_config
from .models import ExperimentConfig, ExperimentConfigurationSpec
from .runner import (
    ExperimentOutcome,
    ExperimentRunner,
    ExperimentRunnerError,
    ExperimentSlot,
    SlotOutcome,
    SlotStatus,
    stable_run_id,
)
from .storage import ExperimentDataLayout

__all__ = [
    "ExperimentConfig",
    "ExperimentConfigurationSpec",
    "ExperimentDataLayout",
    "ExperimentOutcome",
    "ExperimentRunner",
    "ExperimentRunnerError",
    "ExperimentSlot",
    "FailureCategory",
    "FailureClassification",
    "ManualFailureAnnotation",
    "SlotOutcome",
    "SlotStatus",
    "classify_run",
    "load_experiment_config",
    "stable_run_id",
]
