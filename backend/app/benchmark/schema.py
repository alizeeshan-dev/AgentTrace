"""Evaluator-owned schema for portable benchmark task manifests."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from app.schemas.common import (
    CommitSha,
    FilesystemIdentifier,
    ResearchSchema,
    validate_repository_path,
)
from app.schemas.research import Command, ShortText

Description = Annotated[str, StringConstraints(min_length=20, max_length=10_000)]
Tag = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=40,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]


class KnownFault(ResearchSchema):
    """Evaluator-only ground truth used to measure localization quality."""

    file: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    line: Annotated[int, Field(ge=1)]
    symbol: ShortText | None = None

    @field_validator("file")
    @classmethod
    def validate_fault_path(cls, value: str) -> str:
        return validate_repository_path(value)


class BenchmarkTask(ResearchSchema):
    task_source: Literal["benchmark"] = "benchmark"
    """Versioned, portable definition of one immutable benchmark task.

    Hidden commands and evaluator artifacts are deliberately kept in this
    evaluator-owned record.  They must never be included in agent context.
    """

    schema_version: Literal[1] = 1
    task_id: FilesystemIdentifier
    repository: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    base_commit: CommitSha
    title: ShortText
    description: Description
    task_category: Literal["bug_fix", "refactor"]
    difficulty: Literal["easy", "medium", "hard"]
    allowed_paths: Annotated[list[str], Field(min_length=1, max_length=30)]
    forbidden_paths: Annotated[list[str], Field(min_length=1, max_length=30)]
    visible_test_command: Command
    hidden_test_command: Command
    property_profile: ShortText | None = None
    symbolic_profile: ShortText | None = None
    known_correct_patch: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    timeout_seconds: Annotated[int, Field(ge=1, le=900)] = 120
    tags: Annotated[list[Tag], Field(min_length=1, max_length=20)]
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    known_faults: list[KnownFault] = Field(default_factory=list, max_length=20)

    @field_validator("repository")
    @classmethod
    def validate_repository_reference(cls, value: str) -> str:
        """Accept a portable relative artifact or credential-free HTTPS URL."""

        if value.startswith("https://"):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or not parsed.path.strip("/")
                or "@" in unquote(parsed.netloc)
            ):
                raise ValueError("HTTPS repository references cannot contain credentials")
            return value
        return validate_repository_path(value)

    @field_validator("known_correct_patch")
    @classmethod
    def validate_patch_reference(cls, value: str) -> str:
        return validate_repository_path(value)

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def validate_task_paths(cls, values: list[str]) -> list[str]:
        normalized = [validate_repository_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("repository paths must be unique")
        return normalized

    @field_validator("tags")
    @classmethod
    def validate_unique_tags(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("tags must be unique")
        return values

    @model_validator(mode="after")
    def hidden_test_token_is_evaluator_only(self) -> BenchmarkTask:
        if "{hidden_tests}" in self.visible_test_command:
            raise ValueError("visible test command cannot reference hidden tests")
        if self.hidden_test_command.split().count("{hidden_tests}") != 1:
            raise ValueError("hidden test command must contain one {hidden_tests} token")
        return self
