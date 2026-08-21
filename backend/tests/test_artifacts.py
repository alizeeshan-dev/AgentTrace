from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from app.artifacts import ArtifactStore


def test_artifacts_are_content_addressed_and_stably_referenced(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    content = "verification output\n"

    first = store.store_text(run_id="run-001", kind="logs", text=content, suffix=".log")
    second = store.store_text(run_id="run-001", kind="logs", text=content, suffix=".log")

    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert first == second
    assert first.sha256 == expected_hash
    assert first.relative_path == f"run-001/logs/{expected_hash}.log"
    assert not Path(first.relative_path).is_absolute()
    assert store.read_bytes(first) == content.encode("utf-8")


def test_artifacts_are_isolated_by_run_and_kind(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    patch = store.store_text(run_id="run-a", kind="patches", text="diff", suffix=".diff")
    coverage = store.store_text(run_id="run-b", kind="coverage", text="{}", suffix=".json")

    assert patch.relative_path.startswith("run-a/patches/")
    assert coverage.relative_path.startswith("run-b/coverage/")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_artifact_directories_are_owner_only(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    reference = store.store_text(run_id="run-a", kind="model", text="raw model data")

    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "run-a").stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / "run-a" / "model").stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / reference.relative_path).stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "run_id",
    [
        "../escape",
        "run/name",
        "C:escape",
        "Run-A",
        "run.",
        "run--a",
        "con",
        "",
        ".",
    ],
)
def test_rejects_unsafe_run_identifiers(tmp_path: Path, run_id: str) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError):
        store.store_bytes(run_id=run_id, kind="other", data=b"content")


@pytest.mark.parametrize("reference", ["../secret", "/absolute", "C:/absolute", "run\\file"])
def test_rejects_unsafe_artifact_references(tmp_path: Path, reference: str) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError):
        store.read_bytes(reference)


def test_artifact_size_bound_is_enforced(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts", max_artifact_bytes=4)

    with pytest.raises(ValueError, match="size bound"):
        store.store_bytes(run_id="run", kind="other", data=b"12345")


def test_artifact_root_rejects_windows_aliasing_segment(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe filesystem segment"):
        ArtifactStore(tmp_path / "artifacts.")


def test_artifact_root_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    link = tmp_path / "artifact-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symlink creation is unavailable on this Windows configuration: {error}")

    with pytest.raises(ValueError, match="cannot traverse"):
        ArtifactStore(link)


def test_artifact_reference_cannot_follow_symlink_outside_store(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    run_directory = store.root / "run" / "logs"
    run_directory.mkdir(parents=True)
    link = run_directory / "escape.log"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Symlink creation is unavailable on this Windows configuration: {error}")

    with pytest.raises(ValueError, match="escapes"):
        store.read_bytes("run/logs/escape.log")


def test_artifact_reference_cannot_cross_run_through_symlink(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    target = store.store_text(run_id="run-b", kind="logs", text="private")
    run_directory = store.root / "run-a" / "logs"
    run_directory.mkdir(parents=True)
    link = run_directory / "alias.txt"
    try:
        link.symlink_to(store.root / target.relative_path)
    except OSError as error:
        pytest.skip(f"Symlink creation is unavailable on this Windows configuration: {error}")

    with pytest.raises(ValueError, match="escapes"):
        store.read_bytes("run-a/logs/alias.txt")
