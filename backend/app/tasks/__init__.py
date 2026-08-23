"""Task definitions shared by benchmark and external-repository workflows."""

from .external import (
    ExternalTaskDefinition,
    ExternalTaskError,
    ExternalTaskService,
    LoadedExternalTask,
    LoadedTaskDefinition,
    load_task_definition,
)

__all__ = [
    "ExternalTaskDefinition",
    "ExternalTaskError",
    "ExternalTaskService",
    "LoadedExternalTask",
    "LoadedTaskDefinition",
    "load_task_definition",
]
