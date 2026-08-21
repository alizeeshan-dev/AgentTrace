"""Small shared helpers for preventing credentials from entering research data."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_SEPARATOR = re.compile(r"[^A-Za-z0-9]+")
_SENSITIVE_SEGMENTS = {
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "passwd",
    "password",
    "secret",
}
_SENSITIVE_COMPACT_KEYS = {
    "apikey",
    "authorizationheader",
    "bearertoken",
    "clientsecret",
    "privatekey",
    "refreshtoken",
    "accesstoken",
    "authtoken",
}
_SENSITIVE_COMPACT_SUFFIXES = tuple(_SENSITIVE_COMPACT_KEYS)


def is_credential_key(key: str) -> bool:
    """Recognize common snake/kebab/camel-case credential field names.

    Generic generation settings such as ``max_tokens`` are deliberately not
    classified as credentials.
    """

    separated = _CAMEL_BOUNDARY.sub("_", key)
    segments = tuple(
        item.casefold() for item in _KEY_SEPARATOR.split(separated) if item
    )
    if any(segment in _SENSITIVE_SEGMENTS for segment in segments):
        return True
    compact = "".join(segments)
    return compact == "token" or compact.endswith(_SENSITIVE_COMPACT_SUFFIXES)


def find_credential_key(value: Any, *, prefix: str = "") -> str | None:
    """Return the first credential-bearing key in a JSON-like value."""

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            path = f"{prefix}.{key}" if prefix else key
            if is_credential_key(key):
                return path
            found = find_credential_key(child, prefix=path)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            found = find_credential_key(child, prefix=path)
            if found is not None:
                return found
    return None
