"""Configuration C: one counterexample-guided replacement patch, then stop."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

from pydantic import JsonValue
from sqlalchemy.orm import Session

from app.agent.actions import SubmitPatchAction, ToolCallAction
from app.agent.budgets import AgentBudgets, BudgetExhausted, BudgetTracker
from app.agent.patches import (
    PatchValidationError,
    PatchValidationResult,
    PatchValidator,
    apply_validated_patch,
)
from app.agent.protocol import model_event
from app.agent.provider import ModelMessage, ModelProvider, ModelProviderError, ModelRequest
from app.agent.service import AgentRunService
from app.agent.tools import ConstrainedRepositoryTools, ToolInputError
from app.artifacts import ArtifactReference, ArtifactStore
from app.benchmark.loader import LoadedBenchmarkTask, load_benchmark_task
from app.config import Settings
from app.db.models import PatchArtifact, Run, TraceEvent
from app.repositories.path_policy import PathPolicyError
from app.repositories.workspace import WorkspaceManager
from app.schemas.research import Counterexample
from app.services.workspaces import LoadedTaskWorkspace, TaskWorkspaceLoader
from app.verification.service import VerificationRun, VerificationService

from .counterexamples import CounterexampleExtractor

_TOOLS = ["list_tree", "read_file", "search_code"]
_HIDDEN_PATHS = (".agenttrace-evaluator/", "hidden_tests/")


class VerificationOracle(Protocol):
    def verify(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        attempt_number: int = 1,
        benchmark_root: str | Path | None = None,
    ) -> VerificationRun: ...


class CounterexampleOracle(Protocol):
    def extract(
        self, run_id: str, attempt_number: int, verification: VerificationRun
    ) -> Counterexample | None: ...


@dataclass(frozen=True, slots=True)
class RepairMetrics:
    repair_attempted: bool
    initial_resolution: bool | None
    final_resolution: bool | None
    counterexample_source: str | None
    initial_patch_bytes: int | None
    final_patch_bytes: int | None
    added_input_tokens: int
    added_output_tokens: int
    added_model_latency_ms: int
    added_latency_ms: int
    added_cost: float | None
    repair_success: bool
    repair_induced_regression: bool
    initial_verification_duration_ms: int
    final_verification_duration_ms: int | None


@dataclass(frozen=True, slots=True)
class ConfigurationCResult:
    run_id: str
    status: str
    initial_verification: VerificationRun | None
    final_verification: VerificationRun | None
    counterexample: Counterexample | None
    repair_patch: ArtifactReference | None
    metrics: RepairMetrics


class ConfigurationCService:
    """Compose the existing tool agent and verifier into a two-candidate protocol."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider: ModelProvider,
        verifier: VerificationOracle | None = None,
        extractor: CounterexampleOracle | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider
        self.workspaces = WorkspaceManager(settings.effective_workspace_root)
        self.artifacts = ArtifactStore(
            settings.effective_artifact_root,
            max_artifact_bytes=settings.max_artifact_size_bytes,
        )
        self.verifier = verifier or VerificationService(session, settings=settings)
        self.extractor = extractor or CounterexampleExtractor(session)
        self._last_event_id: str | None = None
        self._sequence = 0

    def run(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        model_identifier: str,
        model_parameters: dict[str, JsonValue] | None = None,
        budgets: AgentBudgets | None = None,
        benchmark_root: str | Path | None = None,
    ) -> ConfigurationCResult:
        """Run P0, optionally request P1 once, fully verify it, and terminate."""

        self._last_event_id = None
        self._sequence = 0
        started_clock = monotonic()
        limits = budgets or AgentBudgets()
        loaded = load_benchmark_task(manifest_path, benchmark_root=benchmark_root)
        initial = AgentRunService(
            self.session, settings=self.settings, provider=self.provider
        ).run_tool_agent(
            manifest_path,
            run_id=run_id,
            model_identifier=model_identifier,
            model_parameters=model_parameters or {},
            budgets=limits,
            benchmark_root=benchmark_root,
        )
        run = self.session.get(Run, run_id)
        if run is None:
            raise RuntimeError("initial agent run was not persisted")
        run.configuration_id = "C"
        parameters = dict(run.model_parameters)
        parameters["phase"] = 7
        parameters["protocol_version"] = "configuration-c-cegis-v1"
        run.model_parameters = parameters
        self._event(
            run_id,
            "p0_generation",
            initial.status,
            output={
                "patch_bytes": _patch_bytes(initial.patch.unified_diff) if initial.patch else None,
                "patch_present": initial.patch is not None,
            },
        )

        initial_patch = self.session.get(PatchArtifact, (run_id, 1))
        if initial_patch is None:
            run.status = initial.status
            metrics = self._finish_without_repair(
                run,
                initial_resolution=None,
                initial_patch_bytes=None,
                started_clock=started_clock,
            )
            return ConfigurationCResult(run_id, run.status, None, None, None, None, metrics)

        initial_verification = self.verifier.verify(
            manifest_path,
            run_id=run_id,
            attempt_number=1,
            benchmark_root=benchmark_root,
        )
        self._event(
            run_id,
            "p0_verification",
            _resolution_status(initial_verification.resolved),
            output={"regression": initial_verification.regression},
        )
        if initial_verification.resolved is not False:
            metrics = self._finish_without_repair(
                run,
                initial_resolution=initial_verification.resolved,
                initial_patch_bytes=_patch_bytes(initial_patch.unified_diff),
                started_clock=started_clock,
                verification=initial_verification,
            )
            return ConfigurationCResult(
                run_id,
                run.status,
                initial_verification,
                None,
                None,
                None,
                metrics,
            )

        counterexample = self.extractor.extract(run_id, 1, initial_verification)
        if counterexample is None:
            run.status = "verified_fail_no_counterexample"
            metrics = self._finish_without_repair(
                run,
                initial_resolution=False,
                initial_patch_bytes=_patch_bytes(initial_patch.unified_diff),
                started_clock=started_clock,
                verification=initial_verification,
            )
            return ConfigurationCResult(
                run_id,
                run.status,
                initial_verification,
                None,
                None,
                None,
                metrics,
            )

        self._event(
            run_id,
            "counterexample_creation",
            "created",
            output={
                "counterexample_id": counterexample.counterexample_id,
                "source": counterexample.source,
            },
        )
        run.repair_attempted = True
        self._event(run_id, "repair_start", "started", input={"source": counterexample.source})
        initial_input_tokens = run.input_tokens
        initial_output_tokens = run.output_tokens
        initial_estimated_cost = run.estimated_cost
        repair_started = monotonic()
        repair_model_latency_ms = 0
        repair_patch_reference: ArtifactReference | None = None
        final_verification: VerificationRun | None = None
        repair_messages_payload: list[dict[str, Any]] = []

        tracker = self._resume_tracker(run, limits, started_clock)
        repair_workspace = self._repair_workspace(run_id, loaded)
        repair_events: list[dict[str, Any]] = []
        try:
            self._apply_for_inspection(repair_workspace, initial_patch, limits)
            messages = _repair_messages(loaded, initial_patch, counterexample)
            repair_messages_payload = [message.model_dump(mode="json") for message in messages]
            tools = ConstrainedRepositoryTools(repair_workspace.paths, tracker)
            while True:
                tracker.begin_model_turn()
                remaining = limits.wall_clock_seconds - (monotonic() - started_clock)
                if remaining <= 0:
                    raise BudgetExhausted("wall_clock_seconds")
                response = self.provider.generate(
                    ModelRequest(
                        model_identifier=model_identifier,
                        model_parameters=model_parameters or {},
                        messages=messages,
                        available_tools=(
                            _TOOLS if tracker.tool_calls < limits.max_tool_calls else []
                        ),
                        timeout_seconds=remaining,
                        metadata={"configuration_id": "C", "phase": 7, "stage": "repair"},
                    )
                )
                run.input_tokens += response.usage.input_tokens
                run.output_tokens += response.usage.output_tokens
                run.estimated_cost = _add_cost(
                    run.estimated_cost, response.usage.estimated_cost
                )
                repair_model_latency_ms += response.latency_ms
                repair_events.append(model_event(response))
                if isinstance(response.action, SubmitPatchAction):
                    self._event(
                        run_id,
                        "p1_generation",
                        "generated",
                        output={"patch_bytes": _patch_bytes(response.action.unified_diff)},
                    )
                    self.workspaces.reset(repair_workspace.workspace)
                    self._event(run_id, "workspace_reset", "completed")
                    repair_patch_reference = self._persist_p1(
                        run_id, response.action, repair_workspace, limits
                    )
                    break
                self._execute_tool(response.action, tools, tracker, messages, repair_events)
        except BudgetExhausted as error:
            run.status = "repair_budget_exhausted"
            run.failure_category = "TOOL_BUDGET_EXHAUSTED"
            repair_events.append({"budget_exhausted": error.budget})
        except ModelProviderError as error:
            run.status = "repair_provider_error"
            run.failure_category = "MODEL_PROVIDER_FAILURE"
            repair_events.append({"provider_error": error.code.value})
        finally:
            self.workspaces.remove(repair_workspace.workspace)

        repair_log = self.artifacts.store_text(
            run_id=run_id,
            kind="model",
            text=json.dumps(
                {
                    "counterexample_id": counterexample.counterexample_id,
                    "events": repair_events,
                    "initial_messages": repair_messages_payload,
                    "patch_artifact": (
                        repair_patch_reference.relative_path
                        if repair_patch_reference is not None
                        else None
                    ),
                    "schema_version": 1,
                    "stage": "repair",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            suffix=".json",
        )
        parameters = dict(run.model_parameters)
        references = dict(parameters.get("artifact_references", {}))
        references["repair_model"] = repair_log.relative_path
        references["repair_patch"] = (
            repair_patch_reference.relative_path if repair_patch_reference else None
        )
        parameters["artifact_references"] = references
        run.model_parameters = parameters

        p1 = self.session.get(PatchArtifact, (run_id, 2))
        if p1 is not None:
            final_verification = self.verifier.verify(
                manifest_path,
                run_id=run_id,
                attempt_number=2,
                benchmark_root=benchmark_root,
            )
            self._event(
                run_id,
                "p1_verification",
                _resolution_status(final_verification.resolved),
                output={"regression": final_verification.regression},
            )

        final_resolution = final_verification.resolved if final_verification is not None else False
        repair_success = initial_verification.resolved is False and final_resolution is True
        repair_regression = bool(final_verification and final_verification.regression)
        if final_verification is not None:
            run.status = "verified_pass" if final_resolution else "repair_failed"
            if final_resolution:
                run.failure_category = None
            elif repair_regression:
                run.failure_category = "REPAIR_INTRODUCED_REGRESSION"
            elif final_verification.resolved is None:
                run.status = "verification_infrastructure_failure"
                run.failure_category = "INFRASTRUCTURE_FAILURE"
            else:
                run.failure_category = "REPAIR_FAILED"
            run.final_resolution = final_verification.resolved
        run.tool_calls = tracker.tool_calls
        run.files_read = tracker.files_read
        run.lines_exposed = tracker.lines_exposed
        usage_parameters = dict(run.model_parameters)
        usage_parameters["agent_usage"] = {
            "content_characters": tracker.content_characters,
            "files_exposed": tracker.files_exposed,
            "model_turns": tracker.model_turns,
            "total_tokens": run.input_tokens + run.output_tokens,
        }
        run.model_parameters = usage_parameters
        run.finished_at = datetime.now(UTC)
        run.latency_ms = int((monotonic() - started_clock) * 1_000)
        metrics = RepairMetrics(
            repair_attempted=True,
            initial_resolution=False,
            final_resolution=(
                final_verification.resolved if final_verification is not None else False
            ),
            counterexample_source=counterexample.source,
            initial_patch_bytes=_patch_bytes(initial_patch.unified_diff),
            final_patch_bytes=_patch_bytes(p1.unified_diff) if p1 is not None else None,
            added_input_tokens=run.input_tokens - initial_input_tokens,
            added_output_tokens=run.output_tokens - initial_output_tokens,
            added_model_latency_ms=repair_model_latency_ms,
            added_latency_ms=int((monotonic() - repair_started) * 1_000),
            added_cost=(
                run.estimated_cost - (initial_estimated_cost or 0.0)
                if run.estimated_cost is not None
                else None
            ),
            repair_success=repair_success,
            repair_induced_regression=repair_regression,
            initial_verification_duration_ms=_verification_duration(initial_verification),
            final_verification_duration_ms=(
                _verification_duration(final_verification) if final_verification else None
            ),
        )
        self._store_metrics(run, metrics)
        self._event(
            run_id,
            "final_state",
            run.status,
            output={"repair_success": repair_success, "resolution": run.final_resolution},
        )
        self.session.flush()
        return ConfigurationCResult(
            run_id,
            run.status,
            initial_verification,
            final_verification,
            counterexample,
            repair_patch_reference,
            metrics,
        )

    def _resume_tracker(
        self, run: Run, limits: AgentBudgets, started_clock: float
    ) -> BudgetTracker:
        usage = run.model_parameters.get("agent_usage", {})
        return BudgetTracker.resumed(
            limits,
            model_turns=_safe_int(usage.get("model_turns")),
            tool_calls=run.tool_calls,
            files_read=run.files_read,
            files_exposed=_safe_int(usage.get("files_exposed")),
            content_characters=_safe_int(usage.get("content_characters")),
            lines_exposed=run.lines_exposed,
            started_at=started_clock,
        )

    def _repair_workspace(self, run_id: str, loaded: LoadedBenchmarkTask) -> LoadedTaskWorkspace:
        workspace_id = f"repair-{hashlib.sha256(run_id.encode()).hexdigest()[:24]}"
        return TaskWorkspaceLoader(
            self.session,
            self.workspaces,
            max_file_bytes=self.settings.max_file_size_bytes,
        ).load(
            task_id=loaded.task.task_id,
            run_id=workspace_id,
            hidden_paths=_HIDDEN_PATHS,
        )

    @staticmethod
    def _apply_for_inspection(
        workspace: LoadedTaskWorkspace, patch: PatchArtifact, limits: AgentBudgets
    ) -> None:
        try:
            validation = PatchValidator(workspace.paths, limits).validate(patch.unified_diff)
            apply_validated_patch(patch.unified_diff, validation, workspace.workspace.path)
        except PatchValidationError:
            return

    def _execute_tool(
        self,
        action: ToolCallAction,
        tools: ConstrainedRepositoryTools,
        tracker: BudgetTracker,
        messages: list[ModelMessage],
        events: list[dict[str, Any]],
    ) -> None:
        messages.append(ModelMessage(role="assistant", content=action.model_dump_json()))
        try:
            result = tools.execute(action.tool, action.arguments.model_dump())
            payload: dict[str, Any] = {
                "content": result.content,
                "ok": True,
                "paths": result.paths,
                "tool": result.tool,
                "truncated": result.truncated,
            }
        except (PathPolicyError, ToolInputError) as error:
            payload = {
                "error": type(error).__name__,
                "message": str(error),
                "ok": False,
                "tool": action.tool,
            }
            tracker.record_exposure(json.dumps(payload, sort_keys=True))
        events.append({"action": action.model_dump(mode="json"), "result": payload})
        messages.append(
            ModelMessage(
                role="tool",
                name=action.tool,
                tool_call_id=f"repair-tool-{tracker.tool_calls:04d}",
                content=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            )
        )

    def _persist_p1(
        self,
        run_id: str,
        action: SubmitPatchAction,
        workspace: LoadedTaskWorkspace,
        limits: AgentBudgets,
    ) -> ArtifactReference:
        validation: PatchValidationResult | None = None
        applied = False
        try:
            validation = PatchValidator(workspace.paths, limits).validate(action.unified_diff)
            apply_validated_patch(action.unified_diff, validation, workspace.workspace.path)
            applied = True
        except PatchValidationError:
            pass
        reference = self.artifacts.store_text(
            run_id=run_id,
            kind="patches",
            text=action.unified_diff,
            suffix=".patch",
        )
        self.session.add(
            PatchArtifact(
                run_id=run_id,
                attempt_number=2,
                unified_diff=action.unified_diff,
                files_changed=validation.files_changed if validation else [],
                lines_added=validation.lines_added if validation else 0,
                lines_removed=validation.lines_removed if validation else 0,
                applied_successfully=applied,
            )
        )
        self.session.flush()
        return reference

    def _finish_without_repair(
        self,
        run: Run,
        *,
        initial_resolution: bool | None,
        initial_patch_bytes: int | None,
        started_clock: float,
        verification: VerificationRun | None = None,
    ) -> RepairMetrics:
        metrics = RepairMetrics(
            repair_attempted=False,
            initial_resolution=initial_resolution,
            final_resolution=initial_resolution,
            counterexample_source=None,
            initial_patch_bytes=initial_patch_bytes,
            final_patch_bytes=None,
            added_input_tokens=0,
            added_output_tokens=0,
            added_model_latency_ms=0,
            added_latency_ms=0,
            added_cost=None,
            repair_success=False,
            repair_induced_regression=False,
            initial_verification_duration_ms=(
                _verification_duration(verification) if verification else 0
            ),
            final_verification_duration_ms=None,
        )
        run.repair_attempted = False
        run.final_resolution = initial_resolution
        if verification is not None and initial_resolution is True:
            run.status = "verified_pass"
            run.failure_category = None
        elif verification is not None and initial_resolution is None:
            run.status = "verification_infrastructure_failure"
            run.failure_category = "INFRASTRUCTURE_FAILURE"
        run.finished_at = datetime.now(UTC)
        run.latency_ms = int((monotonic() - started_clock) * 1_000)
        self._store_metrics(run, metrics)
        self._event(
            run.run_id,
            "final_state",
            run.status,
            output={"repair_attempted": False, "resolution": initial_resolution},
        )
        self.session.flush()
        return metrics

    @staticmethod
    def _store_metrics(run: Run, metrics: RepairMetrics) -> None:
        parameters = dict(run.model_parameters)
        parameters["repair_metrics"] = asdict(metrics)
        run.model_parameters = parameters

    def _event(
        self,
        run_id: str,
        operation: str,
        status: str,
        *,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        error_type: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        event_id = f"te-{hashlib.sha256(f'{run_id}:{self._sequence}'.encode()).hexdigest()[:24]}"
        self.session.add(
            TraceEvent(
                event_id=event_id,
                run_id=run_id,
                sequence_number=self._sequence,
                parent_event_id=self._last_event_id,
                operation=operation,
                started_at=now,
                finished_at=now,
                status=status,
                input_summary=_summary(input),
                output_summary=_summary(output),
                error_type=error_type,
            )
        )
        self.session.flush()
        self._last_event_id = event_id
        self._sequence += 1


def _repair_messages(
    loaded: LoadedBenchmarkTask,
    patch: PatchArtifact,
    counterexample: Counterexample,
) -> list[ModelMessage]:
    task = loaded.task
    return [
        ModelMessage(
            role="system",
            content=(
                "Configuration C repair: return one structured action per turn. You may use "
                "only list_tree, read_file, and search_code, then submit exactly one complete "
                "replacement unified diff against the original base commit. The replacement "
                "must not be incremental against P0. No further repair will be offered."
            ),
        ),
        ModelMessage(
            role="user",
            content=json.dumps(
                {
                    "counterexample": json.loads(counterexample.sanitized_feedback),
                    "failed_candidate": patch.unified_diff,
                    "task": {
                        "allowed_paths": task.allowed_paths,
                        "description": task.description,
                        "forbidden_paths": task.forbidden_paths,
                        "task_id": task.task_id,
                        "title": task.title,
                        "visible_test_command": task.visible_test_command,
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    ]


def _patch_bytes(value: str) -> int:
    return len(value.encode("utf-8", errors="replace"))


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _verification_duration(verification: VerificationRun) -> int:
    return sum(result.duration_ms for result in verification.results)


def _resolution_status(value: bool | None) -> str:
    return "passed" if value is True else "failed" if value is False else "infrastructure_error"


def _summary(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return rendered if len(rendered) <= 2_000 else f"{rendered[:1997]}..."


def _add_cost(current: float | None, additional: float | None) -> float | None:
    if additional is None:
        return current
    return (current or 0.0) + additional
