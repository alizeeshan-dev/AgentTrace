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
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class FrozenWindowsEnvironment(ResearchSchema):
    """Reference to the separately materialized native Windows manifest."""

    runner: Literal["native_windows"] = "native_windows"
    manifest_path: str
    environment_id: FilesystemIdentifier
    fingerprint_sha256: Sha256Digest

    @field_validator("manifest_path")
    @classmethod
    def manifest_is_portable_json(cls, value: str) -> str:
        normalized = validate_repository_path(value)
        if not normalized.casefold().endswith(".json"):
            raise ValueError("environment manifest must be a JSON artifact")
        return normalized


class ExperimentOutputLocations(ResearchSchema):
    """Raw and derived namespaces remain physically distinct."""

    raw: str = "raw/"
    derived: str = "derived/"

    @field_validator("raw", "derived")
    @classmethod
    def output_path_is_portable_directory(cls, value: str) -> str:
        normalized = validate_repository_path(value)
        return normalized if normalized.endswith("/") else f"{normalized}/"

    @model_validator(mode="after")
    def raw_and_derived_are_distinct(self) -> ExperimentOutputLocations:
        if self.raw.casefold() == self.derived.casefold():
            raise ValueError("raw and derived output locations must differ")
        return self


class SbflExperimentSettings(ResearchSchema):
    metric: Literal["ochiai"] = "ochiai"
    top_k: Annotated[int, Field(ge=1, le=100)] = 10


class HypothesisExperimentSettings(ResearchSchema):
    enabled_for_eligible_tasks: bool = True
    derandomize: Literal[True] = True
    example_database: Literal["disabled"] = "disabled"


class CrossHairExperimentSettings(ResearchSchema):
    enabled_for_eligible_tasks: bool = True
    no_counterexample_is_proof: Literal[False] = False


class ExperimentCostConfiguration(ResearchSchema):
    """Frozen provider pricing used only for derived cost calculations."""

    currency: Literal["USD"] = "USD"
    input_per_million_tokens: float | None = Field(default=None, ge=0)
    output_per_million_tokens: float | None = Field(default=None, ge=0)
    source: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
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

    schema_version: Literal[1, 2] = 1
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
    environment: FrozenWindowsEnvironment | None = None
    outputs: ExperimentOutputLocations | None = None
    sbfl: SbflExperimentSettings | None = None
    hypothesis: HypothesisExperimentSettings | None = None
    crosshair: CrossHairExperimentSettings | None = None
    cost: ExperimentCostConfiguration | None = None

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
        if self.schema_version == 2:
            required = {
                "environment": self.environment,
                "outputs": self.outputs,
                "sbfl": self.sbfl,
                "hypothesis": self.hypothesis,
                "crosshair": self.crosshair,
                "cost": self.cost,
            }
            missing = sorted(name for name, value in required.items() if value is None)
            if missing:
                raise ValueError(
                    "schema version 2 requires frozen fields: " + ", ".join(missing)
                )
        return self
