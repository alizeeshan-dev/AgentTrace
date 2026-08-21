"""Filesystem boundary and task path-policy enforcement."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

_GLOB_CHARACTERS = re.compile(r"[*?\[\]]")
_WINDOWS_DEVICE_NAME = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", re.I)
_SAFE_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}


class PathPolicyError(ValueError):
    """Raised when a requested repository path violates policy."""


@dataclass(frozen=True, slots=True)
class TreeEntry:
    path: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int | None


class RepositoryPathPolicy:
    """Resolve repository paths while enforcing lexical and canonical bounds."""

    def __init__(
        self,
        repository_root: str | Path,
        *,
        allowed_paths: Sequence[str] = (),
        forbidden_paths: Sequence[str] = (),
        hidden_paths: Sequence[str] = (),
        max_file_bytes: int = 1_048_576,
        max_tree_entries: int = 2_000,
    ) -> None:
        try:
            root = Path(repository_root).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise PathPolicyError("Repository root must exist and resolve safely") from error
        if not root.is_dir():
            raise PathPolicyError("Repository root must be a directory")
        if max_file_bytes < 1 or max_tree_entries < 1:
            raise ValueError("File and tree bounds must be positive")

        self.root = root
        self.allowed_paths = tuple(_validate_policy_entry(entry) for entry in allowed_paths)
        self.forbidden_paths = tuple(_validate_policy_entry(entry) for entry in forbidden_paths)
        self.hidden_paths = tuple(_validate_policy_entry(entry) for entry in hidden_paths)
        self.max_file_bytes = max_file_bytes
        self.max_tree_entries = max_tree_entries

    def resolve(
        self,
        relative_path: str,
        *,
        access: Literal["read", "write"] = "read",
        must_exist: bool = True,
        allow_root: bool = False,
    ) -> Path:
        normalized = _validate_requested_path(relative_path, allow_root=allow_root)
        if normalized != ".":
            segments = PurePosixPath(normalized).parts
            if any(segment.casefold() == ".git" for segment in segments):
                raise PathPolicyError("Git administrative data is protected")
            if _contains_protected_secret_path(segments):
                raise PathPolicyError("Repository secret files are protected")
            self._enforce_task_policy(normalized, access=access)

        candidate = self.root if normalized == "." else self.root.joinpath(*normalized.split("/"))
        try:
            canonical = candidate.resolve(strict=must_exist)
        except (OSError, RuntimeError) as error:
            raise PathPolicyError("Requested path does not exist or cannot be resolved") from error
        if not _is_within(canonical, self.root):
            raise PathPolicyError("Requested path escapes the repository through a link")
        canonical_relative = canonical.relative_to(self.root).as_posix()
        if canonical_relative != ".":
            if any(
                segment.casefold() == ".git" for segment in PurePosixPath(canonical_relative).parts
            ):
                raise PathPolicyError("Git administrative data is protected")
            if _contains_protected_secret_path(PurePosixPath(canonical_relative).parts):
                raise PathPolicyError("Repository secret files are protected")
            self._enforce_task_policy(canonical_relative, access=access)
        return canonical

    def read_text(self, relative_path: str, *, encoding: str = "utf-8") -> str:
        path = self.resolve(relative_path, access="read")
        if not path.is_file():
            raise PathPolicyError("Requested path is not a regular file")
        try:
            with path.open("rb") as stream:
                content = stream.read(self.max_file_bytes + 1)
            if len(content) > self.max_file_bytes:
                raise PathPolicyError("Requested file exceeds the configured read bound")
            return content.decode(encoding)
        except PathPolicyError:
            raise
        except (OSError, UnicodeError, LookupError) as error:
            raise PathPolicyError("Requested file is not readable text") from error

    def list_tree(self, relative_path: str = ".") -> list[TreeEntry]:
        start = self.resolve(relative_path, access="read", allow_root=True)
        if not start.is_dir():
            raise PathPolicyError("Tree root must be a directory")
        entries: list[TreeEntry] = []
        pending_directories = [start]
        while pending_directories:
            current = pending_directories.pop(0)
            visible_children: list[tuple[Path, str]] = []
            try:
                with os.scandir(current) as scanned:
                    for child in scanned:
                        child_path = Path(child.path)
                        relative = child_path.relative_to(self.root).as_posix()
                        if self._tree_path_visible(relative):
                            visible_children.append((child_path, relative))
                            if len(entries) + len(visible_children) > self.max_tree_entries:
                                raise PathPolicyError(
                                    "Repository tree exceeds the configured entry bound"
                                )
            except PathPolicyError:
                raise
            except OSError as error:
                raise PathPolicyError("Repository tree cannot be read safely") from error

            for visible_path, relative in sorted(
                visible_children, key=lambda item: item[1].casefold()
            ):
                link_like = _is_link_like(visible_path)
                if link_like:
                    kind: Literal["file", "directory", "symlink", "other"] = "symlink"
                    size = None
                elif visible_path.is_dir():
                    kind = "directory"
                    size = None
                    pending_directories.append(visible_path)
                elif visible_path.is_file():
                    kind = "file"
                    try:
                        size = visible_path.stat().st_size
                    except OSError:
                        size = None
                else:
                    kind = "other"
                    size = None
                entries.append(TreeEntry(relative, kind, size))
        return entries

    def _enforce_task_policy(self, normalized: str, *, access: str) -> None:
        if any(_matches(normalized, entry) for entry in self.hidden_paths):
            raise PathPolicyError("Hidden evaluator artifacts are not accessible")
        if access == "write" and any(_matches(normalized, entry) for entry in self.forbidden_paths):
            raise PathPolicyError("Path is forbidden by the task policy")
        if (
            access == "write"
            and self.allowed_paths
            and not any(_matches(normalized, entry) for entry in self.allowed_paths)
        ):
            raise PathPolicyError("Path is outside the task write allowlist")

    def _tree_path_visible(self, normalized: str) -> bool:
        segments = PurePosixPath(normalized).parts
        if any(segment.casefold() == ".git" for segment in segments):
            return False
        if _contains_protected_secret_path(segments):
            return False
        return not any(_matches(normalized, entry) for entry in self.hidden_paths)


def _validate_requested_path(path: str, *, allow_root: bool) -> str:
    if not isinstance(path, str) or not path or any(char in path for char in "\x00\r\n"):
        raise PathPolicyError("Repository path must be a non-empty string")
    if path == "." and allow_root:
        return path
    if path == "." or path.startswith("./"):
        raise PathPolicyError("Repository paths must not use dot segments")
    if "\\" in path or "//" in path or ":" in path or _GLOB_CHARACTERS.search(path):
        raise PathPolicyError("Repository paths must use literal POSIX segments")
    windows_path = PureWindowsPath(path)
    posix_path = PurePosixPath(path)
    if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
        raise PathPolicyError("Absolute repository paths are forbidden")
    if any(segment in {"", ".", ".."} for segment in posix_path.parts):
        raise PathPolicyError("Traversal and dot segments are forbidden")
    if any(
        segment.endswith((" ", ".")) or _WINDOWS_DEVICE_NAME.fullmatch(segment)
        for segment in posix_path.parts
    ):
        raise PathPolicyError("Repository path contains an unsafe Windows segment")
    return posix_path.as_posix()


def _validate_policy_entry(entry: str) -> str:
    directory_prefix = isinstance(entry, str) and entry.endswith("/")
    candidate = entry[:-1] if directory_prefix else entry
    normalized = _validate_requested_path(candidate, allow_root=False)
    return f"{normalized}/" if directory_prefix else normalized


def _matches(path: str, policy_entry: str) -> bool:
    path = _comparison_key(path)
    policy_entry = _comparison_key(policy_entry)
    if policy_entry.endswith("/"):
        prefix = policy_entry[:-1]
        return path == prefix or path.startswith(f"{prefix}/")
    return path == policy_entry


def _comparison_key(value: str) -> str:
    return value.casefold() if os.path.normcase("A") == "a" else value


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link_like(path: Path) -> bool:
    """Treat Windows junctions as links and never descend through them."""

    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _contains_protected_secret_path(segments: Sequence[str]) -> bool:
    """Protect live dotenv files while leaving deliberate templates inspectable."""

    for segment in segments:
        name = segment.casefold()
        if name == ".env" or (name.startswith(".env.") and name not in _SAFE_ENV_TEMPLATES):
            return True
    return False
