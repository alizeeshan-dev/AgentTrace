"""Stable, evaluator-safe prompts and observable model-event records."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import JsonValue

from app.agent.actions import SubmitPatchAction
from app.agent.context import PreparedRepositoryContext
from app.agent.provider import ModelMessage, ModelResponse
from app.tasks import LoadedTaskDefinition


def direct_messages(
    loaded: LoadedTaskDefinition,
    context: PreparedRepositoryContext,
) -> list[ModelMessage]:
    """Build Configuration A's fixed no-tool request without evaluator data."""

    return [
        ModelMessage(
            role="system",
            content=(
                "Configuration A: return exactly one structured submit_patch action with a "
                "unified diff and concise rationale. Repository tools, verification feedback, "
                "and repair are unavailable. Do not describe an action in prose."
            ),
        ),
        ModelMessage(
            role="user",
            content=json.dumps(
                {"repository_context": context.content, "task": _agent_task_payload(loaded)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    ]


def tool_agent_messages(loaded: LoadedTaskDefinition) -> list[ModelMessage]:
    """Build Configuration B's metadata-only initial request."""

    return [
        ModelMessage(
            role="system",
            content=(
                "Configuration B: return exactly one structured action per turn. You may use "
                "only list_tree, read_file, and search_code, then finish with one submit_patch. "
                "No shell, hidden tests, SBFL hint, verification feedback, or repair is available."
            ),
        ),
        ModelMessage(
            role="user",
            content=json.dumps(
                {
                    "repository": _agent_repository_payload(loaded),
                    "task": _agent_task_payload(loaded),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    ]


def model_event(response: ModelResponse) -> dict[str, Any]:
    """Record observable response metadata without storing private reasoning."""

    action = response.action
    if isinstance(action, SubmitPatchAction):
        encoded = action.unified_diff.encode("utf-8", errors="replace")
        action_payload: dict[str, Any] = {
            "action_type": "submit_patch",
            "explanation": (
                action.explanation.model_dump(mode="json") if action.explanation else None
            ),
            "patch_bytes": len(encoded),
            "patch_sha256": hashlib.sha256(encoded).hexdigest(),
            "rationale": action.rationale,
        }
    else:
        action_payload = action.model_dump(mode="json")
    return {
        "action": action_payload,
        "finish_reason": response.finish_reason,
        "latency_ms": response.latency_ms,
        "model_identifier": response.model_identifier,
        "model_parameters": response.model_parameters,
        "provider_status": response.provider_status,
        "provider_request_id": response.provider_request_id,
        "usage": response.usage.model_dump(mode="json"),
    }


def _agent_task_payload(loaded: LoadedTaskDefinition) -> dict[str, Any]:
    task = loaded.task
    return {
        "allowed_paths": task.allowed_paths,
        "description": task.description,
        "difficulty": task.difficulty,
        "forbidden_paths": task.forbidden_paths,
        "task_category": task.task_category,
        "task_id": task.task_id,
        "task_source": task.task_source,
        "title": task.title,
        "visible_test_command": task.visible_test_command,
    }


def _agent_repository_payload(loaded: LoadedTaskDefinition) -> dict[str, JsonValue]:
    return {
        "base_commit": loaded.task.base_commit,
        "repository_reference": loaded.task.repository,
    }
