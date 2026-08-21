"""Shared validation primitives for research records."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE
)


def _reject_windows_device_identifier(value: str) -> str:
    if _WINDOWS_DEVICE_NAME.fullmatch(value):
        raise ValueError("identifier cannot be a Windows device name")
    return value

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$",
    ),
]
FilesystemIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    ),
    AfterValidator(_reject_windows_device_identifier),
]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_GLOB_CHARACTERS = re.compile(r"[*?\[\]]")


class ResearchSchema(BaseModel):
    """Strict base that can also serialize SQLAlchemy model instances."""

    model_config = ConfigDict(extra="forbid", from_attributes=True, strict=True)


def validate_repository_path(value: str) -> str:
    """Validate portable repository-relative POSIX syntax without touching disk."""

    if (
        not value
        or any(character in value for character in "\x00\r\n\\:")
        or _GLOB_CHARACTERS.search(value)
    ):
        raise ValueError("path must be a non-empty POSIX repository-relative path")
    if value.startswith("/") or _WINDOWS_DRIVE.match(value):
        raise ValueError("absolute paths are forbidden")
    directory_prefix = value.endswith("/")
    candidate = value[:-1] if directory_prefix else value
    if not candidate or "//" in value:
        raise ValueError("path must be normalized and cannot contain traversal")
    path = PurePosixPath(candidate)
    if candidate != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path must be normalized and cannot contain traversal")
    if any(part.casefold() == ".git" for part in path.parts):
        raise ValueError(".git paths are protected")
    return f"{candidate}/" if directory_prefix else candidate
