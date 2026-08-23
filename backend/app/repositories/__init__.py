"""Safe local Git repository services."""

from .external import (
    ExternalRepositoryError,
    ExternalRepositoryRegistration,
    register_external_repository,
    validate_external_git_url,
)
from .registration import RepositoryRegistration, register_local_repository
from .workspace import DisposableWorkspace, WorkspaceManager

__all__ = [
    "DisposableWorkspace",
    "ExternalRepositoryError",
    "ExternalRepositoryRegistration",
    "RepositoryRegistration",
    "WorkspaceManager",
    "register_external_repository",
    "register_local_repository",
    "validate_external_git_url",
]
