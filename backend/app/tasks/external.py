"""External repair-task creation and unified task-definition loading."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import Field, JsonValue, StringConstraints, field_validator
from sqlalchemy.orm import Session

from app.benchmark.loader import LoadedBenchmarkTask, load_benchmark_task
from app.config import Settings
from app.db.models import Repository, Task
from app.filesystem import validate_runtime_root
from app.schemas.common import CommitSha, Identifier, ResearchSchema, validate_repository_path


class ExternalTaskError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ExternalTaskDefinition(ResearchSchema):
    """Portable managed definition without benchmark ground-truth fields."""

    schema_version: Literal[1] = 1
    task_source: Literal["external"] = "external"
    task_id: Identifier
    repository_id: Identifier
    repository: str
    repository_url: Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
    base_commit: CommitSha
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
    ]
    task_category: Literal["bug_fix", "refactor"] = "bug_fix"
    difficulty: Literal["unspecified"] = "unspecified"
    allowed_paths: Annotated[list[str], Field(max_length=30)] = Field(default_factory=list)
    forbidden_paths: Annotated[list[str], Field(max_length=30)] = Field(default_factory=list)
    visible_test_command: Annotated[
        str, StringConstraints(min_length=1, max_length=500, pattern=r"^[^\x00\r\n]+$")
    ] | None = None
    hidden_test_command: None = None
    property_profile: None = None
    symbolic_profile: None = None
    known_correct_patch: None = None
    timeout_seconds: Annotated[int, Field(ge=1, le=3_600)] = 300
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("repository")
    @classmethod
    def repository_is_managed_relative_path(cls, value: str) -> str:
        return validate_repository_path(value)

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def paths_are_safe_and_unique(cls, values: list[str]) -> list[str]:
        normalized = [validate_repository_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("task paths must be unique")
        return normalized


@dataclass(frozen=True, slots=True)
class LoadedExternalTask:
    task: ExternalTaskDefinition
    manifest_path: Path
    benchmark_root: Path
    repository_path: Path
    known_correct_patch_path: None = None
    hidden_tests_path: None = None


type LoadedTaskDefinition = LoadedBenchmarkTask | LoadedExternalTask


def load_task_definition(
    manifest_path: str | Path,
    *,
    benchmark_root: str | Path | None = None,
) -> LoadedTaskDefinition:
    """Dispatch generated external definitions without changing benchmark loading."""

    supplied = Path(manifest_path)
    try:
        raw = supplied.read_text(encoding="utf-8")
        marker = yaml.safe_load(raw)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError("Task definition could not be read") from error
    if not isinstance(marker, dict) or marker.get("task_source") != "external":
        return load_benchmark_task(supplied, benchmark_root=benchmark_root)
    if supplied.is_symlink():
        raise ValueError("external task definition cannot be a link")
    manifest = supplied.resolve(strict=True)
    root = (
        Path(benchmark_root).resolve(strict=True)
        if benchmark_root is not None
        else manifest.parent.parent.resolve(strict=True)
    )
    if not manifest.is_relative_to(root):
        raise ValueError("external task definition escapes its managed root")
    task = ExternalTaskDefinition.model_validate(marker)
    repository = root.joinpath(*task.repository.split("/"))
    if repository.is_symlink() or (
        hasattr(repository, "is_junction") and repository.is_junction()
    ):
        raise ValueError("managed repository cannot be a link or junction")
    canonical_repository = repository.resolve(strict=True)
    if not canonical_repository.is_relative_to(root) or not canonical_repository.is_dir():
        raise ValueError("managed external repository is unavailable")
    return LoadedExternalTask(task, manifest, root, canonical_repository)


class ExternalTaskService:
    """Persist a user-authored repair task bound to one immutable external commit."""

    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.task_root = validate_runtime_root(
            settings.state_dir / "external_tasks", field_name="external_task_root"
        )

    def create(
        self,
        *,
        repository_id: str,
        title: str,
        description: str,
        task_id: str | None = None,
        task_category: Literal["bug_fix", "refactor"] = "bug_fix",
        test_command: str | None = None,
        allowed_paths: list[str] | None = None,
        forbidden_paths: list[str] | None = None,
        timeout_seconds: int = 300,
        trusted_execution_acknowledged: bool = False,
    ) -> Task:
        repository = self.session.get(Repository, repository_id)
        if repository is None:
            raise ExternalTaskError("repository_not_found", "Repository not found.")
        if repository.source_type != "external_git":
            raise ExternalTaskError(
                "invalid_repository_source", "External tasks require an external Git repository."
            )
        if repository.primary_language != "Python":
            raise ExternalTaskError(
                "unsupported_repository", "External tasks currently support Python only."
            )
        if trusted_execution_acknowledged:
            repository.trusted_for_local_execution = True
            repository.trust_confirmed_at = datetime.now(UTC)
        command = _verification_command(test_command, repository.test_command)
        metadata = dict(repository.repository_metadata or {})
        selected_allowed = (
            allowed_paths
            if allowed_paths is not None
            else _string_list(metadata.get("suggested_allowed_paths"))
        )
        selected_forbidden = (
            forbidden_paths
            if forbidden_paths is not None
            else _default_forbidden_paths(metadata)
        )
        identity = task_id or _task_identity(
            repository_id,
            title,
            description,
            task_category,
            command,
            selected_allowed,
            selected_forbidden,
        )
        managed_source = Path(repository.source).resolve(strict=True)
        managed_root = self.settings.state_dir.resolve(strict=False)
        try:
            repository_reference = managed_source.relative_to(managed_root).as_posix()
        except ValueError as error:
            raise ExternalTaskError(
                "repository_binding_invalid", "Managed repository is outside AgentTrace state."
            ) from error
        definition = ExternalTaskDefinition(
            task_id=identity,
            repository_id=repository.repository_id,
            repository=repository_reference,
            repository_url=repository.repository_url or "",
            base_commit=repository.base_commit,
            title=title,
            description=description,
            task_category=task_category,
            allowed_paths=selected_allowed,
            forbidden_paths=selected_forbidden,
            visible_test_command=command,
            timeout_seconds=timeout_seconds,
            metadata={
                "verification_configured": command is not None,
                "mutation_score_assessed": False,
                "hidden_tests_available": False,
                "task_source": "external",
            },
        )
        self.task_root.mkdir(parents=True, exist_ok=True)
        definition_path = self.task_root / f"{identity}.yaml"
        values = {
            "task_id": identity,
            "repository_id": repository.repository_id,
            "title": definition.title,
            "description": definition.description,
            "task_category": definition.task_category,
            "difficulty": definition.difficulty,
            "allowed_paths": definition.allowed_paths,
            "forbidden_paths": definition.forbidden_paths,
            "visible_test_command": command or "",
            "hidden_test_command": "",
            "property_profile": None,
            "symbolic_profile": None,
            "known_correct_patch": None,
            "task_source": "external",
            "verification_configured": command is not None,
            "definition_path": str(definition_path.resolve(strict=False)),
            "created_at": datetime.now(UTC),
        }
        existing = self.session.get(Task, identity)
        if existing is not None:
            comparable = tuple(key for key in values if key not in {"created_at"})
            if any(getattr(existing, key) != values[key] for key in comparable):
                raise ExternalTaskError(
                    "task_conflict", "External task ID is already bound to different inputs."
                )
            return existing
        if definition_path.exists():
            raise ExternalTaskError(
                "task_conflict", "External task definition path is already occupied."
            )
        temporary = definition_path.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_text(
            yaml.safe_dump(
                definition.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(definition_path)
        task = Task(**values)
        self.session.add(task)
        self.session.flush()
        return task


def _verification_command(explicit: str | None, detected: str) -> str | None:
    selected = explicit.strip() if explicit is not None and explicit.strip() else detected.strip()
    if not selected:
        return None
    # Verification imports task loading, so keep this command validator lazy.
    from app.verification.gates import StandardGateFactory

    try:
        StandardGateFactory.visible_tests(selected, timeout_seconds=60)
    except ValueError as error:
        raise ExternalTaskError(
            "invalid_test_command",
            "Verification must use a non-shell pytest command.",
        ) from error
    return selected


def _task_identity(
    repository_id: str,
    title: str,
    description: str,
    category: str,
    command: str | None,
    allowed: list[str],
    forbidden: list[str],
) -> str:
    payload = "\0".join(
        (
            repository_id,
            title.strip(),
            description.strip(),
            category,
            command or "",
            "\n".join(allowed),
            "\n".join(forbidden),
        )
    )
    return f"external-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)][:30]


def _default_forbidden_paths(metadata: dict[str, Any]) -> list[str]:
    indicators = metadata.get("python_project_indicators", {})
    if not isinstance(indicators, dict) or not indicators.get("tests_directory"):
        return []
    return ["tests/", "test/"]
