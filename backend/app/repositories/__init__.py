"""Safe local Git repository services."""

from .registration import RepositoryRegistration, register_local_repository
from .workspace import DisposableWorkspace, WorkspaceManager

__all__ = [
    "DisposableWorkspace",
    "RepositoryRegistration",
    "WorkspaceManager",
    "register_local_repository",
]
