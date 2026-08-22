"""Mutation-testing integration used only during benchmark qualification."""

from app.mutation.adapter import (
    RECOMMENDED_PYTEST_GREMLINS_REQUIREMENT,
    SUPPORTED_PYTEST_GREMLINS_VERSION,
    MutationEnvironmentUnavailable,
    MutationExecutionError,
    PytestGremlinsAdapter,
    PytestGremlinsConfig,
    build_pytest_gremlins_commands,
    detect_pytest_gremlins_environment,
)
from app.mutation.models import MutationCounts, MutationEnvironment, MutationExecution
from app.mutation.parser import (
    MutationParseError,
    calculate_mutation_score,
    parse_gremlins_report,
)

__all__ = [
    "RECOMMENDED_PYTEST_GREMLINS_REQUIREMENT",
    "SUPPORTED_PYTEST_GREMLINS_VERSION",
    "MutationCounts",
    "MutationEnvironment",
    "MutationEnvironmentUnavailable",
    "MutationExecution",
    "MutationExecutionError",
    "MutationParseError",
    "PytestGremlinsAdapter",
    "PytestGremlinsConfig",
    "build_pytest_gremlins_commands",
    "calculate_mutation_score",
    "detect_pytest_gremlins_environment",
    "parse_gremlins_report",
]
