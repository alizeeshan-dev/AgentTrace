"""Reproducible experiment planning, execution, and failure classification."""

from .environment import (
    EnvironmentManifestError,
    WindowsEnvironmentManifest,
    WindowsToolVersions,
    build_windows_environment_manifest,
    load_windows_environment_manifest,
    verify_environment_fingerprint,
    write_windows_environment_manifest,
)
from .failures import (
    FailureCategory,
    FailureClassification,
    ManualFailureAnnotation,
    classify_run,
)
from .loader import load_experiment_config
from .models import (
    CrossHairExperimentSettings,
    ExperimentConfig,
    ExperimentConfigurationSpec,
    ExperimentCostConfiguration,
    ExperimentOutputLocations,
    FrozenWindowsEnvironment,
    HypothesisExperimentSettings,
    SbflExperimentSettings,
)
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
    "ExperimentCostConfiguration",
    "ExperimentDataLayout",
    "ExperimentOutcome",
    "ExperimentRunner",
    "ExperimentRunnerError",
    "ExperimentSlot",
    "ExperimentOutputLocations",
    "EnvironmentManifestError",
    "FailureCategory",
    "FailureClassification",
    "FrozenWindowsEnvironment",
    "HypothesisExperimentSettings",
    "ManualFailureAnnotation",
    "SlotOutcome",
    "SlotStatus",
    "SbflExperimentSettings",
    "CrossHairExperimentSettings",
    "WindowsEnvironmentManifest",
    "WindowsToolVersions",
    "build_windows_environment_manifest",
    "classify_run",
    "load_experiment_config",
    "load_windows_environment_manifest",
    "stable_run_id",
    "verify_environment_fingerprint",
    "write_windows_environment_manifest",
]
