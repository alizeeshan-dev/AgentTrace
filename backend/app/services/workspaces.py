"""Load persisted benchmark tasks into disposable, policy-bound workspaces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Repository, Task
from app.repositories.path_policy import RepositoryPathPolicy
from app.repositories.workspace import DisposableWorkspace, WorkspaceManager


@dataclass(frozen=True, slots=True)
class LoadedTaskWorkspace:
    """A persisted task paired with its isolated checkout and filesystem policy."""

    task: Task
    repository: Repository
    workspace: DisposableWorkspace
    paths: RepositoryPathPolicy


class TaskWorkspaceLoader:
    """Create the Phase 2 execution foundation without running repository code."""

    def __init__(
        self,
        session: Session,
        workspace_manager: WorkspaceManager,
        *,
        max_file_bytes: int,
        max_tree_entries: int = 2_000,
    ) -> None:
        self.session = session
        self.workspace_manager = workspace_manager
        self.max_file_bytes = max_file_bytes
        self.max_tree_entries = max_tree_entries

    def load(
        self,
        *,
        task_id: str,
        run_id: str,
        hidden_paths: Sequence[str],
    ) -> LoadedTaskWorkspace:
        """Clone the recorded base and bind task write/read policy to it.

        Hidden evaluator locations are an explicit input because Phase 1 keeps
        them in a separately hashed evaluator/tool-policy artifact, not in the
        portable task manifest's write-only ``forbidden_paths`` field.
        """

        task = self.session.get(Task, task_id)
        if task is None:
            raise LookupError(f"Unknown task: {task_id}")
        repository = self.session.get(Repository, task.repository_id)
        if repository is None:
            raise LookupError(f"Task repository is missing: {task.repository_id}")

        workspace = self.workspace_manager.create(
            run_id=run_id,
            source_repository=repository.source,
            base_commit=repository.base_commit,
        )
        try:
            paths = RepositoryPathPolicy(
                workspace.path,
                allowed_paths=task.allowed_paths,
                forbidden_paths=task.forbidden_paths,
                hidden_paths=hidden_paths,
                max_file_bytes=self.max_file_bytes,
                max_tree_entries=self.max_tree_entries,
            )
        except Exception:
            self.workspace_manager.remove(workspace)
            raise
        return LoadedTaskWorkspace(task, repository, workspace, paths)
