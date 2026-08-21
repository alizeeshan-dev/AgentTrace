"""Shared host-filesystem validation for runtime storage roots."""

from __future__ import annotations

import re
from pathlib import Path

_GLOB_CHARACTERS = re.compile(r"[*?\[\]]")
_WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.IGNORECASE
)


def validate_runtime_root(path: Path, *, field_name: str) -> Path:
    """Reject host path spellings that alias on Windows or imply expansion."""

    lexical = path.expanduser().absolute()
    for part in lexical.parts:
        if part == lexical.anchor:
            continue
        if (
            not part
            or "\x00" in part
            or ":" in part
            or part.endswith((" ", "."))
            or _GLOB_CHARACTERS.search(part)
            or _WINDOWS_DEVICE_NAME.fullmatch(part)
        ):
            raise ValueError(f"{field_name} contains an unsafe filesystem segment")
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        if current.is_symlink() or (
            hasattr(current, "is_junction") and current.is_junction()
        ):
            raise ValueError(f"{field_name} cannot traverse a link or junction")
    return lexical


def paths_overlap(first: Path, second: Path) -> bool:
    """Return whether either canonical path contains the other."""

    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False
