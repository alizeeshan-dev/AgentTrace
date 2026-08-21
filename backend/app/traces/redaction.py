"""Deterministic trace redaction and output bounding."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import JsonValue

from app.security import is_credential_key

_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|cookie|credential|password|private[_-]?key|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_ENV_ASSIGNMENT = re.compile(
    r"(?im)^\s*[A-Z][A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)\s*=\s*\S+"
)
_AUTHORIZATION = re.compile(r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*[^\r\n]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COMMON_API_KEY = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b")
_KNOWN_CREDENTIAL = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,})\b"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_HIDDEN_MARKERS = ("hidden_tests", ".agenttrace-evaluator", "hidden-test source")
_ENV_PATH = re.compile(r"(?i)(?:^|[/\\\s])\.env(?:[./\\\s]|$)")
_SAFE_TOKEN_KEYS = {
    "added_input_tokens",
    "added_output_tokens",
    "input_tokens",
    "max_tokens",
    "output_tokens",
    "token_use",
    "tokens",
}


class TraceRedactor:
    """Redact secret-bearing content before it reaches trace rows or exports."""

    def __init__(self, *, max_text_characters: int = 4_000) -> None:
        if max_text_characters < 64:
            raise ValueError("max_text_characters must be at least 64")
        self.max_text_characters = max_text_characters

    def redact(self, value: Any, *, key: str | None = None) -> JsonValue:
        if key is not None and self._sensitive_key(key):
            return "[REDACTED:SECRET]"
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                str(item_key): self.redact(item_value, key=str(item_key))
                for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
            }
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
            return [self.redact(item) for item in value]
        return self.redact_text(str(value))

    def redact_text(self, value: str) -> str:
        lowered = value.casefold()
        hidden_marker = any(marker in lowered for marker in _HIDDEN_MARKERS)
        hidden_source_shape = any(character in value for character in ("/", "\\", "\n", "\r"))
        if "hidden-test source" in lowered or (hidden_marker and hidden_source_shape):
            return "[REDACTED:HIDDEN_TEST_CONTENT]"
        if _ENV_PATH.search(value) or value.casefold().strip() == ".env":
            return "[REDACTED:ENV_CONTENT]"
        redacted = _PRIVATE_KEY.sub("[REDACTED:PRIVATE_KEY]", value)
        redacted = _AUTHORIZATION.sub("Authorization: [REDACTED]", redacted)
        redacted = _BEARER.sub("Bearer [REDACTED]", redacted)
        redacted = _COMMON_API_KEY.sub("[REDACTED:API_KEY]", redacted)
        redacted = _KNOWN_CREDENTIAL.sub("[REDACTED:CREDENTIAL]", redacted)
        redacted = _ENV_ASSIGNMENT.sub("[REDACTED:SECRET_ASSIGNMENT]", redacted)
        if len(redacted) <= self.max_text_characters:
            return redacted
        digest = hashlib.sha256(redacted.encode("utf-8", errors="replace")).hexdigest()
        marker = f"...[TRUNCATED length={len(redacted)} sha256={digest}]"
        return f"{redacted[: self.max_text_characters - len(marker)]}{marker}"

    def summary(self, value: Mapping[str, Any] | None) -> str | None:
        if value is None:
            return None
        rendered = json.dumps(
            self.redact(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return self.redact_text(rendered)

    @staticmethod
    def _sensitive_key(key: str) -> bool:
        normalized = key.casefold()
        return normalized not in _SAFE_TOKEN_KEYS and (
            _SENSITIVE_KEY.search(normalized) is not None or is_credential_key(key)
        )
