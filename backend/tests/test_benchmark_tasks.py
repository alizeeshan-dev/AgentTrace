from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.benchmark import LoadedBenchmarkTask, load_benchmark_task

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"
TASK_MANIFESTS = tuple(sorted((BENCHMARK_ROOT / "tasks").glob("*.yaml")))


def _run(
    arguments: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=30,
    )


def _clone_task(task: LoadedBenchmarkTask, destination: Path) -> Path:
    assert task.repository_path is not None
    cloned = _run(
        [
            "git",
            "clone",
            "--quiet",
            "--branch",
            "main",
            str(task.repository_path),
            str(destination),
        ],
        cwd=destination.parent,
    )
    assert cloned.returncode == 0, cloned.stderr
    checked_out = _run(["git", "rev-parse", "HEAD"], cwd=destination)
    assert checked_out.returncode == 0
    assert checked_out.stdout.strip() == task.task.base_commit
    return destination


def _pytest(repository: Path, test_path: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository)
    return _run(
        [sys.executable, "-m", "pytest", "-q", str(test_path)],
        cwd=repository,
        env=environment,
    )


def test_complete_benchmark_manifests_load() -> None:
    assert len(TASK_MANIFESTS) >= 15

    loaded = [
        load_benchmark_task(manifest, benchmark_root=BENCHMARK_ROOT)
        for manifest in TASK_MANIFESTS
    ]

    assert {
        "boundary-empty-input",
        "transformation-slug-collapse",
        "validation-business-rule",
    }.issubset({item.task.task_id for item in loaded})
    assert all(item.task.task_category == "bug_fix" for item in loaded)
    assert all(item.repository_path and item.repository_path.suffix == ".bundle" for item in loaded)
    assert all(len(item.task.known_faults) == 1 for item in loaded)


@pytest.mark.parametrize("manifest", TASK_MANIFESTS, ids=lambda path: path.stem)
def test_benchmark_task_fails_before_and_passes_after_known_correct_patch(
    manifest: Path,
    tmp_path: Path,
) -> None:
    task = load_benchmark_task(manifest, benchmark_root=BENCHMARK_ROOT)
    repository = _clone_task(task, tmp_path / task.task.task_id)

    visible_before = _pytest(repository, repository / "tests")
    hidden_before = _pytest(repository, task.hidden_tests_path)
    assert visible_before.returncode == 0, visible_before.stdout + visible_before.stderr
    assert hidden_before.returncode != 0, "the baseline bug was not reproduced"

    applied = _run(
        ["git", "apply", "--check", str(task.known_correct_patch_path)],
        cwd=repository,
    )
    assert applied.returncode == 0, applied.stderr
    applied = _run(["git", "apply", str(task.known_correct_patch_path)], cwd=repository)
    assert applied.returncode == 0, applied.stderr

    visible_after = _pytest(repository, repository / "tests")
    hidden_after = _pytest(repository, task.hidden_tests_path)
    assert visible_after.returncode == 0, visible_after.stdout + visible_after.stderr
    assert hidden_after.returncode == 0, hidden_after.stdout + hidden_after.stderr


@pytest.mark.parametrize("manifest", TASK_MANIFESTS, ids=lambda path: path.stem)
def test_hidden_tests_are_outside_agent_readable_repository(
    manifest: Path,
    tmp_path: Path,
) -> None:
    task = load_benchmark_task(manifest, benchmark_root=BENCHMARK_ROOT)
    repository = _clone_task(task, tmp_path / task.task.task_id)

    assert repository not in task.hidden_tests_path.parents
    assert task.hidden_tests_path not in repository.parents
    tree = _run(["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=repository)
    assert tree.returncode == 0
    assert "hidden_tests" not in tree.stdout
