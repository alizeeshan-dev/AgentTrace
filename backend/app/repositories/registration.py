"""Registration of immutable local Git repository inputs."""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .git import GitError, run_git

_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MAX_METADATA_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class RepositoryRegistration:
    """Safe, persistence-agnostic result of inspecting a local repository."""

    identity: str
    name: str
    source_path: Path
    base_commit: str
    python_version: str | None
    test_command: str


def register_local_repository(
    source_path: str | Path,
    *,
    test_command: str,
    base_commit: str = "HEAD",
) -> RepositoryRegistration:
    """Inspect a local Git repository without executing repository code."""

    command = _validate_test_command(test_command)
    supplied_path = Path(source_path).expanduser()
    try:
        canonical_source = supplied_path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("Repository path does not exist or cannot be resolved") from error
    if not canonical_source.is_dir():
        raise ValueError("Repository path must be a directory")

    try:
        reported_root = Path(
            run_git(["rev-parse", "--show-toplevel"], cwd=canonical_source)
        ).resolve(strict=True)
    except GitError as error:
        raise ValueError("Path is not inside a readable Git work tree") from error
    if canonical_source != reported_root:
        raise ValueError("Repository registration requires the Git work-tree root")

    try:
        commit = run_git(
            ["rev-parse", "--verify", "--end-of-options", f"{base_commit}^{{commit}}"],
            cwd=reported_root,
        ).lower()
    except GitError as error:
        raise ValueError("Base commit is not a valid commit in this repository") from error
    if not _FULL_COMMIT.fullmatch(commit):
        raise ValueError("Git did not return a full commit object ID")

    canonical_key = f"{os.path.normcase(str(reported_root))}\0{commit}".encode()
    identity = f"local-{hashlib.sha256(canonical_key).hexdigest()}"
    return RepositoryRegistration(
        identity=identity,
        name=reported_root.name,
        source_path=reported_root,
        base_commit=commit,
        python_version=_detect_python_version(reported_root),
        test_command=command,
    )


def _validate_test_command(command: str) -> str:
    if not isinstance(command, str):
        raise TypeError("test_command must be a string")
    stripped = command.strip()
    if not stripped or len(stripped) > 500 or any(char in stripped for char in "\x00\r\n"):
        raise ValueError("test_command must be a single non-empty line of at most 500 characters")
    return stripped


def _detect_python_version(root: Path) -> str | None:
    """Read declarative metadata only; never import or invoke project code."""

    version_file = root / ".python-version"
    value = _read_small_text(version_file)
    if value:
        first_line = value.splitlines()[0].strip()
        if first_line and len(first_line) <= 100:
            return first_line

    pyproject_file = root / "pyproject.toml"
    raw = _read_small_bytes(pyproject_file)
    if raw is None:
        return None
    try:
        project = tomllib.loads(raw.decode("utf-8")).get("project", {})
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    requires_python = project.get("requires-python") if isinstance(project, dict) else None
    if isinstance(requires_python, str) and 0 < len(requires_python) <= 100:
        return requires_python
    return None


def _read_small_text(path: Path) -> str | None:
    raw = _read_small_bytes(path)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_small_bytes(path: Path) -> bytes | None:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_METADATA_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None
