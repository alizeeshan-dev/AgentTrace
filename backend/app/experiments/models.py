"""Typed, portable experiment plans for reproducible A--D execution."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.agent.budgets import AgentBudgets
from app.configurations.models import (
    ExperimentCondition,
    ModelConfiguration,
    ResearchTechniques,
)
from app.schemas.common import FilesystemIdentifier, ResearchSchema, validate_repository_path

BenchmarkVersion = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$",
    ),
]


class ExperimentConfigurationSpec(ResearchSchema):
    """One named condition with its research switches made explicit."""

    condition: ExperimentCondition
    feature_flags: ResearchTechniques

    @field_validator("condition", mode="before")
    @classmethod
    def parse_condition(cls, value: object) -> ExperimentCondition:
        if isinstance(value, ExperimentCondition):
            return value
        if isinstance(value, str):
            return ExperimentCondition(value)
        raise ValueError("condition must be a named A--D experimental condition")

    @model_validator(mode="after")
    def flags_match_named_condition(self) -> ExperimentConfigurationSpec:
        expected = {
            "D": ResearchTechniques(
                enable_sbfl=True,
                enable_hypothesis=True,
                enable_crosshair=True,
            ),
            "D1": ResearchTechniques(enable_sbfl=True),
            "D2": ResearchTechniques(enable_hypothesis=True),
            "D3": ResearchTechniques(enable_crosshair=True),
        }.get(self.condition.value, ResearchTechniques())
        if self.feature_flags != expected:
            raise ValueError("feature_flags must match the frozen named experimental condition")
        return self


class ExperimentConfig(ResearchSchema):
    """Frozen cross-product definition loaded before an experiment starts."""

    schema_version: Literal[1] = 1
    experiment_id: FilesystemIdentifier
    benchmark_version: BenchmarkVersion
    tasks: Annotated[list[str], Field(min_length=1, max_length=10_000)]
    configurations: Annotated[list[ExperimentConfigurationSpec], Field(min_length=1, max_length=7)]
    model: ModelConfiguration
    budgets: AgentBudgets = Field(default_factory=AgentBudgets)
    max_repairs: Annotated[int, Field(ge=0, le=1)] = 1
    verification_profile: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$",
        ),
    ] = "deterministic-v1"

    @field_validator("tasks")
    @classmethod
    def tasks_are_portable_and_unique(cls, values: list[str]) -> list[str]:
        normalized = [validate_repository_path(value) for value in values]
        if any(not value.casefold().endswith((".yaml", ".yml")) for value in normalized):
            raise ValueError("experiment tasks must reference YAML manifests")
        if len(set(normalized)) != len(normalized):
            raise ValueError("experiment tasks must be unique")
        return normalized

    @field_validator("configurations")
    @classmethod
    def configurations_are_unique(
        cls, values: list[ExperimentConfigurationSpec]
    ) -> list[ExperimentConfigurationSpec]:
        conditions = [value.condition for value in values]
        if len(set(conditions)) != len(conditions):
            raise ValueError("experimental conditions must be unique")
        return values

    @model_validator(mode="after")
    def repair_ceiling_supports_selected_conditions(self) -> ExperimentConfig:
        needs_repair = any(
            item.condition not in {ExperimentCondition.A, ExperimentCondition.B}
            for item in self.configurations
        )
        if needs_repair and self.max_repairs != 1:
            raise ValueError("C/D conditions require max_repairs=1")
        return self
