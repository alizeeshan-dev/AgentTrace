from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.repositories.git import GitError
from app.repositories.workspace import DisposableWorkspace, WorkspaceManager


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "original"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "AgentTrace Tests")
    _git(repository, "config", "user.email", "tests@example.invalid")
    (repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repository / "tracked.py").write_text("VALUE = 'original'\n", encoding="utf-8")
    _git(repository, "add", ".gitignore", "tracked.py")
    _git(repository, "commit", "-m", "base")
    return repository, _git(repository, "rev-parse", "HEAD")


def test_workspace_is_detached_independent_clone_at_recorded_commit(tmp_path: Path) -> None:
    original, commit = _repository(tmp_path)
    manager = WorkspaceManager(tmp_path / "workspaces")

    workspace = manager.create(run_id="run-001", source_repository=original, base_commit=commit)

    assert _git(workspace.path, "rev-parse", "HEAD") == commit
    assert _git(workspace.path, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert (workspace.path / ".git").is_dir()
    assert (workspace.path / ".git").resolve() != (original / ".git").resolve()


def test_reset_removes_tracked_untracked_and_ignored_changes(tmp_path: Path) -> None:
    original, commit = _repository(tmp_path)
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(run_id="run-reset", source_repository=original, base_commit=commit)
    (workspace.path / "tracked.py").write_text("VALUE = 'changed'\n", encoding="utf-8")
    (workspace.path / "untracked.txt").write_text("temporary\n", encoding="utf-8")
    (workspace.path / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    manager.reset(workspace)

    assert (workspace.path / "tracked.py").read_text(encoding="utf-8") == "VALUE = 'original'\n"
    assert not (workspace.path / "untracked.txt").exists()
    assert not (workspace.path / "ignored.txt").exists()
    assert _git(workspace.path, "status", "--porcelain") == ""


def test_workspace_changes_never_modify_original_repository(tmp_path: Path) -> None:
    original, commit = _repository(tmp_path)
    original_git_status = _git(original, "status", "--porcelain")
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(
        run_id="run-preserve", source_repository=original, base_commit=commit
    )

    (workspace.path / "tracked.py").write_text("VALUE = 'workspace'\n", encoding="utf-8")
    (workspace.path / "new.py").write_text("NEW = True\n", encoding="utf-8")
    manager.reset(workspace)

    assert (original / "tracked.py").read_text(encoding="utf-8") == "VALUE = 'original'\n"
    assert not (original / "new.py").exists()
    assert _git(original, "rev-parse", "HEAD") == commit
    assert _git(original, "status", "--porcelain") == original_git_status
    assert not (original / ".git" / "worktrees").exists()


def test_reset_rejects_workspace_with_shared_git_pointer(tmp_path: Path) -> None:
    original, commit = _repository(tmp_path)
    manager = WorkspaceManager(tmp_path / "workspaces")
    forged_path = manager.root / "forged"
    forged_path.mkdir(parents=True)
    (forged_path / ".git").write_text(str(original / ".git"), encoding="utf-8")
    forged = DisposableWorkspace("forged", forged_path, commit)

    with pytest.raises(ValueError, match=r"independent \.git"):
        manager.reset(forged)


def test_workspace_root_cannot_overlap_source_repository(tmp_path: Path) -> None:
    original, commit = _repository(tmp_path)
    unsafe_root = original / "workspaces"
    manager = WorkspaceManager(unsafe_root)

    assert not unsafe_root.exists()
    with pytest.raises(ValueError, match="must not overlap"):
        manager.create(run_id="run-overlap", source_repository=original, base_commit=commit)
    assert not unsafe_root.exists()


def test_workspace_root_rejects_windows_aliasing_segment(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe filesystem segment"):
        WorkspaceManager(tmp_path / "workspaces.")


def test_workspace_root_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    link = tmp_path / "workspace-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symlink creation is unavailable on this Windows configuration: {error}")

    with pytest.raises(ValueError, match="cannot traverse"):
        WorkspaceManager(link)


def test_workspace_can_be_removed_without_touching_original(tmp_path: Path) -> None:
    original, commit = _repository(tmp_path)
    manager = WorkspaceManager(tmp_path / "workspaces")
    workspace = manager.create(run_id="run-remove", source_repository=original, base_commit=commit)

    manager.remove(workspace)

    assert not workspace.path.exists()
    assert (original / "tracked.py").is_file()
    assert _git(original, "rev-parse", "HEAD") == commit


def test_workspace_ignores_dirty_source_working_tree(tmp_path: Path) -> None:
    original, commit = _repository(tmp_path)
    (original / "tracked.py").write_text("VALUE = 'dirty'\n", encoding="utf-8")
    (original / "untracked.py").write_text("UNTRACKED = True\n", encoding="utf-8")
    original_status = _git(original, "status", "--porcelain")
    manager = WorkspaceManager(tmp_path / "workspaces")

    workspace = manager.create(
        run_id="run-dirty-source", source_repository=original, base_commit=commit
    )

    assert (workspace.path / "tracked.py").read_text(encoding="utf-8").splitlines() == [
        "VALUE = 'original'"
    ]
    assert not (workspace.path / "untracked.py").exists()
    assert _git(original, "status", "--porcelain") == original_status


def test_failed_workspace_creation_removes_partial_clone(tmp_path: Path) -> None:
    original, _ = _repository(tmp_path)
    manager = WorkspaceManager(tmp_path / "workspaces")

    with pytest.raises(GitError, match="git checkout failed"):
        manager.create(
            run_id="run-invalid-commit",
            source_repository=original,
            base_commit="f" * 40,
        )

    assert not (manager.root / "run-invalid-commit").exists()
