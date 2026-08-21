"""Deterministic, bounded repository context for the direct-patch baseline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.repositories.path_policy import PathPolicyError, RepositoryPathPolicy


@dataclass(frozen=True, slots=True)
class PreparedRepositoryContext:
    """A stable context snapshot and the exposure accounting used to build it."""

    content: str
    sha256: str
    files_read: int
    files_exposed: int
    lines_exposed: int
    content_characters: int
    exposed_paths: tuple[str, ...]
    read_paths: tuple[str, ...]


def prepare_repository_context(
    paths: RepositoryPathPolicy,
    *,
    task_id: str,
    base_commit: str,
    max_files_read: int,
    max_files_exposed: int,
    max_content_characters: int,
) -> PreparedRepositoryContext:
    """Build the same sorted snapshot for the same checkout and limits.

    File failures are represented in the tree but are not exposed as content.
    This keeps binary files and individually over-bound files out of prompts
    without weakening the repository policy.
    """

    if max_files_read < 0 or max_files_exposed < 1 or max_content_characters < 1:
        raise ValueError("direct-context bounds must be positive")

    tree = paths.list_tree()
    tree_payload: list[dict[str, object]] = []
    exposed_paths: list[str] = []
    for entry in sorted(tree, key=lambda item: item.path):
        if entry.kind == "file":
            if len(exposed_paths) >= max_files_exposed:
                continue
            exposed_paths.append(entry.path)
        tree_payload.append({"kind": entry.kind, "path": entry.path, "size": entry.size})
    files: list[dict[str, object]] = []
    lines_exposed = 0
    read_paths: list[str] = []
    base_payload: dict[str, object] = {
        "base_commit": base_commit,
        "files": files,
        "format": "agenttrace-direct-context-v1",
        "task_id": task_id,
        "tree": tree_payload,
    }
    content = _serialize(base_payload)
    while len(content) > max_content_characters and tree_payload:
        removed = tree_payload.pop()
        path = removed["path"]
        if removed["kind"] == "file" and isinstance(path, str):
            exposed_paths.remove(path)
        content = _serialize(base_payload)
    if len(content) > max_content_characters:
        raise ValueError("direct-context metadata exceeds the content budget")

    for entry in sorted(tree, key=lambda item: item.path):
        if (
            entry.kind != "file"
            or entry.path not in exposed_paths
            or len(read_paths) >= max_files_read
        ):
            continue
        try:
            file_content = paths.read_text(entry.path)
        except PathPolicyError:
            continue
        remaining = max_content_characters - len(content)
        if remaining < 1:
            break
        exposed = file_content[:remaining]
        truncated = len(exposed) < len(file_content)
        file_payload: dict[str, object] = {
            "content": exposed,
            "path": entry.path,
            "truncated": truncated,
        }
        files.append(file_payload)
        candidate = _serialize(base_payload)
        while len(candidate) > max_content_characters and exposed:
            overflow = len(candidate) - max_content_characters
            exposed = exposed[: max(0, len(exposed) - overflow)]
            file_payload["content"] = exposed
            file_payload["truncated"] = True
            candidate = _serialize(base_payload)
        if len(candidate) > max_content_characters:
            files.pop()
            break
        content = candidate
        read_paths.append(entry.path)
        lines_exposed += _line_count(exposed)
        if truncated:
            break

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return PreparedRepositoryContext(
        content=content,
        sha256=digest,
        files_read=len(read_paths),
        files_exposed=len(exposed_paths),
        lines_exposed=lines_exposed + len(tree_payload),
        content_characters=len(content),
        exposed_paths=tuple(exposed_paths),
        read_paths=tuple(read_paths),
    )


def _line_count(content: str) -> int:
    if not content:
        return 0
    return content.count("\n") + (0 if content.endswith("\n") else 1)


def _serialize(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
