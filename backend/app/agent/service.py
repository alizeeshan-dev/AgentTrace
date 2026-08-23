"""Phase 5 orchestration for direct and constrained tool-using patch runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Literal

from pydantic import JsonValue
from sqlalchemy.orm import Session

from app.agent.actions import SubmitPatchAction, ToolCallAction
from app.agent.budgets import AgentBudgets, BudgetExhausted, BudgetTracker
from app.agent.context import PreparedRepositoryContext, prepare_repository_context
from app.agent.patches import (
    PatchValidationError,
    PatchValidationResult,
    PatchValidator,
    apply_validated_patch,
)
from app.agent.protocol import direct_messages, model_event, tool_agent_messages
from app.agent.provider import (
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
)
from app.agent.tools import ConstrainedRepositoryTools, ToolInputError
from app.artifacts import ArtifactReference, ArtifactStore
from app.config import Settings
from app.db.models import PatchArtifact as PatchArtifactRecord
from app.db.models import Repository as RepositoryRecord
from app.db.models import Run as RunRecord
from app.db.models import Task as TaskRecord
from app.repositories.path_policy import PathPolicyError
from app.repositories.workspace import WorkspaceManager
from app.schemas.research import PatchArtifact
from app.services.workspaces import LoadedTaskWorkspace, TaskWorkspaceLoader
from app.tasks import LoadedTaskDefinition, load_task_definition

ConfigurationId = Literal["A", "B"]
_TOOLS = ["list_tree", "read_file", "search_code"]
_HIDDEN_WORKSPACE_PATHS = (".agenttrace-evaluator/", "hidden_tests/")


class AgentRunError(RuntimeError):
    """Raised when a run cannot be prepared without violating its contract."""


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    run_id: str
    task_id: str
    configuration_id: ConfigurationId
    status: str
    failure_category: str | None
    patch: PatchArtifact | None
    patch_artifact: ArtifactReference | None
    model_artifact: ArtifactReference
    context_sha256: str | None
    budget_exhausted: str | None


class AgentRunService:
    """Run the two pre-verification experimental conditions.

    This service intentionally stops after applying a validated candidate to a
    disposable checkout.  It executes no tests and returns no verification or
    SBFL evidence to the model.
    """

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider: ModelProvider,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider
        self.workspaces = WorkspaceManager(settings.effective_workspace_root)
        self.artifacts = ArtifactStore(
            settings.effective_artifact_root,
            max_artifact_bytes=settings.max_artifact_size_bytes,
        )

    def run_direct(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        model_identifier: str,
        model_parameters: dict[str, JsonValue] | None = None,
        budgets: AgentBudgets | None = None,
        benchmark_root: str | Path | None = None,
    ) -> AgentRunResult:
        """Configuration A: one deterministic context, one model response, stop."""

        return self._run(
            manifest_path,
            run_id=run_id,
            configuration_id="A",
            model_identifier=model_identifier,
            model_parameters=model_parameters or {},
            budgets=budgets or AgentBudgets(max_model_turns=1, max_tool_calls=0),
            benchmark_root=benchmark_root,
        )

    def run_tool_agent(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        model_identifier: str,
        model_parameters: dict[str, JsonValue] | None = None,
        budgets: AgentBudgets | None = None,
        benchmark_root: str | Path | None = None,
    ) -> AgentRunResult:
        """Configuration B: bounded inspection actions followed by one patch."""

        return self._run(
            manifest_path,
            run_id=run_id,
            configuration_id="B",
            model_identifier=model_identifier,
            model_parameters=model_parameters or {},
            budgets=budgets or AgentBudgets(),
            benchmark_root=benchmark_root,
        )

    def _run(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        configuration_id: ConfigurationId,
        model_identifier: str,
        model_parameters: dict[str, JsonValue],
        budgets: AgentBudgets,
        benchmark_root: str | Path | None,
    ) -> AgentRunResult:
        if self.session.get(RunRecord, run_id) is not None:
            raise AgentRunError(f"run_id already exists: {run_id}")
        loaded = load_task_definition(manifest_path, benchmark_root=benchmark_root)
        task, repository = self._require_persisted_binding(loaded)
        started_at = datetime.now(UTC)
        started_clock = monotonic()
        tracker = BudgetTracker(budgets)
        workspace_loader = TaskWorkspaceLoader(
            self.session,
            self.workspaces,
            max_file_bytes=min(self.settings.max_file_size_bytes, budgets.max_file_bytes),
            max_tree_entries=budgets.max_tree_entries,
        )
        task_workspace = workspace_loader.load(
            task_id=task.task_id,
            run_id=run_id,
            hidden_paths=_HIDDEN_WORKSPACE_PATHS,
        )

        status = "running"
        failure_category: str | None = None
        budget_exhausted: str | None = None
        patch_schema: PatchArtifact | None = None
        patch_reference: ArtifactReference | None = None
        context: PreparedRepositoryContext | None = None
        input_tokens = 0
        output_tokens = 0
        estimated_cost: float | None = None
        events: list[dict[str, Any]] = []
        initial_messages: list[ModelMessage] = []
        messages: list[ModelMessage] = []

        try:
            if configuration_id == "A":
                context = prepare_repository_context(
                    task_workspace.paths,
                    task_id=task.task_id,
                    base_commit=repository.base_commit,
                    max_files_read=budgets.max_files_read,
                    max_files_exposed=budgets.max_files_exposed,
                    max_content_characters=budgets.max_content_characters,
                )
                initial_messages = direct_messages(loaded, context)
            else:
                initial_messages = tool_agent_messages(loaded)
            messages = list(initial_messages)
            if configuration_id == "A":
                tracker.begin_model_turn()
                response = self._generate(
                    messages,
                    tracker=tracker,
                    model_identifier=model_identifier,
                    model_parameters=model_parameters,
                    configuration_id=configuration_id,
                    available_tools=[],
                )
                input_tokens += response.usage.input_tokens
                output_tokens += response.usage.output_tokens
                estimated_cost = _add_cost(estimated_cost, response.usage.estimated_cost)
                events.append(model_event(response))
                tracker.check_wall_clock()
                if isinstance(response.action, ToolCallAction):
                    status = "invalid_action"
                    failure_category = "TOOL_MISUSE"
                else:
                    patch_schema, patch_reference, status, failure_category = self._submit_patch(
                        response.action,
                        task_workspace,
                        budgets,
                        run_id=run_id,
                    )
            else:
                tools = ConstrainedRepositoryTools(task_workspace.paths, tracker)
                while True:
                    tracker.begin_model_turn()
                    response = self._generate(
                        messages,
                        tracker=tracker,
                        model_identifier=model_identifier,
                        model_parameters=model_parameters,
                        configuration_id=configuration_id,
                        available_tools=_TOOLS,
                    )
                    input_tokens += response.usage.input_tokens
                    output_tokens += response.usage.output_tokens
                    estimated_cost = _add_cost(
                        estimated_cost, response.usage.estimated_cost
                    )
                    events.append(model_event(response))
                    tracker.check_wall_clock()
                    action = response.action
                    if isinstance(action, SubmitPatchAction):
                        (
                            patch_schema,
                            patch_reference,
                            status,
                            failure_category,
                        ) = self._submit_patch(action, task_workspace, budgets, run_id=run_id)
                        break
                    messages.append(
                        ModelMessage(role="assistant", content=action.model_dump_json())
                    )
                    try:
                        tool_result = tools.execute(action.tool, action.arguments.model_dump())
                        tool_payload = {
                            "content": tool_result.content,
                            "ok": True,
                            "paths": tool_result.paths,
                            "tool": tool_result.tool,
                            "truncated": tool_result.truncated,
                        }
                    except (PathPolicyError, ToolInputError) as error:
                        tool_payload = {
                            "error": type(error).__name__,
                            "message": str(error),
                            "ok": False,
                            "tool": action.tool,
                        }
                        bounded_error = json.dumps(
                            tool_payload, sort_keys=True, separators=(",", ":")
                        )
                        tracker.record_exposure(bounded_error)
                    tool_content = json.dumps(
                        tool_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    events.append(
                        {"action": action.model_dump(mode="json"), "result": tool_payload}
                    )
                    messages.append(
                        ModelMessage(
                            role="tool",
                            name=action.tool,
                            tool_call_id=f"tool-{tracker.tool_calls:04d}",
                            content=tool_content,
                        )
                    )
        except BudgetExhausted as error:
            status = "budget_exhausted"
            failure_category = "TOOL_MISUSE"
            budget_exhausted = error.budget
            events.append({"budget_exhausted": error.budget})
        except ModelProviderError as error:
            status = "provider_error"
            failure_category = "MODEL_PROVIDER_FAILURE"
            events.append(
                {
                    "provider_error": {
                        "code": error.code.value,
                        "provider": error.provider_name,
                        "provider_request_id": error.provider_request_id,
                        "retryable": error.retryable,
                    }
                }
            )
        finally:
            try:
                self.workspaces.reset(task_workspace.workspace)
            finally:
                self.workspaces.remove(task_workspace.workspace)

        finished_at = datetime.now(UTC)
        duration_ms = int((monotonic() - started_clock) * 1_000)
        model_log = {
            "budget": budgets.model_dump(mode="json"),
            "configuration_id": configuration_id,
            "context_sha256": context.sha256 if context is not None else None,
            "events": events,
            "failure_category": failure_category,
            "final_status": status,
            "initial_messages": [message.model_dump(mode="json") for message in initial_messages],
            "patch_artifact": (
                patch_reference.relative_path if patch_reference is not None else None
            ),
            "provider": self.provider.provider_name,
            "schema_version": 1,
            "task_id": task.task_id,
        }
        model_reference = self.artifacts.store_text(
            run_id=run_id,
            kind="model",
            text=json.dumps(model_log, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            suffix=".json",
        )
        self._persist(
            run_id=run_id,
            task=task,
            configuration_id=configuration_id,
            model_identifier=model_identifier,
            model_parameters=model_parameters,
            budgets=budgets,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimated_cost,
            tool_calls=tracker.tool_calls,
            model_turns=tracker.model_turns,
            files_read=context.files_read if context is not None else tracker.files_read,
            files_exposed=(context.files_exposed if context is not None else tracker.files_exposed),
            content_characters=tracker.content_characters,
            lines_exposed=context.lines_exposed if context is not None else tracker.lines_exposed,
            failure_category=failure_category,
            patch=patch_schema,
            model_artifact=model_reference.relative_path,
            patch_artifact=(patch_reference.relative_path if patch_reference is not None else None),
        )
        return AgentRunResult(
            run_id=run_id,
            task_id=task.task_id,
            configuration_id=configuration_id,
            status=status,
            failure_category=failure_category,
            patch=patch_schema,
            patch_artifact=patch_reference,
            model_artifact=model_reference,
            context_sha256=context.sha256 if context is not None else None,
            budget_exhausted=budget_exhausted,
        )

    def _generate(
        self,
        messages: list[ModelMessage],
        *,
        tracker: BudgetTracker,
        model_identifier: str,
        model_parameters: dict[str, JsonValue],
        configuration_id: ConfigurationId,
        available_tools: list[str],
    ) -> ModelResponse:
        remaining = tracker.limits.wall_clock_seconds - (monotonic() - tracker.started_at)
        if remaining <= 0:
            raise BudgetExhausted("wall_clock_seconds")
        request = ModelRequest(
            model_identifier=model_identifier,
            model_parameters=model_parameters,
            messages=messages,
            available_tools=available_tools,
            timeout_seconds=remaining,
            metadata={"configuration_id": configuration_id, "phase": 5},
        )
        return self.provider.generate(request)

    def _submit_patch(
        self,
        action: SubmitPatchAction,
        task_workspace: LoadedTaskWorkspace,
        budgets: AgentBudgets,
        *,
        run_id: str,
    ) -> tuple[PatchArtifact | None, ArtifactReference | None, str, str | None]:
        validation: PatchValidationResult | None = None
        failure_category: str | None = None
        try:
            validation = PatchValidator(task_workspace.paths, budgets).validate(action.unified_diff)
            apply_validated_patch(
                action.unified_diff,
                validation,
                task_workspace.workspace.path,
            )
            status = "patch_submitted"
        except PatchValidationError as error:
            status = "patch_rejected"
            failure_category = (
                "PATCH_DID_NOT_APPLY" if "appl" in str(error).casefold() else "INVALID_PATCH"
            )

        try:
            encoded = action.unified_diff.encode("utf-8")
        except UnicodeEncodeError:
            encoded = b""
        patch_reference: ArtifactReference | None = None
        patch_schema: PatchArtifact | None = None
        if encoded:
            if len(encoded) <= self.artifacts.max_artifact_bytes:
                patch_reference = self.artifacts.store_bytes(
                    run_id=run_id,
                    kind="patches",
                    data=encoded,
                    suffix=".patch",
                )
            patch_schema = PatchArtifact(
                run_id=run_id,
                attempt_number=1,
                unified_diff=action.unified_diff,
                files_changed=validation.files_changed if validation is not None else [],
                lines_added=validation.lines_added if validation is not None else 0,
                lines_removed=validation.lines_removed if validation is not None else 0,
                applied_successfully=status == "patch_submitted",
            )
        return patch_schema, patch_reference, status, failure_category

    def _require_persisted_binding(
        self, loaded: LoadedTaskDefinition
    ) -> tuple[TaskRecord, RepositoryRecord]:
        task = self.session.get(TaskRecord, loaded.task.task_id)
        if task is None:
            raise AgentRunError("task must be qualified and persisted before an agent run")
        repository = self.session.get(RepositoryRecord, task.repository_id)
        if repository is None:
            raise AgentRunError("persisted task repository is missing")
        if (
            repository.source_type == "external_git"
            and not repository.trusted_for_local_execution
        ):
            raise AgentRunError(
                "External repository execution is blocked until explicit trust is granted"
            )
        if loaded.repository_path is None:
            raise AgentRunError("agent runs require a locally managed Git repository")
        try:
            source = Path(repository.source).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise AgentRunError("persisted repository source is unavailable") from error
        if source != loaded.repository_path or repository.base_commit != loaded.task.base_commit:
            raise AgentRunError("persisted repository binding differs from the manifest")
        expected = {
            "allowed_paths": loaded.task.allowed_paths,
            "description": loaded.task.description,
            "forbidden_paths": loaded.task.forbidden_paths,
            "title": loaded.task.title,
            "visible_test_command": loaded.task.visible_test_command,
        }
        if any(getattr(task, field) != value for field, value in expected.items()):
            raise AgentRunError("persisted task contract differs from the manifest")
        return task, repository

    def _persist(
        self,
        *,
        run_id: str,
        task: TaskRecord,
        configuration_id: ConfigurationId,
        model_identifier: str,
        model_parameters: dict[str, JsonValue],
        budgets: AgentBudgets,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float | None,
        tool_calls: int,
        model_turns: int,
        files_read: int,
        files_exposed: int,
        content_characters: int,
        lines_exposed: int,
        failure_category: str | None,
        patch: PatchArtifact | None,
        model_artifact: str,
        patch_artifact: str | None,
    ) -> None:
        stored_parameters: dict[str, Any] = {
            "agent_budgets": budgets.model_dump(mode="json"),
            "agent_usage": {
                "content_characters": content_characters,
                "files_exposed": files_exposed,
                "model_turns": model_turns,
                "total_tokens": input_tokens + output_tokens,
            },
            "artifact_references": {
                "model": model_artifact,
                "patch": patch_artifact,
            },
            "phase": 5,
            "provider": self.provider.provider_name,
            "provider_parameters": model_parameters,
            "protocol_version": f"configuration-{configuration_id.lower()}-v1",
        }
        self.session.add(
            RunRecord(
                run_id=run_id,
                task_id=task.task_id,
                configuration_id=configuration_id,
                model=model_identifier,
                model_parameters=stored_parameters,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=estimated_cost,
                tool_calls=tool_calls,
                files_read=files_read,
                lines_exposed=lines_exposed,
                repair_attempted=False,
                final_resolution=None,
                failure_category=failure_category,
            )
        )
        # The ORM records intentionally have no relationship objects; flush
        # the foreign-key parent explicitly before the candidate row.
        self.session.flush()
        if patch is not None:
            self.session.add(PatchArtifactRecord(**patch.model_dump()))
        self.session.flush()


def _add_cost(current: float | None, additional: float | None) -> float | None:
    if additional is None:
        return current
    return (current or 0.0) + additional
