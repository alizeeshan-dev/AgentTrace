from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.actions import (
    ReadFileArguments,
    SubmitPatchAction,
    ToolCallAction,
    parse_agent_action,
)
from app.agent.provider import (
    FakeModelProvider,
    ModelMessage,
    ModelProviderError,
    ModelRequest,
    ModelUsage,
    ProviderErrorCode,
)


def _request() -> ModelRequest:
    return ModelRequest(
        model_identifier="local-test-model",
        model_parameters={"temperature": 0},
        messages=[ModelMessage(role="user", content="Inspect the reported bug.")],
        available_tools=["read_file"],
        timeout_seconds=10,
    )


def test_fake_provider_returns_a_valid_structured_response() -> None:
    action = ToolCallAction(
        tool="read_file",
        arguments=ReadFileArguments(path="src/parser.py"),
    )
    provider = FakeModelProvider(
        [action], usage_per_action=ModelUsage(input_tokens=17, output_tokens=5), latency_ms=4
    )

    response = provider.generate(_request())

    assert response.action == action
    assert response.model_identifier == "local-test-model"
    assert response.model_parameters == {"temperature": 0}
    assert response.usage == ModelUsage(input_tokens=17, output_tokens=5)
    assert response.latency_ms == 4
    assert response.provider_request_id == "fake-request-0001"
    assert response.finish_reason == "tool_call"
    assert provider.requests == [_request()]


def test_action_parser_never_infers_an_action_from_prose() -> None:
    with pytest.raises(ValidationError):
        parse_agent_action("I think src/parser.py should change")


def test_tool_arguments_must_match_the_selected_tool() -> None:
    with pytest.raises(ValidationError):
        ToolCallAction(
            tool="search_code",
            arguments=ReadFileArguments(path="src/parser.py"),
        )


def test_private_reasoning_fields_are_not_accepted() -> None:
    with pytest.raises(ValidationError):
        SubmitPatchAction.model_validate(
            {
                "unified_diff": "--- a/src/a.py\n+++ b/src/a.py\n",
                "rationale": "Make the bounded change.",
                "chain_of_thought": "private reasoning",
            }
        )


def test_fake_provider_exhaustion_is_a_controlled_error() -> None:
    provider = FakeModelProvider([])

    with pytest.raises(ModelProviderError) as error:
        provider.generate(_request())

    assert error.value.code is ProviderErrorCode.SCRIPT_EXHAUSTED
    assert error.value.retryable is False
