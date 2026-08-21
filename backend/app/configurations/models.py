"""Typed experimental conditions shared by Configurations A through D."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from app.agent.budgets import AgentBudgets
from app.benchmark.schema import BenchmarkTask
from app.schemas.common import FilesystemIdentifier, ResearchSchema
from app.security import find_credential_key

ConfigurationId = Literal["A", "B", "C", "D"]
VerificationProfile = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$",
    ),
]


class ExperimentCondition(StrEnum):
    """Named study conditions; D1-D3 remain Configuration D ablations."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    D1 = "D1"
    D2 = "D2"
    D3 = "D3"

    @property
    def configuration_id(self) -> ConfigurationId:
        if self is self.A:
            return "A"
        if self is self.B:
            return "B"
        if self is self.C:
            return "C"
        return "D"


class ResearchTechniques(ResearchSchema):
    """The independently switchable research evidence sources."""

    enable_sbfl: bool = False
    enable_hypothesis: bool = False
    enable_crosshair: bool = False


class ExperimentalConfiguration(ResearchSchema):
    """A fully explicit, internally consistent experimental condition."""

    condition: ExperimentCondition
    verification_profile: VerificationProfile = "deterministic-v1"
    repair_allowance: Literal[0, 1]
    techniques: ResearchTechniques = Field(default_factory=ResearchTechniques)

    @classmethod
    def preset(
        cls,
        condition: ExperimentCondition | str,
        *,
        verification_profile: str = "deterministic-v1",
    ) -> ExperimentalConfiguration:
        selected = ExperimentCondition(condition)
        repair_allowance, techniques = _protocol_for(selected)
        return cls(
            condition=selected,
            verification_profile=verification_profile,
            repair_allowance=repair_allowance,
            techniques=techniques,
        )

    @property
    def configuration_id(self) -> ConfigurationId:
        return self.condition.configuration_id

    @property
    def repository_tools_enabled(self) -> bool:
        return self.condition is not ExperimentCondition.A

    @property
    def verification_feedback_enabled(self) -> bool:
        return self.condition not in {ExperimentCondition.A, ExperimentCondition.B}

    @model_validator(mode="after")
    def condition_has_exact_protocol(self) -> ExperimentalConfiguration:
        repair_allowance, techniques = _protocol_for(self.condition)
        if self.repair_allowance != repair_allowance:
            raise ValueError("repair allowance does not match the named condition")
        if self.techniques != techniques:
            raise ValueError("research techniques do not match the named condition")
        return self


def _protocol_for(
    condition: ExperimentCondition,
) -> tuple[Literal[0, 1], ResearchTechniques]:
    techniques = {
        ExperimentCondition.D: ResearchTechniques(
            enable_sbfl=True,
            enable_hypothesis=True,
            enable_crosshair=True,
        ),
        ExperimentCondition.D1: ResearchTechniques(enable_sbfl=True),
        ExperimentCondition.D2: ResearchTechniques(enable_hypothesis=True),
        ExperimentCondition.D3: ResearchTechniques(enable_crosshair=True),
    }.get(condition, ResearchTechniques())
    repair_allowance: Literal[0, 1] = (
        0 if condition in {ExperimentCondition.A, ExperimentCondition.B} else 1
    )
    return repair_allowance, techniques


class ModelConfiguration(ResearchSchema):
    """Provider-neutral model settings that must remain fixed across conditions."""

    provider: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$",
        ),
    ]
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    model_version: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    temperature: Annotated[float, Field(ge=0, le=2)]
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def reserved_parameters_are_explicit(cls, values: dict[str, JsonValue]) -> dict[str, JsonValue]:
        reserved = {"temperature", "model_version", "experiment_contract"}
        overlap = reserved.intersection(values)
        if overlap:
            names = ", ".join(sorted(overlap))
            raise ValueError(f"reserved model parameters must use typed fields: {names}")
        if find_credential_key(values) is not None:
            raise ValueError(
                "model parameters cannot contain credentials; provider authentication is external"
            )
        return values

    def provider_parameters(self) -> dict[str, JsonValue]:
        return {**self.parameters, "temperature": self.temperature}


class EffectiveResearchTechniques(ResearchSchema):
    """Requested switches resolved against evaluator-owned task eligibility."""

    requested: ResearchTechniques
    effective: ResearchTechniques
    disabled_reasons: dict[str, str] = Field(default_factory=dict)


def resolve_research_techniques(
    configuration: ExperimentalConfiguration,
    task: BenchmarkTask,
) -> EffectiveResearchTechniques:
    """Disable task-inapplicable optional techniques without changing condition identity."""

    requested = configuration.techniques
    disabled: dict[str, str] = {}
    hypothesis = requested.enable_hypothesis and task.property_profile is not None
    crosshair = requested.enable_crosshair and task.symbolic_profile is not None
    if requested.enable_hypothesis and not hypothesis:
        disabled["hypothesis"] = "task has no property_profile"
    if requested.enable_crosshair and not crosshair:
        disabled["crosshair"] = "task has no symbolic_profile"
    return EffectiveResearchTechniques(
        requested=requested,
        effective=ResearchTechniques(
            enable_sbfl=requested.enable_sbfl,
            enable_hypothesis=hypothesis,
            enable_crosshair=crosshair,
        ),
        disabled_reasons=disabled,
    )


class ExperimentContract(ResearchSchema):
    """Frozen fairness metadata persisted with every run."""

    schema_version: Literal[1] = 1
    condition: ExperimentCondition
    configuration_id: ConfigurationId
    provider: str
    model: str
    model_version: str
    temperature: float
    task_id: str
    task_description: str
    base_commit: str
    declared_budgets: AgentBudgets
    effective_budgets: AgentBudgets
    verification_profile: VerificationProfile
    repair_allowance: Literal[0, 1]
    repository_tools_enabled: bool
    verification_feedback_enabled: bool
    research_techniques: EffectiveResearchTechniques


class CommonRunResult(ResearchSchema):
    """Configuration-independent view of the existing core Run record."""

    run_id: FilesystemIdentifier
    task_id: str
    configuration_id: ConfigurationId
    condition: ExperimentCondition
    status: str
    final_resolution: bool | None
    failure_category: str | None
    repair_attempted: bool
    experiment_contract: ExperimentContract
