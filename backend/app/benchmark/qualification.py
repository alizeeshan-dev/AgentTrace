"""Reusable benchmark qualification workflow for fixed pilot tasks."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.artifacts import ArtifactReference, ArtifactStore
from app.benchmark.loader import LoadedBenchmarkTask, load_benchmark_task
from app.benchmark.runner import CommandOutcome, QualificationCommandRunner
from app.config import Settings
from app.db.models import BenchmarkQuality as BenchmarkQualityRecord
from app.db.models import Repository as RepositoryRecord
from app.db.models import Task as TaskRecord
from app.filesystem import paths_overlap
from app.mutation import (
    MutationEnvironmentUnavailable,
    MutationExecution,
    MutationExecutionError,
    PytestGremlinsAdapter,
    PytestGremlinsConfig,
)
from app.repositories.git import GitError, run_git
from app.repositories.workspace import WorkspaceManager
from app.schemas.research import BenchmarkQuality


class MutationRunner(Protocol):
    def run(
        self,
        workspace: str | Path,
        config: PytestGremlinsConfig,
        *,
        manual_exclusions: Mapping[str, str] | None = None,
    ) -> MutationExecution: ...


class QualificationError(RuntimeError):
    """Raised when a task fails an admission prerequisite."""


@dataclass(frozen=True, slots=True)
class QualificationResult:
    task_id: str
    status: str
    baseline_visible: CommandOutcome
    baseline_hidden: CommandOutcome
    corrected_visible: CommandOutcome
    corrected_hidden: CommandOutcome
    quality: BenchmarkQuality
    known_patch_artifact: ArtifactReference
    log_artifact: ArtifactReference
    mutation_artifact: ArtifactReference


class BenchmarkQualificationService:
    """Qualify one immutable benchmark task and persist its evidence."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        mutation_runner: MutationRunner | None = None,
        command_runner: QualificationCommandRunner | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.mutation_runner = mutation_runner or PytestGremlinsAdapter()
        self.command_runner = command_runner or QualificationCommandRunner()
        self.workspaces = WorkspaceManager(settings.effective_workspace_root)
        self.artifacts = ArtifactStore(
            settings.effective_artifact_root,
            max_artifact_bytes=settings.max_artifact_size_bytes,
        )

    def qualify(
        self,
        manifest_path: str | Path,
        *,
        benchmark_root: str | Path | None = None,
    ) -> QualificationResult:
        loaded = load_benchmark_task(manifest_path, benchmark_root=benchmark_root)
        if loaded.repository_path is None:
            raise NotImplementedError("Phase 3 qualification supports local repositories only")
        for runtime_root in (
            self.settings.effective_workspace_root.resolve(strict=False),
            self.settings.effective_artifact_root.resolve(strict=False),
        ):
            if paths_overlap(loaded.repository_path, runtime_root):
                raise QualificationError(
                    "Benchmark repository must not overlap runtime storage"
                )

        artifact_id = _qualification_artifact_id(loaded.task.task_id)
        patch_bytes = loaded.known_correct_patch_path.read_bytes()
        known_patch_artifact = self.artifacts.store_bytes(
            run_id=artifact_id,
            kind="patches",
            data=patch_bytes,
            suffix=".patch",
        )
        workspace = self.workspaces.create(
            run_id=artifact_id,
            source_repository=loaded.repository_path,
            base_commit=loaded.task.base_commit,
        )
        mutation_execution: MutationExecution | None = None
        mutation_unavailable_reason: str | None = None
        mutation_execution_error: MutationExecutionError | None = None
        try:
            baseline_visible = self.command_runner.run(
                loaded.task.visible_test_command,
                cwd=workspace.path,
                timeout_seconds=loaded.task.timeout_seconds,
            )
            baseline_hidden = self.command_runner.run(
                loaded.task.hidden_test_command,
                cwd=workspace.path,
                timeout_seconds=loaded.task.timeout_seconds,
                hidden_tests=loaded.hidden_tests_path,
            )
            _validate_baseline(loaded, baseline_visible, baseline_hidden)

            _apply_reference_patch(workspace.path, loaded.known_correct_patch_path)
            corrected_visible = self.command_runner.run(
                loaded.task.visible_test_command,
                cwd=workspace.path,
                timeout_seconds=loaded.task.timeout_seconds,
            )
            corrected_hidden = self.command_runner.run(
                loaded.task.hidden_test_command,
                cwd=workspace.path,
                timeout_seconds=loaded.task.timeout_seconds,
                hidden_tests=loaded.hidden_tests_path,
            )
            if not corrected_visible.succeeded or not corrected_hidden.succeeded:
                raise QualificationError("known-correct patch did not pass all task tests")

            self.workspaces.reset(workspace)
            _apply_reference_patch(workspace.path, loaded.known_correct_patch_path)
            staged_hidden = _stage_hidden_tests(
                loaded.hidden_tests_path,
                workspace.path / ".agenttrace-evaluator" / "hidden_tests",
                max_file_bytes=self.settings.max_file_size_bytes,
            )
            mutation_config = _mutation_config(loaded, staged_hidden, workspace.path)
            try:
                mutation_execution = self.mutation_runner.run(
                    workspace.path,
                    mutation_config,
                    manual_exclusions=_manual_exclusions(loaded),
                )
            except MutationEnvironmentUnavailable as error:
                mutation_unavailable_reason = str(error)
            except MutationExecutionError as error:
                mutation_execution_error = error
        finally:
            try:
                self.workspaces.reset(workspace)
            finally:
                self.workspaces.remove(workspace)

        mutation_payload = _mutation_payload(
            mutation_execution,
            unavailable_reason=mutation_unavailable_reason,
            execution_error=mutation_execution_error,
        )
        mutation_artifact = self.artifacts.store_text(
            run_id=artifact_id,
            kind="mutation",
            text=json.dumps(mutation_payload, sort_keys=True, separators=(",", ":")),
            suffix=".json",
        )
        status = _qualification_status(mutation_execution, mutation_unavailable_reason)
        log_payload = _qualification_log(
            loaded,
            baseline_visible,
            baseline_hidden,
            corrected_visible,
            corrected_hidden,
            status=status,
            known_patch_artifact=known_patch_artifact,
            mutation_artifact=mutation_artifact,
        )
        log_artifact = self.artifacts.store_text(
            run_id=artifact_id,
            kind="qualification",
            text=json.dumps(log_payload, sort_keys=True, separators=(",", ":")),
            suffix=".json",
        )
        quality = _quality_schema(
            loaded,
            mutation_execution,
            mutation_unavailable_reason=mutation_unavailable_reason,
            mutation_execution_error=mutation_execution_error,
            status=status,
            mutation_artifact=mutation_artifact,
            log_artifact=log_artifact,
        )
        quality_result_artifact = self.artifacts.store_text(
            run_id=artifact_id,
            kind="qualification",
            text=quality.model_dump_json(),
            suffix=".json",
        )
        quality = quality.model_copy(
            update={"qualification_artifact": quality_result_artifact.relative_path}
        )
        self._persist(loaded, quality, known_patch_artifact)
        return QualificationResult(
            task_id=loaded.task.task_id,
            status=status,
            baseline_visible=baseline_visible,
            baseline_hidden=baseline_hidden,
            corrected_visible=corrected_visible,
            corrected_hidden=corrected_hidden,
            quality=quality,
            known_patch_artifact=known_patch_artifact,
            log_artifact=log_artifact,
            mutation_artifact=mutation_artifact,
        )

    def _persist(
        self,
        loaded: LoadedBenchmarkTask,
        quality: BenchmarkQuality,
        known_patch_artifact: ArtifactReference,
    ) -> None:
        assert loaded.repository_path is not None
        repository_id = _benchmark_repository_id(
            loaded.repository_path, loaded.task.base_commit
        )
        repository_values: dict[str, Any] = {
            "repository_id": repository_id,
            "name": loaded.repository_path.stem,
            "source": str(loaded.repository_path),
            "base_commit": loaded.task.base_commit,
            "python_version": ">=3.12",
            "test_command": loaded.task.visible_test_command,
        }
        repository = self.session.get(RepositoryRecord, repository_id)
        if repository is None:
            self.session.add(RepositoryRecord(**repository_values))
            self.session.flush()
        elif any(
            getattr(repository, field) != value
            for field, value in repository_values.items()
        ):
            raise QualificationError("persisted benchmark repository metadata changed")

        task_values: dict[str, Any] = {
            "task_id": loaded.task.task_id,
            "repository_id": repository_id,
            "title": loaded.task.title,
            "description": loaded.task.description,
            "task_category": loaded.task.task_category,
            "difficulty": loaded.task.difficulty,
            "allowed_paths": loaded.task.allowed_paths,
            "forbidden_paths": loaded.task.forbidden_paths,
            "visible_test_command": loaded.task.visible_test_command,
            "hidden_test_command": loaded.task.hidden_test_command,
            "property_profile": loaded.task.property_profile,
            "symbolic_profile": loaded.task.symbolic_profile,
            "known_correct_patch": known_patch_artifact.relative_path,
        }
        task = self.session.get(TaskRecord, loaded.task.task_id)
        if task is None:
            self.session.add(TaskRecord(**task_values))
            self.session.flush()
        elif any(getattr(task, field) != value for field, value in task_values.items()):
            raise QualificationError("persisted benchmark task metadata changed")

        existing_quality = self.session.get(BenchmarkQualityRecord, loaded.task.task_id)
        quality_values = quality.model_dump()
        if existing_quality is None:
            self.session.add(BenchmarkQualityRecord(**quality_values))
        else:
            for field, value in quality_values.items():
                setattr(existing_quality, field, value)
        self.session.flush()


def _validate_baseline(
    loaded: LoadedBenchmarkTask,
    visible: CommandOutcome,
    hidden: CommandOutcome,
) -> None:
    expected_visible = loaded.task.metadata.get("expected_baseline_visible_exit_code")
    expected_hidden = loaded.task.metadata.get("expected_baseline_hidden_exit_code")
    if isinstance(expected_visible, int) and visible.exit_code != expected_visible:
        raise QualificationError("visible baseline outcome differs from the manifest")
    if isinstance(expected_hidden, int) and hidden.exit_code != expected_hidden:
        raise QualificationError("hidden baseline outcome differs from the manifest")
    if visible.succeeded and hidden.succeeded:
        raise QualificationError("baseline did not reproduce a failing behavior")
    if visible.timed_out or hidden.timed_out:
        raise QualificationError("baseline verification timed out")


def _apply_reference_patch(repository: Path, patch_path: Path) -> None:
    try:
        run_git(["apply", "--check", "--", str(patch_path)], cwd=repository)
        run_git(["apply", "--", str(patch_path)], cwd=repository)
    except GitError as error:
        raise QualificationError("known-correct patch did not apply cleanly") from error


def _stage_hidden_tests(source: Path, destination: Path, *, max_file_bytes: int) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    copied = 0
    for item in sorted(source.rglob("*")):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise QualificationError("hidden evaluator cannot contain links or junctions")
        relative = item.relative_to(source)
        if "__pycache__" in relative.parts or item.suffix == ".pyc":
            continue
        target = destination / relative
        if item.is_dir():
            target.mkdir(exist_ok=True)
            continue
        if not item.is_file() or item.stat().st_size > max_file_bytes:
            raise QualificationError("hidden evaluator contains an unsupported file")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target, follow_symlinks=False)
        copied += 1
    if copied == 0:
        raise QualificationError("hidden evaluator contains no files")
    return destination


def _mutation_config(
    loaded: LoadedBenchmarkTask, staged_hidden: Path, workspace: Path
) -> PytestGremlinsConfig:
    raw_targets = loaded.task.metadata.get("mutation_targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise QualificationError("task metadata must define mutation_targets")
    mutation_targets = tuple(
        value for value in raw_targets if isinstance(value, str)
    )
    if len(mutation_targets) != len(raw_targets):
        raise QualificationError("mutation_targets must contain only paths")
    hidden_relative = staged_hidden.relative_to(workspace).as_posix()
    return PytestGremlinsConfig(
        source_paths=mutation_targets,
        test_selection=("tests", hidden_relative),
        pytest_args=("-q", "-p", "no:cacheprovider"),
        workers=1,
        timeout_seconds=max(loaded.task.timeout_seconds, 600),
    )


def _manual_exclusions(loaded: LoadedBenchmarkTask) -> dict[str, str]:
    raw = loaded.task.metadata.get("mutation_exclusions", {})
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
    ):
        raise QualificationError("mutation_exclusions must map mutant names to reasons")
    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _qualification_status(
    execution: MutationExecution | None, unavailable_reason: str | None
) -> str:
    if unavailable_reason is not None:
        return "mutation_unavailable"
    if execution is None or not execution.counts.completed:
        return "mutation_incomplete"
    if execution.counts.mutation_score is None:
        return "mutation_unusable"
    return "qualified"


def _quality_schema(
    loaded: LoadedBenchmarkTask,
    execution: MutationExecution | None,
    *,
    mutation_unavailable_reason: str | None,
    mutation_execution_error: MutationExecutionError | None,
    status: str,
    mutation_artifact: ArtifactReference,
    log_artifact: ArtifactReference,
) -> BenchmarkQuality:
    counts = execution.counts if execution is not None else None
    metadata: dict[str, Any] = {
        "schema_version": loaded.task.schema_version,
        "manifest_sha256": _sha256_file(loaded.manifest_path),
        "repository_sha256": _sha256_file(loaded.repository_path),
        "known_patch_sha256": _sha256_file(loaded.known_correct_patch_path),
        "tags": loaded.task.tags,
        "timeout_seconds": loaded.task.timeout_seconds,
        "qualification_status": status,
        "qualification_log_artifact": log_artifact.relative_path,
    }
    if execution is not None:
        metadata.update(
            {
                "commands": [list(command) for command in execution.commands],
                "config_sha256": execution.config_sha256,
                "platform": execution.platform,
                "python_version": execution.python_version,
                "report_relative_path": execution.report_relative_path,
                "started_at": execution.started_at.isoformat(),
                "finished_at": execution.finished_at.isoformat(),
                "status_counts": execution.counts.status_counts,
                "exclusion_reasons": execution.counts.exclusion_reasons,
                "tool_reported_score": execution.tool_reported_score,
                "score_normalization": (
                    "killed/(killed+survived); pardoned, invalid, and unusable "
                    "mutations excluded"
                ),
            }
        )
    if mutation_unavailable_reason is not None:
        metadata["mutation_unavailable_reason"] = mutation_unavailable_reason
    if mutation_execution_error is not None:
        metadata["mutation_execution_error"] = {
            "message": str(mutation_execution_error),
            "command": list(mutation_execution_error.command),
        }
    return BenchmarkQuality(
        task_id=loaded.task.task_id,
        baseline_status="verified",
        mutation_tool=execution.tool if execution else "pytest-gremlins",
        mutation_score=counts.mutation_score if counts else None,
        mutants_generated=counts.generated if counts else 0,
        mutants_killed=counts.killed if counts else 0,
        mutants_survived=counts.survived if counts else 0,
        mutants_excluded=counts.excluded if counts else 0,
        mutants_skipped=counts.skipped if counts else 0,
        mutants_invalid=counts.invalid if counts else 0,
        mutants_unusable=counts.unusable if counts else 0,
        mutation_tool_version=execution.tool_version if execution else None,
        mutation_completed=counts.completed if counts else False,
        mutation_duration_ms=execution.duration_ms if execution else None,
        mutation_artifact=mutation_artifact.relative_path,
        execution_metadata=metadata,
        quality_notes=(
            mutation_unavailable_reason
            or (str(mutation_execution_error) if mutation_execution_error else None)
            or (
                "Mutation run contains explicitly classified unusable mutants"
                if counts and counts.unusable
                else None
            )
        ),
    )


def _qualification_log(
    loaded: LoadedBenchmarkTask,
    baseline_visible: CommandOutcome,
    baseline_hidden: CommandOutcome,
    corrected_visible: CommandOutcome,
    corrected_hidden: CommandOutcome,
    *,
    status: str,
    known_patch_artifact: ArtifactReference,
    mutation_artifact: ArtifactReference,
) -> dict[str, Any]:
    return {
        "task_id": loaded.task.task_id,
        "base_commit": loaded.task.base_commit,
        "status": status,
        "baseline_visible": asdict(baseline_visible),
        "baseline_hidden": asdict(baseline_hidden),
        "corrected_visible": asdict(corrected_visible),
        "corrected_hidden": asdict(corrected_hidden),
        "known_patch_artifact": asdict(known_patch_artifact),
        "mutation_artifact": asdict(mutation_artifact),
    }


def _mutation_payload(
    execution: MutationExecution | None,
    *,
    unavailable_reason: str | None,
    execution_error: MutationExecutionError | None,
) -> dict[str, Any]:
    if execution is None:
        payload: dict[str, Any] = {
            "tool": "pytest-gremlins",
            "completed": False,
            "unavailable_reason": unavailable_reason,
        }
        if execution_error is not None:
            payload["execution_error"] = {
                "message": str(execution_error),
                "command": list(execution_error.command),
                "stdout": execution_error.stdout,
                "stderr": execution_error.stderr,
            }
        return payload
    payload = asdict(execution)
    payload["started_at"] = execution.started_at.isoformat()
    payload["finished_at"] = execution.finished_at.isoformat()
    return payload


def _benchmark_repository_id(repository: Path, base_commit: str) -> str:
    digest = hashlib.sha256(bytes.fromhex(_sha256_file(repository)))
    digest.update(base_commit.encode("ascii"))
    return f"benchmark-{digest.hexdigest()}"


def _qualification_artifact_id(task_id: str) -> str:
    return f"qual-{hashlib.sha256(task_id.encode()).hexdigest()[:20]}"


def _sha256_file(path: Path | None) -> str:
    if path is None:
        raise ValueError("cannot hash a remote repository reference")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
