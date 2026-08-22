"""Google Gemini adapter for AgentTrace's structured action boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from time import monotonic_ns
from typing import Any, cast

from pydantic import BaseModel, JsonValue, ValidationError

from app.agent.actions import (
    ListTreeArguments,
    ReadFileArguments,
    SearchCodeArguments,
    SubmitPatchAction,
    ToolArguments,
    ToolCallAction,
    ToolName,
)
from app.agent.pricing import TokenPricing
from app.agent.provider import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderErrorCode,
)

_ARGUMENT_MODELS: dict[str, type[BaseModel]] = {
    "list_tree": ListTreeArguments,
    "read_file": ReadFileArguments,
    "search_code": SearchCodeArguments,
    "submit_patch": SubmitPatchAction,
}
_TOOL_DESCRIPTIONS = {
    "list_tree": "List a bounded repository tree below the requested agent-readable path.",
    "read_file": "Read one bounded agent-readable repository file.",
    "search_code": "Search agent-readable repository text with bounded literal matching.",
    "submit_patch": (
        "Submit the complete final Git-style unified diff and concise observable rationale. "
        "This terminates the current candidate-generation stage."
    ),
}
_SUPPORTED_PARAMETERS = {
    "max_output_tokens",
    "seed",
    "stop_sequences",
    "temperature",
    "thinking_config",
    "top_k",
    "top_p",
}


class GeminiModelProvider:
    """Translate provider-neutral requests into required Gemini function calls.

    Gemini only proposes repository actions. AgentTrace's existing orchestrator
    remains the sole tool executor and patch-application authority.
    """

    provider_name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None,
        request_timeout_seconds: float = 120.0,
        max_retries: int = 0,
        pricing: TokenPricing | None = None,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if max_retries < 0 or max_retries > 10:
            raise ValueError("max_retries must be between 0 and 10")
        self._api_key = api_key.strip() if api_key is not None else None
        self._request_timeout_seconds = request_timeout_seconds
        self._max_retries = max_retries
        self._pricing = pricing or TokenPricing()
        self._client: Any | None = None
        self._types: Any | None = None

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Return exactly one Pydantic-validated AgentTrace action."""

        client, types = self._client_or_error()
        parameters = _provider_parameters(request.model_parameters)
        timeout = min(request.timeout_seconds, self._request_timeout_seconds)
        system_instruction, contents = _gemini_contents(request, types)
        config = types.GenerateContentConfig(
            **parameters,
            system_instruction=system_instruction or None,
            tools=[_action_tool(request.available_tools, types)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
            http_options=types.HttpOptions(timeout=max(1, int(timeout * 1_000))),
        )
        started = monotonic_ns()
        try:
            response = client.models.generate_content(
                model=request.model_identifier,
                contents=contents,
                config=config,
            )
        except Exception as error:
            raise _normalize_gemini_error(error) from error
        latency_ms = (monotonic_ns() - started) // 1_000_000

        request_id = _optional_text(getattr(response, "response_id", None))
        finish_reason = _finish_reason(response)
        if _blocked(response, finish_reason):
            raise ModelProviderError(
                ProviderErrorCode.CONTENT_FILTER,
                "Gemini declined to produce an AgentTrace action",
                provider_name=self.provider_name,
                provider_request_id=request_id,
            )
        action = _parse_function_action(response, request, request_id=request_id)
        usage = _usage(response, self._pricing)
        actual_model = _optional_text(getattr(response, "model_version", None))
        return ModelResponse(
            action=action,
            model_identifier=actual_model or request.model_identifier,
            model_parameters=dict(request.model_parameters),
            usage=usage,
            latency_ms=latency_ms,
            provider_request_id=request_id,
            finish_reason=finish_reason or (
                "tool_call" if isinstance(action, ToolCallAction) else "stop"
            ),
            provider_status="completed",
        )

    def _client_or_error(self) -> tuple[Any, Any]:
        if not self._api_key:
            raise ModelProviderError(
                ProviderErrorCode.AUTHENTICATION,
                "GEMINI_API_KEY is not configured",
                provider_name=self.provider_name,
            )
        if self._client is not None and self._types is not None:
            return self._client, self._types
        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise ModelProviderError(
                ProviderErrorCode.UNAVAILABLE,
                "The Google Gen AI Python SDK is not installed",
                provider_name=self.provider_name,
            ) from error
        try:
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(
                    timeout=max(1, int(self._request_timeout_seconds * 1_000)),
                    retry_options=types.HttpRetryOptions(attempts=self._max_retries + 1),
                ),
            )
            self._types = types
        except Exception as error:
            raise _normalize_gemini_error(error) from error
        return self._client, self._types


def _provider_parameters(values: Mapping[str, JsonValue]) -> dict[str, Any]:
    unknown = sorted(set(values) - _SUPPORTED_PARAMETERS)
    if unknown:
        raise ModelProviderError(
            ProviderErrorCode.INVALID_REQUEST,
            "Unsupported Gemini model parameters: " + ", ".join(unknown),
            provider_name=GeminiModelProvider.provider_name,
        )
    return dict(values)


def _gemini_contents(request: ModelRequest, types: Any) -> tuple[str, list[Any]]:
    system = "\n\n".join(
        message.content for message in request.messages if message.role == "system"
    )
    contents: list[Any] = []
    for message in request.messages:
        if message.role == "system":
            continue
        role = "model" if message.role == "assistant" else "user"
        if message.role == "tool":
            text = json.dumps(
                {
                    "agenttrace_tool_result": json.loads(message.content),
                    "tool_call_id": message.tool_call_id,
                    "tool_name": message.name,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            text = message.content
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=text)])
        )
    return system, contents


def _action_tool(available_tools: list[str], types: Any) -> Any:
    names = [*available_tools, "submit_patch"]
    declarations = [
        types.FunctionDeclaration(
            name=name,
            description=_TOOL_DESCRIPTIONS[name],
            parameters_json_schema=_ARGUMENT_MODELS[name].model_json_schema(),
        )
        for name in names
    ]
    return types.Tool(function_declarations=declarations)


def _parse_function_action(
    response: Any,
    request: ModelRequest,
    *,
    request_id: str | None,
) -> ToolCallAction | SubmitPatchAction:
    calls = list(getattr(response, "function_calls", None) or ())
    if len(calls) != 1:
        raise ModelProviderError(
            ProviderErrorCode.RESPONSE_VALIDATION,
            "Gemini response must contain exactly one AgentTrace function call",
            provider_name=GeminiModelProvider.provider_name,
            provider_request_id=request_id,
        )
    call = calls[0]
    name = _optional_text(getattr(call, "name", None))
    permitted = {*request.available_tools, "submit_patch"}
    if name not in permitted:
        raise ModelProviderError(
            ProviderErrorCode.RESPONSE_VALIDATION,
            "Gemini returned an unavailable AgentTrace action",
            provider_name=GeminiModelProvider.provider_name,
            provider_request_id=request_id,
        )
    try:
        raw_arguments = getattr(call, "args", None)
        if isinstance(raw_arguments, str):
            payload = json.loads(raw_arguments)
        elif isinstance(raw_arguments, Mapping):
            payload = dict(raw_arguments)
        else:
            raise ValueError("function arguments are not an object")
        if name == "submit_patch":
            payload.setdefault("action_type", "submit_patch")
            return SubmitPatchAction.model_validate(payload)
        arguments = _ARGUMENT_MODELS[name].model_validate(payload)
        return ToolCallAction(
            tool=cast(ToolName, name),
            arguments=cast(ToolArguments, arguments),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, ValidationError) as error:
        raise ModelProviderError(
            ProviderErrorCode.RESPONSE_VALIDATION,
            "Gemini returned malformed structured action arguments",
            provider_name=GeminiModelProvider.provider_name,
            provider_request_id=request_id,
        ) from error


def _usage(response: Any, pricing: TokenPricing) -> ModelUsage:
    provider_usage = getattr(response, "usage_metadata", None)
    input_tokens = _non_negative_int(
        getattr(provider_usage, "prompt_token_count", 0)
    ) + _non_negative_int(
        getattr(provider_usage, "tool_use_prompt_token_count", 0)
    )
    output_tokens = _non_negative_int(
        getattr(provider_usage, "candidates_token_count", 0)
    ) + _non_negative_int(
        getattr(provider_usage, "thoughts_token_count", 0)
    )
    reported_total = getattr(provider_usage, "total_token_count", None)
    total_tokens = (
        max(input_tokens + output_tokens, _non_negative_int(reported_total))
        if reported_total is not None
        else input_tokens + output_tokens
    )
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost=pricing.estimate(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def _normalize_gemini_error(error: Exception) -> ModelProviderError:
    status_code = getattr(error, "code", None)
    if not isinstance(status_code, int):
        status_code = getattr(error, "status_code", None)
    name = type(error).__name__.casefold()
    safe_error_text = str(error).casefold()
    credential_error = status_code in {400, 401, 403} and any(
        marker in safe_error_text
        for marker in (
            "api key",
            "api_key_invalid",
            "permission_denied",
            "unauthenticated",
        )
    )
    if status_code in {401, 403} or credential_error:
        code, retryable, message = (
            ProviderErrorCode.AUTHENTICATION,
            False,
            "Gemini authentication failed",
        )
    elif status_code == 429 or "ratelimit" in name or "resourceexhausted" in name:
        code, retryable, message = (
            ProviderErrorCode.RATE_LIMIT,
            True,
            "Gemini rate limit was reached",
        )
    elif status_code in {408, 504} or "timeout" in name:
        code, retryable, message = (
            ProviderErrorCode.TIMEOUT,
            True,
            "Gemini request timed out",
        )
    elif isinstance(status_code, int) and status_code >= 500:
        code, retryable, message = (
            ProviderErrorCode.UNAVAILABLE,
            True,
            "Gemini returned a provider error",
        )
    elif isinstance(status_code, int) and 400 <= status_code < 500:
        code, retryable, message = (
            ProviderErrorCode.INVALID_REQUEST,
            False,
            "Gemini rejected the model request",
        )
    elif "connect" in name or "network" in name:
        code, retryable, message = (
            ProviderErrorCode.UNAVAILABLE,
            True,
            "Gemini could not be reached",
        )
    else:
        code, retryable, message = (
            ProviderErrorCode.UNKNOWN,
            False,
            "Unexpected Google Gen AI SDK failure",
        )
    return ModelProviderError(
        code,
        message,
        provider_name=GeminiModelProvider.provider_name,
        retryable=retryable,
    )


def _finish_reason(response: Any) -> str | None:
    candidates = list(getattr(response, "candidates", None) or ())
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    value = getattr(reason, "value", reason)
    return str(value) if value is not None else None


def _blocked(response: Any, finish_reason: str | None) -> bool:
    prompt_feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(prompt_feedback, "block_reason", None)
    if block_reason is not None and str(getattr(block_reason, "value", block_reason)) not in {
        "0",
        "BLOCK_REASON_UNSPECIFIED",
        "None",
    }:
        return True
    return finish_reason is not None and any(
        marker in finish_reason.upper()
        for marker in ("SAFETY", "BLOCKLIST", "PROHIBITED", "SPII", "RECITATION")
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
