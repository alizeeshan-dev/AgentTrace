"""Configuration-driven, resumable execution over the common A--D interface."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field
from sqlalchemy.orm import Session, sessionmaker

from app.agent.provider import ModelProviderError
from app.benchmark.loader import LoadedBenchmarkTask, load_benchmark_task
from app.config import Settings
from app.configurations import ConfigurationRunner, ExperimentalConfiguration
from app.db.engine import session_scope
from app.db.models import Run, Task
from app.schemas.common import ResearchSchema
from app.verification import VerificationRun, VerificationService

from .failures import FailureCategory, FailureClassification, classify_run
from .models import ExperimentConfig, ExperimentConfigurationSpec
from .storage import ExperimentDataLayout


class ExperimentRunnerError(RuntimeError):
    """The frozen experiment plan cannot be executed safely."""


class SlotStatus(StrEnum):
    EXECUTED = "executed"
    SKIPPED_COMPLETED = "skipped_completed"
    SKIPPED_INFRASTRUCTURE_FAILURE = "skipped_infrastructure_failure"
    BLOCKED_INCOMPLETE = "blocked_incomplete"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    MODEL_PROVIDER_FAILURE = "model_provider_failure"


class ExperimentSlot(ResearchSchema):
    """One deterministic task-condition cell in the experiment matrix."""

    run_id: str
    task_manifest: str
    task_id: str
    base_commit: str
    condition: str
    configuration_id: str


class SlotOutcome(ResearchSchema):
    run_id: str
    task_id: str
    condition: str
    status: SlotStatus
    run_status: str | None = None
    final_resolution: bool | None = None
    failure: FailureClassification | None = None
    raw_record: str | None = None
    raw_sha256: str | None = None


class ExperimentOutcome(ResearchSchema):
    experiment_id: str
    benchmark_version: str
    slots: list[SlotOutcome] = Field(default_factory=list)


class PostPatchVerifier(Protocol):
    def verify(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        attempt_number: int = 1,
        benchmark_root: str | Path | None = None,
    ) -> VerificationRun: ...


type ConfigurationRunnerFactory = Callable[[Session, ExperimentSlot], ConfigurationRunner]
type VerifierFactory = Callable[[Session, ExperimentSlot], PostPatchVerifier]
type CompletedRunHook = Callable[[Session, ExperimentSlot, Run], None]
type RawExportFactory = Callable[[Session, ExperimentSlot, Run], dict[str, object]]


class ExperimentRunner:
    """Execute missing cells once while preserving existing raw outcomes."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        settings: Settings,
        benchmark_root: str | Path,
        configuration_runner_factory: ConfigurationRunnerFactory,
        verifier_factory: VerifierFactory | None = None,
        completed_run_hooks: Sequence[CompletedRunHook] = (),
        raw_export_factory: RawExportFactory | None = None,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.benchmark_root = Path(benchmark_root).resolve(strict=True)
        self.configuration_runner_factory = configuration_runner_factory
        self.verifier_factory = verifier_factory or self._default_verifier
        self.completed_run_hooks = tuple(completed_run_hooks)
        self.raw_export_factory = raw_export_factory or _core_raw_export

    def plan(self, config: ExperimentConfig) -> list[ExperimentSlot]:
        slots: list[ExperimentSlot] = []
        for reference in config.tasks:
            loaded = self._load_task(reference)
            for condition in config.configurations:
                slots.append(_slot(config, reference, loaded, condition))
        return slots

    def run(self, config: ExperimentConfig) -> ExperimentOutcome:
        layout = ExperimentDataLayout.create(
            self.settings.state_dir,
            config.experiment_id,
            raw_location=config.outputs.raw if config.outputs is not None else "raw/",
            derived_location=(
                config.outputs.derived if config.outputs is not None else "derived/"
            ),
        )
        outcomes: list[SlotOutcome] = []
        for slot in self.plan(config):
            existing = self._existing_outcome(slot, layout)
            if existing is not None:
                outcomes.append(existing)
                continue
            outcome, run_export = self._execute_slot(config, slot)
            payload = {
                "runner_outcome": outcome.model_dump(
                    mode="json", exclude={"raw_record", "raw_sha256"}
                ),
                "run_export": run_export,
            }
            raw_path = layout.write_raw_once(slot.run_id, payload)
            raw_digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
            outcomes.append(
                outcome.model_copy(
                    update={
                        "raw_record": raw_path.relative_to(layout.root).as_posix(),
                        "raw_sha256": raw_digest,
                    }
                )
            )
        return ExperimentOutcome(
            experiment_id=config.experiment_id,
            benchmark_version=config.benchmark_version,
            slots=outcomes,
        )

    def _execute_slot(
        self, config: ExperimentConfig, slot: ExperimentSlot
    ) -> tuple[SlotOutcome, dict[str, object]]:
        manifest = self.benchmark_root / Path(*slot.task_manifest.split("/"))
        selected = next(
            item for item in config.configurations if item.condition.value == slot.condition
        )
        configuration = ExperimentalConfiguration.preset(
            selected.condition,
            verification_profile=config.verification_profile,
        )
        loaded = load_benchmark_task(manifest, benchmark_root=self.benchmark_root)
        try:
            with session_scope(self.sessions) as session:
                task = session.get(Task, slot.task_id)
                if task is None:
                    raise ExperimentRunnerError(
                        f"benchmark task is not qualified in the database: {slot.task_id}"
                    )
                _reconcile_additive_task_profiles(task, loaded)
                runner = self.configuration_runner_factory(session, slot)
                runner.run(
                    manifest,
                    run_id=slot.run_id,
                    configuration=configuration,
                    model=config.model,
                    budgets=config.budgets,
                    benchmark_root=self.benchmark_root,
                )
                record = session.get(Run, slot.run_id)
                if record is None:
                    raise ExperimentRunnerError("configuration produced no core Run record")
                if slot.configuration_id in {"A", "B"} and record.status == "patch_submitted":
                    # Post-hoc only: the model is never called again and receives
                    # no verification result in the A/B experimental conditions.
                    self.verifier_factory(session, slot).verify(
                        manifest,
                        run_id=slot.run_id,
                        attempt_number=1,
                        benchmark_root=self.benchmark_root,
                    )
                    completed_at = datetime.now(UTC)
                    record.finished_at = completed_at
                    record.latency_ms = max(
                        0,
                        int((completed_at - record.started_at).total_seconds() * 1_000),
                    )
                _store_experiment_identity(record, config, slot)
                failure = classify_run(session, record)
                _store_automatic_classification(record, failure)
                for hook in self.completed_run_hooks:
                    hook(session, slot, record)
                return (
                    _outcome_from_record(slot, record, failure),
                    self.raw_export_factory(session, slot, record),
                )
        except ExperimentRunnerError:
            raise
        except ModelProviderError as error:
            return self._persist_execution_failure(
                config,
                slot,
                FailureCategory.MODEL_PROVIDER_FAILURE,
                error_type=type(error).__name__,
            )
        except Exception as error:
            return self._persist_execution_failure(
                config,
                slot,
                FailureCategory.INFRASTRUCTURE_FAILURE,
                error_type=type(error).__name__,
            )

    def _persist_execution_failure(
        self,
        config: ExperimentConfig,
        slot: ExperimentSlot,
        category: FailureCategory,
        *,
        error_type: str,
    ) -> tuple[SlotOutcome, dict[str, object]]:
        now = datetime.now(UTC)
        with session_scope(self.sessions) as session:
            existing = session.get(Run, slot.run_id)
            if existing is not None:
                # A failed transaction may have committed a terminal failure
                # inside an underlying service. Never overwrite that raw state.
                failure = classify_run(session, existing)
                return (
                    _outcome_from_record(slot, existing, failure),
                    self.raw_export_factory(session, slot, existing),
                )
            record = Run(
                run_id=slot.run_id,
                task_id=slot.task_id,
                configuration_id=slot.configuration_id,
                model=config.model.model,
                model_parameters={
                    "experiment_id": config.experiment_id,
                    "benchmark_version": config.benchmark_version,
                    "condition": slot.condition,
                    "execution_error_type": error_type,
                },
                status=(
                    "model_provider_failure"
                    if category is FailureCategory.MODEL_PROVIDER_FAILURE
                    else "infrastructure_failure"
                ),
                started_at=now,
                finished_at=now,
                latency_ms=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost=None,
                tool_calls=0,
                files_read=0,
                lines_exposed=0,
                repair_attempted=False,
                final_resolution=None,
                failure_category=category.value,
            )
            failure = FailureClassification(
                primary=category,
                evidence=["runner controlled exception boundary"],
            )
            _store_experiment_identity(record, config, slot)
            _store_automatic_classification(record, failure)
            session.add(record)
            session.flush()
            return (
                _outcome_from_record(slot, record, failure),
                self.raw_export_factory(session, slot, record),
            )

    def _existing_outcome(
        self,
        slot: ExperimentSlot,
        layout: ExperimentDataLayout,
    ) -> SlotOutcome | None:
        with self.sessions() as session:
            record = session.get(Run, slot.run_id)
            if record is None:
                return None
            failure = classify_run(session, record)
            raw = layout.existing_raw(slot.run_id)
            if (
                record.finished_at is None
                or record.status in {"running", "preparing"}
                or raw is None
            ):
                status = SlotStatus.BLOCKED_INCOMPLETE
            elif failure.primary is FailureCategory.INFRASTRUCTURE_FAILURE:
                status = SlotStatus.SKIPPED_INFRASTRUCTURE_FAILURE
            else:
                status = SlotStatus.SKIPPED_COMPLETED
            return SlotOutcome(
                run_id=record.run_id,
                task_id=record.task_id,
                condition=slot.condition,
                status=status,
                run_status=record.status,
                final_resolution=record.final_resolution,
                failure=failure,
                raw_record=(
                    raw[0].relative_to(layout.root).as_posix() if raw is not None else None
                ),
                raw_sha256=raw[1] if raw is not None else None,
            )

    def _load_task(self, reference: str) -> LoadedBenchmarkTask:
        path = self.benchmark_root / Path(*reference.split("/"))
        return load_benchmark_task(path, benchmark_root=self.benchmark_root)

    def _default_verifier(self, session: Session, slot: ExperimentSlot) -> PostPatchVerifier:
        del slot
        return VerificationService(session, settings=self.settings)


def stable_run_id(
    config: ExperimentConfig,
    task: LoadedBenchmarkTask,
    condition: ExperimentConfigurationSpec,
) -> str:
    """Derive the run identity from every frozen, outcome-relevant input."""

    identity = {
        "experiment": config.model_dump(mode="json"),
        "task": task.task.model_dump(mode="json"),
        "condition": condition.model_dump(mode="json"),
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"run-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:40]}"


def _slot(
    config: ExperimentConfig,
    reference: str,
    task: LoadedBenchmarkTask,
    condition: ExperimentConfigurationSpec,
) -> ExperimentSlot:
    return ExperimentSlot(
        run_id=stable_run_id(config, task, condition),
        task_manifest=reference,
        task_id=task.task.task_id,
        base_commit=task.task.base_commit,
        condition=condition.condition.value,
        configuration_id=condition.condition.configuration_id,
    )


def _store_automatic_classification(
    run: Run,
    classification: FailureClassification,
) -> None:
    parameters = dict(run.model_parameters)
    parameters["failure_classification"] = classification.model_dump(mode="json")
    run.model_parameters = parameters
    run.failure_category = (
        classification.primary.value if classification.primary is not None else None
    )


def _reconcile_additive_task_profiles(task: Task, loaded: LoadedBenchmarkTask) -> None:
    """Fill profile fields absent from pre-Phase6 rows after immutable checks."""

    expected = {
        "allowed_paths": loaded.task.allowed_paths,
        "description": loaded.task.description,
        "difficulty": loaded.task.difficulty,
        "forbidden_paths": loaded.task.forbidden_paths,
        "hidden_test_command": loaded.task.hidden_test_command,
        "task_category": loaded.task.task_category,
        "title": loaded.task.title,
        "visible_test_command": loaded.task.visible_test_command,
    }
    if any(getattr(task, field) != value for field, value in expected.items()):
        raise ExperimentRunnerError("persisted benchmark task contract differs from the manifest")
    for field in ("property_profile", "symbolic_profile"):
        persisted = getattr(task, field)
        declared = getattr(loaded.task, field)
        if persisted is None and declared is not None:
            setattr(task, field, declared)
        elif persisted != declared:
            raise ExperimentRunnerError(
                f"persisted benchmark {field} differs from the manifest"
            )


def _store_experiment_identity(
    run: Run,
    config: ExperimentConfig,
    slot: ExperimentSlot,
) -> None:
    canonical = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    parameters = dict(run.model_parameters)
    parameters["experiment"] = {
        "experiment_id": config.experiment_id,
        "benchmark_version": config.benchmark_version,
        "experiment_config_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "stable_run_id": slot.run_id,
        "condition": slot.condition,
        "environment_id": (
            config.environment.environment_id if config.environment is not None else None
        ),
        "environment_fingerprint_sha256": (
            config.environment.fingerprint_sha256 if config.environment is not None else None
        ),
    }
    run.model_parameters = parameters


def _outcome_from_record(
    slot: ExperimentSlot,
    run: Run,
    failure: FailureClassification,
) -> SlotOutcome:
    if failure.primary is FailureCategory.INFRASTRUCTURE_FAILURE:
        status = SlotStatus.INFRASTRUCTURE_FAILURE
    elif failure.primary is FailureCategory.MODEL_PROVIDER_FAILURE:
        status = SlotStatus.MODEL_PROVIDER_FAILURE
    else:
        status = SlotStatus.EXECUTED
    return SlotOutcome(
        run_id=run.run_id,
        task_id=run.task_id,
        condition=slot.condition,
        status=status,
        run_status=run.status,
        final_resolution=run.final_resolution,
        failure=failure,
    )


def _core_raw_export(
    session: Session,
    slot: ExperimentSlot,
    run: Run,
) -> dict[str, object]:
    """Minimal DB-free fallback; a trace exporter can inject the complete export."""

    del session
    return {
        "schema": "agenttrace.runner-core-export.v1",
        "slot": slot.model_dump(mode="json"),
        "run": {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "configuration_id": run.configuration_id,
            "model": run.model,
            "model_parameters": run.model_parameters,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
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
        },
    }
