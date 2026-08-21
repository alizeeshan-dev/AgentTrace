"""Disposable, independent Git workspaces for experiment runs."""

from __future__ import annotations

import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from app.filesystem import paths_overlap, validate_runtime_root

from .git import GitError, run_git
from .identifiers import validate_safe_identifier

_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class DisposableWorkspace:
    run_id: str
    path: Path
    base_commit: str


class WorkspaceManager:
    """Create clones that share no mutable Git administrative state."""

    def __init__(self, workspace_root: str | Path) -> None:
        root = validate_runtime_root(
            Path(workspace_root),
            field_name="workspace_root",
        )
        if root.exists():
            if _is_link_like(root):
                raise ValueError("Workspace root cannot be a link or junction")
            self.root = root.resolve(strict=True)
            if not self.root.is_dir():
                raise ValueError("Workspace root must be a directory")
        else:
            self.root = root.resolve(strict=False)

    def create(
        self,
        *,
        run_id: str,
        source_repository: str | Path,
        base_commit: str,
    ) -> DisposableWorkspace:
        safe_run_id = validate_safe_identifier(run_id, field_name="run_id")
        if not _FULL_COMMIT.fullmatch(base_commit):
            raise ValueError("base_commit must be a full lowercase Git object ID")
        source = Path(source_repository).resolve(strict=True)
        source_is_bundle = source.is_file() and source.suffix.casefold() == ".bundle"
        if not source.is_dir() and not source_is_bundle:
            raise ValueError("Source repository must be a Git directory or bundle")
        if source.is_symlink() or (
            hasattr(source, "is_junction") and source.is_junction()
        ):
            raise ValueError("Source repository cannot be a link or junction")
        if paths_overlap(source, self.root):
            raise ValueError("Workspace root and source repository must not overlap")
        self._ensure_root()
        destination = self.root / safe_run_id
        if destination.exists():
            raise FileExistsError(f"Workspace already exists for run {safe_run_id}")

        try:
            run_git(
                [
                    "clone",
                    "--no-hardlinks",
                    "--no-checkout",
                    "--",
                    str(source),
                    str(destination),
                ],
                cwd=self.root,
                timeout_seconds=120,
            )
            run_git(["checkout", "--detach", base_commit], cwd=destination)
            self._reset_path(destination, base_commit)
        except Exception:
            self._remove_direct_child(destination)
            raise
        return DisposableWorkspace(safe_run_id, destination.resolve(strict=True), base_commit)

    def reset(self, workspace: DisposableWorkspace) -> None:
        path = workspace.path.resolve(strict=True)
        if not _is_direct_child(path, self.root) or path.name != workspace.run_id:
            raise ValueError("Workspace is outside the managed workspace root")
        self._reset_path(path, workspace.base_commit)

    def remove(self, workspace: DisposableWorkspace) -> None:
        """Delete a disposable workspace after validating its managed location."""

        path = workspace.path.resolve(strict=True)
        if not _is_direct_child(path, self.root) or path.name != workspace.run_id:
            raise ValueError("Workspace is outside the managed workspace root")
        if _is_link_like(workspace.path):
            raise ValueError("Workspace path cannot be a link or junction")
        self._remove_direct_child(path)

    def _remove_direct_child(self, path: Path) -> None:
        if not path.exists():
            return
        if path.parent.resolve(strict=True) != self.root or path == self.root:
            raise ValueError("Refusing to remove a path outside the workspace root")
        if _is_link_like(path):
            raise ValueError("Refusing to remove a linked workspace path")
        # Git may mark packed objects read-only on Windows.  Only relax that
        # attribute for regular pack files inside the already validated clone;
        # never walk links or mutate anything in the source repository.
        pack_directory = path / ".git" / "objects" / "pack"
        if pack_directory.is_dir() and not _is_link_like(pack_directory):
            for packed_object in pack_directory.iterdir():
                if packed_object.is_file() and not _is_link_like(packed_object):
                    packed_object.chmod(packed_object.stat().st_mode | stat.S_IWRITE)
        shutil.rmtree(path)

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        validated = validate_runtime_root(self.root, field_name="workspace_root")
        if validated.resolve(strict=True) != self.root:
            raise ValueError("Workspace root changed during creation")
        if not self.root.is_dir():
            raise ValueError("Workspace root must be a directory")

    @staticmethod
    def _reset_path(path: Path, base_commit: str) -> None:
        _verify_independent_git_directory(path)
        try:
            actual = run_git(
                ["rev-parse", "--verify", "--end-of-options", f"{base_commit}^{{commit}}"],
                cwd=path,
            )
        except GitError as error:
            raise ValueError("Workspace does not contain its recorded base commit") from error
        if actual.lower() != base_commit:
            raise ValueError("Workspace commit does not match the recorded base commit")
        run_git(["reset", "--hard", base_commit], cwd=path)
        run_git(["clean", "-ffdx"], cwd=path)
        run_git(["checkout", "--detach", base_commit], cwd=path)


def _is_direct_child(candidate: Path, root: Path) -> bool:
    return candidate.parent == root and candidate != root


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _verify_independent_git_directory(workspace: Path) -> None:
    git_directory = workspace / ".git"
    if (
        not git_directory.is_dir()
        or git_directory.is_symlink()
        or (hasattr(git_directory, "is_junction") and git_directory.is_junction())
    ):
        raise ValueError("Workspace must have an independent .git directory")
    try:
        canonical_git_directory = git_directory.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("Workspace Git directory cannot be resolved") from error
    if canonical_git_directory.parent != workspace or canonical_git_directory.name != ".git":
        raise ValueError("Workspace Git directory escapes the workspace")
