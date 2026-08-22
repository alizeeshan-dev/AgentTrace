"""Deterministic native-Windows verification primitives."""

from .native import (
    NativeEnvironmentError,
    WindowsExecution,
    WindowsExecutionEnvironment,
    WindowsVerificationRunner,
)
from .gates import GateOutcome, GateSpec, StandardGateFactory, StandardGateRunner
from .service import (
    NormalizedGate,
    VerificationFeatures,
    VerificationRun,
    VerificationService,
    VerificationServiceError,
)

__all__ = [
    "NativeEnvironmentError",
    "GateOutcome",
    "GateSpec",
    "NormalizedGate",
    "StandardGateFactory",
    "StandardGateRunner",
    "VerificationFeatures",
    "VerificationRun",
    "VerificationService",
    "VerificationServiceError",
    "WindowsExecution",
    "WindowsExecutionEnvironment",
    "WindowsVerificationRunner",
]
