"""Baseline SBFL orchestration over qualified benchmark tasks."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from time import monotonic_ns
from typing import Any

from coverage import Coverage
from sqlalchemy.orm import Session

from app.artifacts import ArtifactReference, ArtifactStore
from app.benchmark.loader import LoadedBenchmarkTask, load_benchmark_task
from app.config import Settings
from app.db.models import FaultLocalizationResult as FaultLocalizationResultRecord
from app.db.models import Repository as RepositoryRecord
from app.db.models import Run as RunRecord
from app.db.models import Task as TaskRecord
from app.filesystem import paths_overlap
from app.repositories.workspace import WorkspaceManager
from app.schemas.common import validate_repository_path
from app.schemas.research import FaultLocalizationResult

from .coverage import PerTestCoverage, PerTestCoverageCollector
from .ranking import RankedLocation, rank_of_location, rank_spectrum
from .spectrum import (
    SourceLocation,
    TestExecution,
    build_line_spectrum,
    resolve_symbols,
)


class FaultLocalizationError(RuntimeError):
    """Raised when reproducible localization evidence cannot be produced."""


@dataclass(frozen=True, slots=True)
class PilotLocalizationMetrics:
    true_fault_rank: int | None
    top_1: bool
    top_5: bool
    top_10: bool


@dataclass(frozen=True, slots=True)
class FaultLocalizationRun:
    task_id: str
    result: FaultLocalizationResult
    full_ranking: tuple[RankedLocation, ...]
    metrics: PilotLocalizationMetrics
    coverage_artifact: ArtifactReference
    passing_tests: int
    failing_tests: int
    skipped_tests: int


class FaultLocalizationService:
    """Collect and persist pre-agent Ochiai evidence for one benchmark task."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        collector: PerTestCoverageCollector | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.collector = collector or PerTestCoverageCollector()
        self.workspaces = WorkspaceManager(settings.effective_workspace_root)
        self.artifacts = ArtifactStore(
            settings.effective_artifact_root,
            max_artifact_bytes=settings.max_artifact_size_bytes,
        )

    def localize(
        self,
        manifest_path: str | Path,
        *,
        benchmark_root: str | Path | None = None,
        run_id: str | None = None,
        top_k: int = 10,
    ) -> FaultLocalizationRun:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        loaded = load_benchmark_task(manifest_path, benchmark_root=benchmark_root)
        if loaded.repository_path is None:
            raise NotImplementedError("Phase 4 localization supports local benchmarks only")
        localization_id = run_id or localization_run_id(
            loaded.task.task_id,
            loaded.task.base_commit,
        )
        self._require_qualified_task(loaded)
        for runtime_root in (
            self.settings.effective_workspace_root.resolve(strict=False),
            self.settings.effective_artifact_root.resolve(strict=False),
        ):
            if paths_overlap(loaded.repository_path, runtime_root):
                raise FaultLocalizationError(
                    "benchmark repository must not overlap runtime storage"
                )

        started_at = datetime.now(UTC)
        started_ns = monotonic_ns()
        workspace = self.workspaces.create(
            run_id=localization_id,
            source_repository=loaded.repository_path,
            base_commit=loaded.task.base_commit,
        )
        try:
            coverage_result = self.collector.collect(
                workspace=workspace.path,
                visible_test_command=loaded.task.visible_test_command,
                hidden_test_command=loaded.task.hidden_test_command,
                hidden_tests=loaded.hidden_tests_path,
                source_paths=loaded.task.allowed_paths,
                timeout_seconds=loaded.task.timeout_seconds,
            )
            relevant_lines, symbols = _source_inventory(
                workspace.path,
                loaded.task.allowed_paths,
                max_file_bytes=self.settings.max_file_size_bytes,
            )
            executions = _spectrum_executions(coverage_result)
            spectrum = build_line_spectrum(
                executions,
                relevant_lines=relevant_lines,
                symbols=symbols,
            )
            full_ranking = rank_spectrum(spectrum)
            if not full_ranking:
                raise FaultLocalizationError("no executable source lines were localized")
            metrics = _known_fault_metrics(loaded, full_ranking)
            coverage_payload = _coverage_payload(
                loaded,
                coverage_result,
                relevant_lines=relevant_lines,
            )
        finally:
            try:
                self.workspaces.reset(workspace)
            finally:
                self.workspaces.remove(workspace)

        coverage_artifact = self.artifacts.store_text(
            run_id=localization_id,
            kind="coverage",
            text=json.dumps(coverage_payload, sort_keys=True, separators=(",", ":")),
            suffix=".json",
        )
        ranked = full_ranking[:top_k]
        result = FaultLocalizationResult(
            run_id=localization_id,
            metric="ochiai",
            ranked_locations=[_ranked_location_payload(item) for item in ranked],
            top_k=top_k,
            fault_rank_if_known=metrics.true_fault_rank,
            coverage_artifact=coverage_artifact.relative_path,
        )
        finished_at = datetime.now(UTC)
        duration_ms = (monotonic_ns() - started_ns) // 1_000_000
        self._persist(
            loaded,
            result,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
        return FaultLocalizationRun(
            task_id=loaded.task.task_id,
            result=result,
            full_ranking=full_ranking,
            metrics=metrics,
            coverage_artifact=coverage_artifact,
            passing_tests=sum(test.outcome == "passed" for test in coverage_result.tests),
            failing_tests=sum(test.outcome == "failed" for test in coverage_result.tests),
            skipped_tests=sum(test.outcome == "skipped" for test in coverage_result.tests),
        )

    def _require_qualified_task(self, loaded: LoadedBenchmarkTask) -> None:
        task = self.session.get(TaskRecord, loaded.task.task_id)
        if task is None:
            raise FaultLocalizationError(
                "task must be persisted by Phase 3 qualification before localization"
            )
        repository = self.session.get(RepositoryRecord, task.repository_id)
        if repository is None or repository.base_commit != loaded.task.base_commit:
            raise FaultLocalizationError("persisted task base commit differs from the manifest")
        assert loaded.repository_path is not None
        try:
            persisted_source = Path(repository.source).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise FaultLocalizationError("persisted repository source is unavailable") from error
        if persisted_source != loaded.repository_path:
            raise FaultLocalizationError("persisted repository source differs from the manifest")

    def _persist(
        self,
        loaded: LoadedBenchmarkTask,
        result: FaultLocalizationResult,
        *,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: int,
    ) -> None:
        run = self.session.get(RunRecord, result.run_id)
        run_values: dict[str, Any] = {
            "task_id": loaded.task.task_id,
            "configuration_id": "sbfl-only",
            "model": "not-applicable",
            "model_parameters": {"llm_used": False, "phase": 4},
            "status": "localized",
            "started_at": started_at,
            "finished_at": finished_at,
            "latency_ms": duration_ms,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": None,
            "tool_calls": 0,
            "files_read": 0,
            "lines_exposed": 0,
            "repair_attempted": False,
            "final_resolution": None,
            "failure_category": None,
        }
        if run is None:
            self.session.add(RunRecord(run_id=result.run_id, **run_values))
            self.session.flush()
        else:
            if run.task_id != loaded.task.task_id or run.configuration_id != "sbfl-only":
                raise FaultLocalizationError("run_id already belongs to another experiment run")
            for field, value in run_values.items():
                setattr(run, field, value)

        values = result.model_dump()
        existing = self.session.get(FaultLocalizationResultRecord, result.run_id)
        if existing is None:
            self.session.add(FaultLocalizationResultRecord(**values))
        else:
            for field, value in values.items():
                setattr(existing, field, value)
        self.session.flush()


def format_fault_localization_evidence(
    ranking: tuple[RankedLocation, ...],
) -> str:
    """Render bounded SBFL evidence without claiming that it is fault truth."""

    lines = ["FAULT LOCALIZATION EVIDENCE"]
    for item in ranking:
        symbol = f" [{item.symbol}]" if item.symbol else ""
        lines.append(
            f"{item.rank}. {item.location.file}:{item.location.line}{symbol} "
            f"Ochiai = {item.score:.6f}"
        )
    lines.append("Evidence only; suspiciousness does not establish fault truth.")
    return "\n".join(lines)


def _source_inventory(
    workspace: Path,
    allowed_paths: list[str],
    *,
    max_file_bytes: int,
    max_source_files: int = 2_000,
) -> tuple[tuple[SourceLocation, ...], dict[SourceLocation, str]]:
    root = workspace.resolve(strict=True)
    source_files: set[Path] = set()
    for entry in allowed_paths:
        normalized = validate_repository_path(entry)
        candidate = root.joinpath(*PurePosixPath(normalized.rstrip("/")).parts)
        _reject_linked_components(candidate, root)
        selected = candidate.resolve(strict=True)
        if not _is_within(selected, root):
            raise FaultLocalizationError("allowed source path escaped the workspace")
        if selected.is_file():
            if selected.suffix == ".py":
                source_files.add(selected)
        elif selected.is_dir():
            for source_path in selected.rglob("*.py"):
                _reject_linked_components(source_path, root)
                canonical = source_path.resolve(strict=True)
                if not _is_within(canonical, selected) or not canonical.is_file():
                    raise FaultLocalizationError("source inventory escaped its allowlist")
                source_files.add(canonical)
                if len(source_files) > max_source_files:
                    raise FaultLocalizationError("source inventory exceeds the file bound")
        else:
            raise FaultLocalizationError("allowed source path is not a file or directory")

    locations: list[SourceLocation] = []
    symbols: dict[SourceLocation, str] = {}
    analyzer = Coverage(data_file=None, config_file=False)
    for source_file in sorted(source_files):
        if source_file.stat().st_size > max_file_bytes:
            raise FaultLocalizationError("source file exceeds the configured bound")
        try:
            source_text = source_file.read_text(encoding="utf-8")
            statements = analyzer.analysis2(str(source_file))[1]
            symbol_by_line = resolve_symbols(source_text, statements)
        except (OSError, UnicodeError, SyntaxError) as error:
            raise FaultLocalizationError("source inventory could not be analyzed") from error
        relative = source_file.relative_to(root).as_posix()
        for line in statements:
            location = SourceLocation(relative, line)
            locations.append(location)
            symbol = symbol_by_line[line]
            if symbol is not None:
                symbols[location] = symbol
    return tuple(sorted(set(locations))), symbols


def _spectrum_executions(coverage_result: PerTestCoverage) -> tuple[TestExecution, ...]:
    return tuple(
        TestExecution(
            test_id=test.test_id,
            passed=test.outcome == "passed",
            executed_lines=frozenset(
                SourceLocation(line.file, line.line) for line in test.executed_lines
            ),
        )
        for test in coverage_result.tests
        if test.outcome != "skipped"
    )


def _known_fault_metrics(
    loaded: LoadedBenchmarkTask,
    ranking: tuple[RankedLocation, ...],
) -> PilotLocalizationMetrics:
    ranks = [
        rank
        for fault in loaded.task.known_faults
        if (
            rank := rank_of_location(
                ranking,
                SourceLocation(fault.file, fault.line),
            )
        )
        is not None
    ]
    true_rank = min(ranks) if ranks else None
    return PilotLocalizationMetrics(
        true_fault_rank=true_rank,
        top_1=true_rank is not None and true_rank <= 1,
        top_5=true_rank is not None and true_rank <= 5,
        top_10=true_rank is not None and true_rank <= 10,
    )


def _coverage_payload(
    loaded: LoadedBenchmarkTask,
    coverage_result: PerTestCoverage,
    *,
    relevant_lines: tuple[SourceLocation, ...],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": loaded.task.task_id,
        "base_commit": loaded.task.base_commit,
        "manifest_sha256": _sha256_file(loaded.manifest_path),
        "repository_sha256": _sha256_file(loaded.repository_path),
        "python_version": platform.python_version(),
        "coverage_version": _package_version("coverage"),
        "pytest_version": _package_version("pytest"),
        "pytest_cov_version": _package_version("pytest-cov"),
        "hidden_test_identifiers": "opaque-sha256",
        "source_files": list(coverage_result.source_files),
        "executable_lines": [asdict(location) for location in relevant_lines],
        "suite_executions": [asdict(execution) for execution in coverage_result.executions],
        "tests": [
            {
                "test_id": test.test_id,
                "suite": test.suite,
                "outcome": test.outcome,
                "executed_lines": [asdict(line) for line in test.executed_lines],
            }
            for test in coverage_result.tests
        ],
    }


def _ranked_location_payload(item: RankedLocation) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "file": item.location.file,
        "line": item.location.line,
        "symbol": item.symbol,
        "ochiai": item.score,
        "ef": item.ef,
        "nf": item.nf,
        "ep": item.ep,
    }


def localization_run_id(task_id: str, base_commit: str) -> str:
    """Return the stable identifier used for pre-agent localization evidence."""

    digest = hashlib.sha256(f"{task_id}:{base_commit}:ochiai-v1".encode()).hexdigest()
    return f"sbfl-{digest[:24]}"


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unavailable"


def _sha256_file(path: Path | None) -> str:
    if path is None:
        raise ValueError("cannot hash a remote repository reference")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_linked_components(candidate: Path, root: Path) -> None:
    current = candidate
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise FaultLocalizationError("source inventory cannot traverse links")
        if root not in current.parents:
            raise FaultLocalizationError("source inventory escaped the workspace")
        current = current.parent


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
