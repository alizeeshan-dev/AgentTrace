"""Local HTTP API over AgentTrace's existing persistence and experiment services."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import GeminiModelProvider, TokenPricing
from app.agent.budgets import AgentBudgets
from app.agent.service import AgentRunError
from app.artifacts import ArtifactStore
from app.benchmark import load_benchmark_task
from app.config import Settings
from app.configurations import (
    ConfigurationExecutionError,
    ConfigurationRunner,
    ExperimentalConfiguration,
    ExperimentCondition,
    ModelConfiguration,
)
from app.db.engine import session_scope
from app.db.models import (
    Counterexample,
    FaultLocalizationResult,
    PatchArtifact,
    Repository,
    Run,
    Task,
    TraceEvent,
    VerificationResult,
)
from app.experiments.models import ExperimentConfig, ExperimentConfigurationSpec
from app.experiments.runner import ExperimentRunner, ExperimentSlot
from app.repositories import ExternalRepositoryError
from app.services.repositories import RepositoryRegistrationConflict, RepositoryRegistry
from app.tasks import ExternalTaskError, ExternalTaskService
from app.traces import RunTraceExporter
from app.verification import VerificationService, VerificationServiceError


def _database_session(request: Request) -> Iterator[Session]:
    factory = getattr(request.app.state, "sessions", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AgentTrace persistence is not initialized.",
        )
    with session_scope(factory) as session:
        yield session


SessionDependency = Annotated[Session, Depends(_database_session)]


class CreateRunRequest(BaseModel):
    """Small UI request mapped onto the common A--D experiment interface."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    configuration_id: ExperimentCondition
    model: str = Field(min_length=1, max_length=200, pattern=r"^gemini-[A-Za-z0-9._-]+$")


class RegisterExternalRepositoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_url: str = Field(min_length=1, max_length=2_000)
    test_command: str | None = Field(default=None, max_length=500)


class SetRepositoryTrustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trusted_for_local_execution: bool
    acknowledgement: bool = False


class CreateExternalTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_id: str = Field(min_length=1, max_length=100)
    task_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$",
    )
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=10_000)
    task_category: Literal["bug_fix", "refactor"] = "bug_fix"
    test_command: str | None = Field(default=None, max_length=500)
    allowed_paths: list[str] | None = Field(default=None, max_length=30)
    forbidden_paths: list[str] | None = Field(default=None, max_length=30)
    timeout_seconds: int = Field(default=300, ge=1, le=3_600)
    trusted_execution_acknowledged: bool = False


def build_api_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    project_root = Path(__file__).resolve().parents[2]
    benchmark_root = project_root / "benchmark"

    @router.post(
        "/repositories/external",
        status_code=status.HTTP_201_CREATED,
        tags=["repositories"],
    )
    def register_external(
        payload: RegisterExternalRepositoryRequest,
        session: SessionDependency,
    ) -> dict[str, Any]:
        try:
            repository = RepositoryRegistry(session, settings=settings).register_external(
                payload.repository_url,
                test_command=payload.test_command,
            )
        except (ExternalRepositoryError, RepositoryRegistrationConflict) as error:
            error_code = getattr(error, "code", "registration_conflict")
            code = (
                status.HTTP_502_BAD_GATEWAY
                if error_code == "clone_failed"
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=code,
                detail={"code": error_code, "message": str(error)},
            ) from error
        return _repository_payload(repository)

    @router.get("/repositories", tags=["repositories"])
    def list_repositories(session: SessionDependency) -> list[dict[str, Any]]:
        repositories = session.scalars(
            select(Repository).order_by(Repository.registered_at.desc(), Repository.name)
        ).all()
        return [_repository_payload(repository) for repository in repositories]

    @router.get("/repositories/{repository_id}", tags=["repositories"])
    def get_repository(
        repository_id: str, session: SessionDependency
    ) -> dict[str, Any]:
        repository = session.get(Repository, repository_id)
        if repository is None:
            raise HTTPException(status_code=404, detail="Repository not found.")
        return _repository_payload(repository)

    @router.patch("/repositories/{repository_id}/trust", tags=["repositories"])
    def set_repository_trust(
        repository_id: str,
        payload: SetRepositoryTrustRequest,
        session: SessionDependency,
    ) -> dict[str, Any]:
        try:
            repository = RepositoryRegistry(session, settings=settings).set_external_trust(
                repository_id,
                trusted=payload.trusted_for_local_execution,
                acknowledged=payload.acknowledgement,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Repository not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _repository_payload(repository)

    @router.post(
        "/tasks/external",
        status_code=status.HTTP_201_CREATED,
        tags=["tasks"],
    )
    def create_external_task(
        payload: CreateExternalTaskRequest,
        session: SessionDependency,
    ) -> dict[str, Any]:
        try:
            task = ExternalTaskService(session, settings=settings).create(
                repository_id=payload.repository_id,
                task_id=payload.task_id,
                title=payload.title,
                description=payload.description,
                task_category=payload.task_category,
                test_command=payload.test_command,
                allowed_paths=payload.allowed_paths,
                forbidden_paths=payload.forbidden_paths,
                timeout_seconds=payload.timeout_seconds,
                trusted_execution_acknowledged=(
                    payload.trusted_execution_acknowledged
                ),
            )
        except ExternalTaskError as error:
            code = 404 if error.code == "repository_not_found" else 409
            raise HTTPException(
                status_code=code,
                detail={"code": error.code, "message": str(error)},
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_external_task",
                    "message": "External task inputs are invalid.",
                },
            ) from error
        return _task_payload(task)

    @router.get("/tasks", tags=["tasks"])
    def list_tasks(session: SessionDependency) -> list[dict[str, Any]]:
        tasks = session.scalars(select(Task).order_by(Task.task_id)).all()
        return [_task_payload(task) for task in tasks]

    @router.get("/runs", tags=["runs"])
    def list_runs(session: SessionDependency) -> list[dict[str, Any]]:
        runs = session.scalars(select(Run).order_by(Run.started_at.desc())).all()
        return [_run_payload(run) for run in runs]

    @router.get("/runs/{run_id}", tags=["runs"])
    def get_run(run_id: str, session: SessionDependency) -> dict[str, Any]:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        task = session.get(Task, run.task_id)
        if task is None:
            raise HTTPException(status_code=500, detail="Run task record is missing.")
        trace = session.scalars(
            select(TraceEvent)
            .where(TraceEvent.run_id == run_id)
            .order_by(TraceEvent.sequence_number)
        ).all()
        verification = session.scalars(
            select(VerificationResult)
            .where(VerificationResult.run_id == run_id)
            .order_by(VerificationResult.attempt_number, VerificationResult.gate)
        ).all()
        patches = session.scalars(
            select(PatchArtifact)
            .where(PatchArtifact.run_id == run_id)
            .order_by(PatchArtifact.attempt_number)
        ).all()
        counterexamples = session.scalars(
            select(Counterexample)
            .where(Counterexample.run_id == run_id)
            .order_by(Counterexample.attempt_number, Counterexample.counterexample_id)
        ).all()
        localization = session.get(FaultLocalizationResult, run_id)
        return {
            "run": _run_payload(run),
            "task": _task_payload(task),
            "trace": [_trace_payload(item) for item in trace],
            "verification": [_verification_payload(item) for item in verification],
            "patches": [_patch_payload(item) for item in patches],
            "counterexamples": [_counterexample_payload(item) for item in counterexamples],
            "sbfl": _localization_payload(localization) if localization is not None else None,
        }

    @router.get("/experiments", tags=["experiments"])
    def list_experiments(session: SessionDependency) -> list[dict[str, Any]]:
        runs = session.scalars(select(Run).order_by(Run.started_at.desc())).all()
        experiments: dict[str, dict[str, Any]] = {}
        for run in runs:
            metadata = run.model_parameters.get("experiment", {})
            experiment_id = metadata.get("experiment_id")
            if not isinstance(experiment_id, str) or not experiment_id:
                continue
            summary = experiments.setdefault(
                experiment_id,
                {
                    "experiment_id": experiment_id,
                    "benchmark_version": metadata.get("benchmark_version"),
                    "runs": 0,
                    "resolved": 0,
                },
            )
            summary["runs"] += 1
            summary["resolved"] += int(run.final_resolution is True)
        return list(experiments.values())

    @router.post("/runs", status_code=status.HTTP_201_CREATED, tags=["runs"])
    def create_run(payload: CreateRunRequest, request: Request) -> dict[str, str]:
        factory = getattr(request.app.state, "sessions", None)
        if factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AgentTrace persistence is not initialized.",
            )
        with factory() as session:
            task = session.get(Task, payload.task_id)
            if task is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Task is not registered in the AgentTrace database.",
                )
            task_source = task.task_source
            if task_source == "external":
                repository = session.get(Repository, task.repository_id)
                if repository is None:
                    raise HTTPException(status_code=500, detail="Task repository is missing.")
                if not repository.trusted_for_local_execution:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=(
                            "External repository execution is blocked. Explicitly confirm that "
                            "you trust this repository before starting a run."
                        ),
                    )
        secret = settings.gemini_api_key
        if secret is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GEMINI_API_KEY is not configured.",
            )
        if task_source == "external":
            return _create_external_run(
                factory,
                settings=settings,
                payload=payload,
            )

        manifest_reference = _task_manifest_reference(
            payload.task_id, benchmark_root=benchmark_root
        )
        condition = ExperimentalConfiguration.preset(payload.configuration_id)
        experiment = ExperimentConfig(
            experiment_id=(
                f"ui-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
            ),
            benchmark_version="benchmark-v1.0.0",
            tasks=[manifest_reference],
            configurations=[
                ExperimentConfigurationSpec(
                    condition=payload.configuration_id,
                    feature_flags=condition.techniques,
                )
            ],
            model=ModelConfiguration(
                provider="gemini",
                model=payload.model,
                api_key_env="GEMINI_API_KEY",
                temperature=0.0,
                request_timeout_seconds=120.0,
                max_retries=0,
                parameters={"max_output_tokens": 12_000},
            ),
            max_repairs=condition.repair_allowance,
            verification_profile="deterministic-v1",
        )

        def configuration_factory(
            session: Session, slot: ExperimentSlot
        ) -> ConfigurationRunner:
            del slot
            return ConfigurationRunner.from_services(
                session,
                settings=settings,
                provider=GeminiModelProvider(
                    api_key=secret.get_secret_value(),
                    request_timeout_seconds=120.0,
                    max_retries=0,
                    pricing=TokenPricing(),
                ),
            )

        artifacts = ArtifactStore(
            settings.effective_artifact_root,
            max_artifact_bytes=settings.max_artifact_size_bytes,
        )

        def raw_export(
            session: Session, slot: ExperimentSlot, run: Run
        ) -> dict[str, object]:
            del slot
            return RunTraceExporter(session, artifacts).build(run.run_id).model_dump(
                mode="json"
            )

        runner = ExperimentRunner(
            factory,
            settings=settings,
            benchmark_root=benchmark_root,
            configuration_runner_factory=configuration_factory,
            raw_export_factory=raw_export,
        )
        outcome = runner.run(experiment)
        if not outcome.slots:
            raise HTTPException(status_code=500, detail="Experiment produced no run slot.")
        return {"run_id": outcome.slots[0].run_id}

    return router


def _task_manifest_reference(task_id: str, *, benchmark_root: Path) -> str:
    for path in sorted((benchmark_root / "tasks").glob("*.yaml")):
        loaded = load_benchmark_task(path, benchmark_root=benchmark_root)
        if loaded.task.task_id == task_id:
            return path.relative_to(benchmark_root).as_posix()
    raise HTTPException(status_code=404, detail="Benchmark task manifest not found.")


def _create_external_run(
    factory: Any,
    *,
    settings: Settings,
    payload: CreateRunRequest,
) -> dict[str, str]:
    """Run one trusted external task through the same A--D orchestrators."""

    secret = settings.gemini_api_key
    if secret is None:  # Kept at the boundary in case this helper is reused.
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured.")
    run_id = f"run-{uuid4().hex}"
    condition = ExperimentalConfiguration.preset(payload.configuration_id)
    model = ModelConfiguration(
        provider="gemini",
        model=payload.model,
        api_key_env="GEMINI_API_KEY",
        temperature=0.0,
        request_timeout_seconds=120.0,
        max_retries=0,
        parameters={"max_output_tokens": 12_000},
    )
    try:
        with session_scope(factory) as session:
            task = session.get(Task, payload.task_id)
            if task is None or task.task_source != "external":
                raise HTTPException(status_code=404, detail="External task not found.")
            repository = session.get(Repository, task.repository_id)
            if repository is None or not repository.trusted_for_local_execution:
                raise HTTPException(
                    status_code=403,
                    detail="External repository execution requires explicit trust.",
                )
            if task.definition_path is None:
                raise HTTPException(
                    status_code=409,
                    detail="External task has no managed definition.",
                )
            manifest = Path(task.definition_path)
            provider = GeminiModelProvider(
                api_key=secret.get_secret_value(),
                request_timeout_seconds=model.request_timeout_seconds,
                max_retries=model.max_retries,
                pricing=TokenPricing(),
            )
            runner = ConfigurationRunner.from_services(
                session,
                settings=settings,
                provider=provider,
            )
            runner.run(
                manifest,
                run_id=run_id,
                configuration=condition,
                model=model,
                budgets=AgentBudgets(),
                benchmark_root=settings.state_dir,
            )
            record = session.get(Run, run_id)
            if record is None:
                raise ConfigurationExecutionError("configuration produced no Run record")
            if condition.configuration_id in {"A", "B"} and record.status == "patch_submitted":
                # Evaluation remains post-hoc for A/B; no result returns to the model.
                VerificationService(session, settings=settings).verify(
                    manifest,
                    run_id=run_id,
                    attempt_number=1,
                    benchmark_root=settings.state_dir,
                )
                completed_at = datetime.now(UTC)
                record.finished_at = completed_at
                record.latency_ms = max(
                    0,
                    int((completed_at - record.started_at).total_seconds() * 1_000),
                )
            parameters = dict(record.model_parameters)
            parameters["external_repository"] = {
                "repository_id": repository.repository_id,
                "repository_url": repository.repository_url,
                "base_commit": repository.base_commit,
                "trusted_for_local_execution": True,
                "task_source": "external",
                "verification_configured": task.verification_configured,
                "hidden_tests_available": False,
                "mutation_score_assessed": False,
            }
            record.model_parameters = parameters
            artifacts = ArtifactStore(
                settings.effective_artifact_root,
                max_artifact_bytes=settings.max_artifact_size_bytes,
            )
            exporter = RunTraceExporter(session, artifacts)
            export_reference = exporter.store_export(run_id)
            parameters = dict(record.model_parameters)
            references = dict(parameters.get("artifact_references", {}))
            references["raw_run_export"] = export_reference.relative_path
            parameters["artifact_references"] = references
            record.model_parameters = parameters
            session.flush()
    except HTTPException:
        raise
    except (AgentRunError, ConfigurationExecutionError, VerificationServiceError) as error:
        raise HTTPException(
            status_code=409,
            detail={"code": type(error).__name__, "message": str(error)},
        ) from error
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "external_run_infrastructure_failure",
                "message": "The external run could not prepare its managed workspace.",
            },
        ) from error
    return {"run_id": run_id}


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _task_payload(task: Task) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "repository_id": task.repository_id,
        "title": task.title,
        "description": task.description,
        "task_category": task.task_category,
        "difficulty": task.difficulty,
        "allowed_paths": task.allowed_paths,
        "forbidden_paths": task.forbidden_paths,
        "visible_test_command": task.visible_test_command,
        "hidden_test_command": (
            "[REDACTED:HIDDEN_TEST_COMMAND]" if task.hidden_test_command else ""
        ),
        "hidden_tests_available": bool(task.hidden_test_command),
        "property_profile": task.property_profile,
        "symbolic_profile": task.symbolic_profile,
        "known_correct_patch": task.known_correct_patch,
        "task_source": task.task_source,
        "verification_configured": task.verification_configured,
        "created_at": _timestamp(task.created_at),
    }


def _repository_payload(repository: Repository) -> dict[str, Any]:
    metadata = dict(repository.repository_metadata or {})
    return {
        "repository_id": repository.repository_id,
        "name": repository.name,
        "source_type": repository.source_type,
        "repository_url": repository.repository_url,
        "base_commit": repository.base_commit,
        "default_branch": repository.default_branch,
        "primary_language": repository.primary_language,
        "python_version": repository.python_version,
        "test_command": repository.test_command or None,
        "trusted_for_local_execution": repository.trusted_for_local_execution,
        "trust_confirmed_at": _timestamp(repository.trust_confirmed_at),
        "registered_at": _timestamp(repository.registered_at),
        "verification_configured": bool(metadata.get("verification_configured")),
        "metadata": metadata,
    }


def _run_payload(run: Run) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "configuration_id": run.configuration_id,
        "model": run.model,
        "model_parameters": run.model_parameters,
        "status": run.status,
        "started_at": _timestamp(run.started_at),
        "finished_at": _timestamp(run.finished_at),
        "latency_ms": run.latency_ms,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "estimated_cost": run.estimated_cost,
        "tool_calls": run.tool_calls,
        "files_read": run.files_read,
        "lines_exposed": run.lines_exposed,
        "repair_attempted": run.repair_attempted,
        "final_resolution": run.final_resolution,
        "failure_category": run.failure_category,
    }


def _trace_payload(event: TraceEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "sequence_number": event.sequence_number,
        "parent_event_id": event.parent_event_id,
        "operation": event.operation,
        "started_at": _timestamp(event.started_at),
        "finished_at": _timestamp(event.finished_at),
        "status": event.status,
        "input_summary": event.input_summary,
        "output_summary": event.output_summary,
        "error_type": event.error_type,
    }


def _verification_payload(result: VerificationResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "attempt_number": result.attempt_number,
        "gate": result.gate,
        "required": result.required,
        "status": result.status,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "baseline_difference": result.baseline_difference,
        "summary": result.summary,
        "log_artifact": result.log_artifact,
    }


def _patch_payload(patch: PatchArtifact) -> dict[str, Any]:
    return {
        "run_id": patch.run_id,
        "attempt_number": patch.attempt_number,
        "unified_diff": patch.unified_diff,
        "files_changed": patch.files_changed,
        "lines_added": patch.lines_added,
        "lines_removed": patch.lines_removed,
        "applied_successfully": patch.applied_successfully,
    }


def _counterexample_payload(counterexample: Counterexample) -> dict[str, Any]:
    return {
        "counterexample_id": counterexample.counterexample_id,
        "run_id": counterexample.run_id,
        "attempt_number": counterexample.attempt_number,
        "source": counterexample.source,
        "gate": counterexample.gate,
        "input_summary": counterexample.input_summary,
        "expected_summary": counterexample.expected_summary,
        "observed_summary": counterexample.observed_summary,
        "failure_type": counterexample.failure_type,
        "location_hints": counterexample.location_hints,
        "is_new_vs_baseline": counterexample.is_new_vs_baseline,
        "log_excerpt": counterexample.log_excerpt,
        "sanitized_feedback": counterexample.sanitized_feedback,
    }


def _localization_payload(result: FaultLocalizationResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "metric": result.metric,
        "ranked_locations": result.ranked_locations,
        "top_k": result.top_k,
        "fault_rank_if_known": result.fault_rank_if_known,
        "coverage_artifact": result.coverage_artifact,
    }
