"""Persistence boundary for immutable local repository registrations."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Repository
from app.filesystem import paths_overlap
from app.repositories.registration import register_local_repository


class RepositoryRegistrationConflict(ValueError):
    """Raised when an existing immutable registration has different metadata."""


class RepositoryRegistry:
    """Register canonical local Git snapshots without mutating their source."""

    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.protected_roots = (
            settings.effective_workspace_root.resolve(strict=False),
            settings.effective_artifact_root.resolve(strict=False),
        )

    def register_local(
        self,
        source_path: str | Path,
        *,
        test_command: str,
        base_commit: str = "HEAD",
    ) -> Repository:
        registration = register_local_repository(
            source_path,
            test_command=test_command,
            base_commit=base_commit,
        )
        for protected_root in self.protected_roots:
            if paths_overlap(registration.source_path, protected_root):
                raise ValueError("Repository source must not overlap workspace or artifact storage")

        values = {
            "repository_id": registration.identity,
            "name": registration.name,
            "source": str(registration.source_path),
            "base_commit": registration.base_commit,
            "python_version": registration.python_version,
            "test_command": registration.test_command,
        }
        existing = self.session.get(Repository, registration.identity)
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items()):
                raise RepositoryRegistrationConflict(
                    "Repository snapshot is already registered with different metadata"
                )
            return existing

        repository = Repository(**values)
        self.session.add(repository)
        self.session.flush()
        return repository
