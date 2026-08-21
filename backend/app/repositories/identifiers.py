"""Validation for identifiers used as filesystem directory names."""

from __future__ import annotations

import re

_SAFE_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE
)


def validate_safe_identifier(value: str, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 100
        or not _SAFE_IDENTIFIER.fullmatch(value)
        or _WINDOWS_DEVICE_NAME.fullmatch(value)
    ):
        raise ValueError(
            f"{field_name} must be a portable lowercase identifier of at most 100 characters"
        )
    return value
