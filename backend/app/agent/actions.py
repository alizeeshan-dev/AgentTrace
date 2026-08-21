"""Provider-neutral structured actions produced by an AgentTrace model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import StringConstraints, TypeAdapter, field_validator, model_validator

from app.schemas.common import ResearchSchema

type ToolName = Literal["list_tree", "read_file", "search_code"]
ToolPath = Annotated[str, StringConstraints(min_length=1, max_length=500)]
ExplanationText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]


class ObservableExplanation(ResearchSchema):
    """Concise, user-visible metadata rather than private model reasoning."""

    reason: ExplanationText | None = None
    suspected_cause: ExplanationText | None = None
    expected_behavioral_change: ExplanationText | None = None
    uncertainty: ExplanationText | None = None


class ListTreeArguments(ResearchSchema):
    path: ToolPath = "."


class ReadFileArguments(ResearchSchema):
    path: ToolPath


class SearchCodeArguments(ResearchSchema):
    query: Annotated[
        str,
        StringConstraints(min_length=1, max_length=200, pattern=r"^[^\x00\r\n]+$"),
    ]
    path: ToolPath = "."
    case_sensitive: bool = True


type ToolArguments = ListTreeArguments | ReadFileArguments | SearchCodeArguments

_ARGUMENT_MODELS: dict[str, type[ResearchSchema]] = {
    "list_tree": ListTreeArguments,
    "read_file": ReadFileArguments,
    "search_code": SearchCodeArguments,
}


class ToolCallAction(ResearchSchema):
    """A request for exactly one approved, read-only repository tool."""

    action_type: Literal["tool_call"] = "tool_call"
    tool: ToolName
    arguments: ToolArguments
    explanation: ObservableExplanation | None = None

    @model_validator(mode="before")
    @classmethod
    def parse_arguments_for_selected_tool(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data: dict[str, object] = dict(value)
        tool = data.get("tool")
        arguments = data.get("arguments")
        if isinstance(tool, str) and tool in _ARGUMENT_MODELS:
            data["arguments"] = _ARGUMENT_MODELS[tool].model_validate(arguments)
        return data

    @model_validator(mode="after")
    def arguments_match_selected_tool(self) -> ToolCallAction:
        expected_type = _ARGUMENT_MODELS[self.tool]
        if not isinstance(self.arguments, expected_type):
            raise ValueError(f"{self.tool} requires {expected_type.__name__}")
        return self


class SubmitPatchAction(ResearchSchema):
    """A final unified-diff candidate; validation and application stay orchestration-owned."""

    action_type: Literal["submit_patch"] = "submit_patch"
    unified_diff: Annotated[str, StringConstraints(min_length=1, max_length=2_000_000)]
    rationale: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ]
    explanation: ObservableExplanation | None = None

    @field_validator("unified_diff")
    @classmethod
    def reject_binary_content(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("unified diff cannot contain NUL bytes")
        return value


type AgentAction = ToolCallAction | SubmitPatchAction
_ACTION_ADAPTER: TypeAdapter[AgentAction] = TypeAdapter(AgentAction)


def parse_agent_action(value: object) -> AgentAction:
    """Validate provider data without inferring actions from prose."""

    return _ACTION_ADAPTER.validate_python(value)
