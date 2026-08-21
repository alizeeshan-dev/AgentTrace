"""Safe YAML loading for evaluator-owned benchmark manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from app.benchmark.schema import BenchmarkTask


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class LoadedBenchmarkTask:
    """Validated manifest plus canonical evaluator-only artifact locations."""

    task: BenchmarkTask
    manifest_path: Path
    benchmark_root: Path
    repository_path: Path | None
    known_correct_patch_path: Path
    hidden_tests_path: Path


def load_benchmark_task(
    manifest_path: str | Path,
    *,
    benchmark_root: str | Path | None = None,
) -> LoadedBenchmarkTask:
    """Load one manifest and resolve its local artifacts beneath the corpus root."""

    supplied_manifest = Path(manifest_path)
    if supplied_manifest.is_symlink():
        raise ValueError("benchmark manifest must not be a symlink")
    manifest = supplied_manifest.resolve(strict=True)
    if benchmark_root is not None:
        supplied_root = Path(benchmark_root)
        if supplied_root.is_symlink():
            raise ValueError("benchmark root must not be a symlink")
        root = supplied_root.resolve(strict=True)
    else:
        root = _find_benchmark_root(manifest)
    _require_within(manifest, root, label="manifest")
    if manifest.suffix.casefold() not in {".yaml", ".yml"}:
        raise ValueError("benchmark manifests must use a .yaml or .yml extension")
    if not manifest.is_file():
        raise ValueError("benchmark manifest must be a regular non-symlink file")

    payload = yaml.load(manifest.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(payload, dict):
        raise ValueError("benchmark manifest must contain one mapping")
    task = BenchmarkTask.model_validate(payload)

    repository_path: Path | None = None
    if not task.repository.startswith("https://"):
        repository_path = _resolve_regular_artifact(root, task.repository, label="repository")
    patch_path = _resolve_regular_artifact(
        root,
        task.known_correct_patch,
        label="known-correct patch",
    )
    hidden_tests_reference = root / "hidden_tests" / task.task_id
    _reject_linked_path(hidden_tests_reference, root=root, label="hidden tests")
    hidden_tests_path = hidden_tests_reference.resolve(strict=True)
    _require_within(hidden_tests_path, root / "hidden_tests", label="hidden tests")
    if not hidden_tests_path.is_dir():
        raise ValueError("hidden tests must be a regular directory")
    if repository_path is not None and _paths_overlap(repository_path, hidden_tests_path):
        raise ValueError("hidden tests must be physically outside the agent repository")

    return LoadedBenchmarkTask(
        task=task,
        manifest_path=manifest,
        benchmark_root=root,
        repository_path=repository_path,
        known_correct_patch_path=patch_path,
        hidden_tests_path=hidden_tests_path,
    )


def _find_benchmark_root(manifest: Path) -> Path:
    for candidate in manifest.parents:
        if candidate.name == "benchmark":
            return candidate.resolve(strict=True)
    raise ValueError("benchmark_root is required outside a benchmark/ directory")


def _resolve_regular_artifact(root: Path, reference: str, *, label: str) -> Path:
    artifact_reference = root / Path(*reference.split("/"))
    _reject_linked_path(artifact_reference, root=root, label=label)
    artifact = artifact_reference.resolve(strict=True)
    _require_within(artifact, root, label=label)
    if not artifact.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    return artifact


def _require_within(candidate: Path, root: Path, *, label: str) -> None:
    canonical_root = root.resolve(strict=True)
    try:
        candidate.relative_to(canonical_root)
    except ValueError as error:
        raise ValueError(f"{label} escapes the benchmark root") from error


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _reject_linked_path(candidate: Path, *, root: Path, label: str) -> None:
    current = candidate
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError(f"{label} cannot use symlinks or junctions")
        if root not in current.parents:
            raise ValueError(f"{label} escapes the benchmark root")
        current = current.parent
