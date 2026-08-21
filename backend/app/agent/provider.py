"""Provider-independent model boundary and deterministic local fake."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field, JsonValue, StringConstraints, field_validator

from app.agent.actions import AgentAction, SubmitPatchAction, ToolCallAction
from app.schemas.common import Identifier, ResearchSchema
from app.security import find_credential_key

ModelText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ModelMessage(ResearchSchema):
    """One provider-neutral conversation item."""

    role: Literal["system", "user", "assistant", "tool"]
    # Repository exposure is bounded independently by ``AgentBudgets``.  This
    # envelope leaves room for JSON escaping and protocol metadata around the
    # largest permitted prepared context.
    content: Annotated[str, StringConstraints(min_length=1, max_length=25_000_000)]
    name: Identifier | None = None
    tool_call_id: Identifier | None = None


class ModelRequest(ResearchSchema):
    """A structured-action request which can be translated by any provider adapter."""

    model_identifier: ModelText
    model_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    messages: Annotated[list[ModelMessage], Field(min_length=1, max_length=500)]
    available_tools: Annotated[list[str], Field(max_length=3)] = Field(default_factory=list)
    timeout_seconds: Annotated[float, Field(gt=0, le=86_400)]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    response_format: Literal["agent_action"] = "agent_action"

    @field_validator("available_tools")
    @classmethod
    def tools_are_known_and_unique(cls, values: list[str]) -> list[str]:
        approved = {"list_tree", "read_file", "search_code"}
        if any(value not in approved for value in values):
            raise ValueError("available tools must be approved repository tools")
        if len(values) != len(set(values)):
            raise ValueError("available tools must be unique")
        return values

    @field_validator("model_parameters", "metadata")
    @classmethod
    def credentials_are_not_request_data(
        cls, values: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        sensitive = find_credential_key(values)
        if sensitive is not None:
            raise ValueError(
                "model requests cannot contain credential fields; configure provider "
                "authentication outside experiment data"
            )
        return values


class ModelUsage(ResearchSchema):
    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0


class ModelResponse(ResearchSchema):
    """One validated action plus provider execution metadata."""

    action: AgentAction
    model_identifier: ModelText
    model_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    latency_ms: Annotated[int, Field(ge=0)]
    provider_request_id: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = (
        None
    )
    finish_reason: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None

    @field_validator("model_parameters")
    @classmethod
    def credentials_are_not_response_data(
        cls, values: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if find_credential_key(values) is not None:
            raise ValueError("model response metadata cannot contain credential fields")
        return values


class ProviderErrorCode(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    INVALID_REQUEST = "invalid_request"
    CONTENT_FILTER = "content_filter"
    UNAVAILABLE = "unavailable"
    RESPONSE_VALIDATION = "response_validation"
    SCRIPT_EXHAUSTED = "script_exhausted"
    UNKNOWN = "unknown"


class ModelProviderError(RuntimeError):
    """Sanitized, controlled error raised across the provider boundary."""

    def __init__(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        provider_name: str,
        retryable: bool = False,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider_name = provider_name
        self.retryable = retryable
        self.provider_request_id = provider_request_id


@runtime_checkable
class ModelProvider(Protocol):
    """Small synchronous seam implemented by concrete provider adapters."""

    provider_name: str

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Return one typed action or raise ``ModelProviderError``."""
        ...


type FakeProviderStep = ModelResponse | ToolCallAction | SubmitPatchAction | ModelProviderError


class FakeModelProvider:
    """Deterministic scripted provider for local tests and offline experiments."""

    provider_name = "fake"

    def __init__(
        self,
        script: Sequence[FakeProviderStep],
        *,
        usage_per_action: ModelUsage | None = None,
        latency_ms: int = 0,
    ) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        self._script = tuple(script)
        self._next_index = 0
        self._usage = (usage_per_action or ModelUsage()).model_copy(deep=True)
        self._latency_ms = latency_ms
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request.model_copy(deep=True))
        if self._next_index >= len(self._script):
            raise ModelProviderError(
                ProviderErrorCode.SCRIPT_EXHAUSTED,
                "fake provider script is exhausted",
                provider_name=self.provider_name,
            )
        index = self._next_index
        self._next_index += 1
        step = self._script[index]
        if isinstance(step, ModelProviderError):
            raise step
        if isinstance(step, ModelResponse):
            return step.model_copy(deep=True)

        finish_reason = "tool_call" if isinstance(step, ToolCallAction) else "stop"
        return ModelResponse(
            action=step.model_copy(deep=True),
            model_identifier=request.model_identifier,
            model_parameters=dict(request.model_parameters),
            usage=self._usage.model_copy(deep=True),
            latency_ms=self._latency_ms,
            provider_request_id=f"fake-request-{index + 1:04d}",
            finish_reason=finish_reason,
        )
