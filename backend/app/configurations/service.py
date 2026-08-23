"""One execution interface over Configurations A, B, C, and injected D."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue
from sqlalchemy.orm import Session

from app.agent.budgets import AgentBudgets
from app.agent.provider import ModelProvider
from app.agent.service import AgentRunService
from app.cegis.service import ConfigurationCService
from app.config import Settings
from app.db.models import Run
from app.tasks import LoadedTaskDefinition, load_task_definition
from app.verification.service import VerificationFeatures, VerificationService

from .models import (
    CommonRunResult,
    EffectiveResearchTechniques,
    ExperimentalConfiguration,
    ExperimentContract,
    ModelConfiguration,
    resolve_research_techniques,
)


class ConfigurationExecutionError(RuntimeError):
    """Raised when a condition cannot satisfy the common execution contract."""


@dataclass(frozen=True, slots=True)
class ConfigurationExecution:
    """Resolved inputs passed to one small condition adapter."""

    manifest_path: Path
    benchmark_root: Path | None
    run_id: str
    loaded_task: LoadedTaskDefinition
    configuration: ExperimentalConfiguration
    model: ModelConfiguration
    budgets: AgentBudgets
    provider_parameters: dict[str, JsonValue]
    research_techniques: EffectiveResearchTechniques


class ConfigurationExecutor(Protocol):
    """Seam implemented by each condition without duplicating its orchestration."""

    def execute(self, execution: ConfigurationExecution) -> object: ...


class _DirectExecutor:
    def __init__(self, service: AgentRunService) -> None:
        self.service = service

    def execute(self, execution: ConfigurationExecution) -> object:
        return self.service.run_direct(
            execution.manifest_path,
            run_id=execution.run_id,
            model_identifier=execution.model.model,
            model_parameters=execution.provider_parameters,
            budgets=execution.budgets,
            benchmark_root=execution.benchmark_root,
        )


class _ToolExecutor:
    def __init__(self, service: AgentRunService) -> None:
        self.service = service

    def execute(self, execution: ConfigurationExecution) -> object:
        return self.service.run_tool_agent(
            execution.manifest_path,
            run_id=execution.run_id,
            model_identifier=execution.model.model,
            model_parameters=execution.provider_parameters,
            budgets=execution.budgets,
            benchmark_root=execution.benchmark_root,
        )


class _CegisExecutor:
    def __init__(self, service: ConfigurationCService) -> None:
        self.service = service

    def execute(self, execution: ConfigurationExecution) -> object:
        return self.service.run(
            execution.manifest_path,
            run_id=execution.run_id,
            model_identifier=execution.model.model,
            model_parameters=execution.provider_parameters,
            budgets=execution.budgets,
            benchmark_root=execution.benchmark_root,
        )


class ConfigurationRunner:
    """Dispatch a typed study condition and persist its immutable fairness snapshot."""

    def __init__(
        self,
        session: Session,
        *,
        executors: dict[str, ConfigurationExecutor],
        provider_name: str | None = None,
    ) -> None:
        self.session = session
        self.executors = dict(executors)
        self.provider_name = provider_name

    @classmethod
    def from_services(
        cls,
        session: Session,
        *,
        settings: Settings,
        provider: ModelProvider,
        configuration_d: ConfigurationExecutor | None = None,
    ) -> ConfigurationRunner:
        agent = AgentRunService(session, settings=settings, provider=provider)
        executors: dict[str, ConfigurationExecutor] = {
            "A": _DirectExecutor(agent),
            "B": _ToolExecutor(agent),
            "C": _CegisExecutor(
                ConfigurationCService(
                    session,
                    settings=settings,
                    provider=provider,
                    verifier=VerificationService(
                        session,
                        settings=settings,
                        features=VerificationFeatures(
                            enable_hypothesis=False,
                            enable_symbolic=False,
                        ),
                    ),
                )
            ),
        }
        if configuration_d is None:
            # Local import keeps the shared protocol independent from D's
            # implementation while making the standard factory complete.
            from .enhanced import ConfigurationDExecutor

            configuration_d = ConfigurationDExecutor(
                session,
                settings=settings,
                provider=provider,
            )
        executors["D"] = configuration_d
        return cls(session, executors=executors, provider_name=provider.provider_name)

    def run(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        configuration: ExperimentalConfiguration,
        model: ModelConfiguration,
        budgets: AgentBudgets | None = None,
        benchmark_root: str | Path | None = None,
    ) -> CommonRunResult:
        loaded = load_task_definition(manifest_path, benchmark_root=benchmark_root)
        if self.provider_name is not None and model.provider != self.provider_name:
            raise ConfigurationExecutionError(
                "configured provider identity does not match the active provider adapter"
            )
        declared = budgets or AgentBudgets()
        effective_budgets = _effective_budgets(configuration, declared)
        techniques = resolve_research_techniques(configuration, loaded.task)
        contract = ExperimentContract(
            condition=configuration.condition,
            configuration_id=configuration.configuration_id,
            provider=model.provider,
            model=model.model,
            model_version=model.model_version,
            api_key_env=model.api_key_env,
            request_timeout_seconds=model.request_timeout_seconds,
            max_retries=model.max_retries,
            temperature=model.temperature,
            task_id=loaded.task.task_id,
            task_description=loaded.task.description,
            base_commit=loaded.task.base_commit,
            declared_budgets=declared,
            effective_budgets=effective_budgets,
            verification_profile=configuration.verification_profile,
            repair_allowance=configuration.repair_allowance,
            repository_tools_enabled=configuration.repository_tools_enabled,
            verification_feedback_enabled=configuration.verification_feedback_enabled,
            research_techniques=techniques,
        )
        executor = self.executors.get(configuration.configuration_id)
        if executor is None:
            raise ConfigurationExecutionError(
                f"no executor is registered for Configuration {configuration.configuration_id}"
            )
        root = Path(benchmark_root).resolve() if benchmark_root is not None else None
        execution = ConfigurationExecution(
            manifest_path=Path(manifest_path).resolve(),
            benchmark_root=root,
            run_id=run_id,
            loaded_task=loaded,
            configuration=configuration,
            model=model,
            budgets=effective_budgets,
            provider_parameters=model.provider_parameters(),
            research_techniques=techniques,
        )
        executor.execute(execution)
        record = self.session.get(Run, run_id)
        if record is None:
            raise ConfigurationExecutionError("configuration executor did not persist a Run")
        if record.task_id != loaded.task.task_id:
            raise ConfigurationExecutionError("configuration executor persisted the wrong task")
        if record.configuration_id != configuration.configuration_id:
            raise ConfigurationExecutionError(
                "configuration executor persisted the wrong configuration identity"
            )
        parameters = dict(record.model_parameters)
        parameters["experiment_contract"] = contract.model_dump(mode="json")
        record.model_parameters = parameters
        self.session.flush()
        return CommonRunResult(
            run_id=record.run_id,
            task_id=record.task_id,
            configuration_id=configuration.configuration_id,
            condition=configuration.condition,
            status=record.status,
            final_resolution=record.final_resolution,
            failure_category=record.failure_category,
            repair_attempted=record.repair_attempted,
            experiment_contract=contract,
        )


def _effective_budgets(
    configuration: ExperimentalConfiguration,
    declared: AgentBudgets,
) -> AgentBudgets:
    if configuration.configuration_id != "A":
        return declared
    # Configuration A's one-shot/no-tool ceiling is a declared protocol
    # restriction and is persisted beside the caller's common budget.
    return declared.model_copy(update={"max_model_turns": 1, "max_tool_calls": 0})
