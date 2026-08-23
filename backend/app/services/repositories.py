"""Persistence boundary for immutable local and external Git registrations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Repository
from app.filesystem import paths_overlap
from app.repositories.external import register_external_repository
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
            settings.effective_verification_root.resolve(strict=False),
            settings.effective_external_repository_root.resolve(strict=False),
        )
        self.external_repository_root = settings.effective_external_repository_root

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
                raise ValueError("Repository source must not overlap AgentTrace managed storage")

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

    def register_external(
        self,
        repository_url: str,
        *,
        test_command: str | None = None,
    ) -> Repository:
        registration = register_external_repository(
            repository_url,
            managed_root=self.external_repository_root,
            test_command=test_command,
        )
        values = {
            "repository_id": registration.identity,
            "name": registration.name,
            "source": str(registration.source_path),
            "base_commit": registration.base_commit,
            "python_version": registration.python_version,
            "test_command": registration.test_command or "",
            "source_type": "external_git",
            "repository_url": registration.repository_url,
            "default_branch": registration.default_branch,
            "primary_language": registration.primary_language,
            "registered_at": datetime.now(UTC),
            "managed_source": str(registration.source_path),
            "trusted_for_local_execution": False,
            "trust_confirmed_at": None,
            "repository_metadata": registration.metadata,
        }
        existing = self.session.get(Repository, registration.identity)
        if existing is not None:
            immutable = (
                "repository_id",
                "name",
                "source",
                "base_commit",
                "source_type",
                "repository_url",
                "managed_source",
            )
            if any(getattr(existing, key) != values[key] for key in immutable):
                raise RepositoryRegistrationConflict(
                    "External repository snapshot is already registered differently"
                )
            return existing
        repository = Repository(**values)
        self.session.add(repository)
        self.session.flush()
        return repository

    def set_external_trust(
        self,
        repository_id: str,
        *,
        trusted: bool,
        acknowledged: bool,
    ) -> Repository:
        repository = self.session.get(Repository, repository_id)
        if repository is None:
            raise LookupError("Repository not found")
        if repository.source_type != "external_git":
            raise ValueError("Execution trust applies only to external Git repositories")
        if trusted and not acknowledged:
            raise ValueError(
                "Trust acknowledgement is required before local repository code may execute"
            )
        repository.trusted_for_local_execution = trusted
        repository.trust_confirmed_at = datetime.now(UTC) if trusted else None
        self.session.flush()
        return repository
