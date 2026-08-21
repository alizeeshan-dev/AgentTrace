from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.repositories.path_policy import PathPolicyError, RepositoryPathPolicy


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "safe.py").write_text("SAFE = True\n", encoding="utf-8")
    (root / "src_old").mkdir()
    (root / "src_old" / "legacy.py").write_text("LEGACY = True\n", encoding="utf-8")
    (root / "hidden_tests").mkdir()
    (root / "hidden_tests" / "test_secret.py").write_text("SECRET = 42\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret\n", encoding="utf-8")
    return root, outside


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.txt",
        "src/../../outside.txt",
        "/absolute.py",
        "C:/absolute.py",
        "src\\safe.py",
        "src//safe.py",
        "./src/safe.py",
        "src/*.py",
        "src/safe.py:secret",
        "src/NUL.txt",
        "src/trailing.",
    ],
)
def test_rejects_traversal_absolute_and_non_literal_paths(tmp_path: Path, unsafe_path: str) -> None:
    root, _ = _tree(tmp_path)
    policy = RepositoryPathPolicy(root)

    with pytest.raises(PathPolicyError):
        policy.resolve(unsafe_path)


@pytest.mark.parametrize("git_path", [".git/config", ".GIT/config", "src/.Git/config"])
def test_git_administrative_paths_are_case_insensitively_protected(
    tmp_path: Path, git_path: str
) -> None:
    root, _ = _tree(tmp_path)
    policy = RepositoryPathPolicy(root)

    with pytest.raises(PathPolicyError, match="Git administrative"):
        policy.resolve(git_path, must_exist=False)


def test_forbidden_and_hidden_paths_take_precedence_over_allowlist(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    policy = RepositoryPathPolicy(
        root,
        allowed_paths=("src/", "hidden_tests/"),
        forbidden_paths=("src/safe.py",),
        hidden_paths=("hidden_tests/",),
    )

    with pytest.raises(PathPolicyError, match="forbidden"):
        policy.resolve("src/safe.py", access="write")
    with pytest.raises(PathPolicyError, match="Hidden evaluator"):
        policy.read_text("hidden_tests/test_secret.py")
    with pytest.raises(PathPolicyError, match="write allowlist"):
        policy.resolve("src_old/legacy.py", access="write")


@pytest.mark.skipif(os.path.normcase("A") != "a", reason="Windows case semantics")
def test_hidden_policy_cannot_be_bypassed_with_path_casing(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    policy = RepositoryPathPolicy(root, hidden_paths=("hidden_tests/",))

    with pytest.raises(PathPolicyError, match="Hidden evaluator"):
        policy.read_text("HIDDEN_TESTS/TEST_SECRET.PY")


def test_directory_policy_matching_is_segment_aware(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    policy = RepositoryPathPolicy(root, forbidden_paths=("src/",))

    with pytest.raises(PathPolicyError):
        policy.resolve("src/safe.py", access="write")
    assert policy.read_text("src/safe.py").splitlines() == ["SAFE = True"]
    assert policy.read_text("src_old/legacy.py").splitlines() == ["LEGACY = True"]


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    root, outside = _tree(tmp_path)
    link = root / "src" / "escape.py"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Symlink creation is unavailable on this Windows configuration: {error}")
    policy = RepositoryPathPolicy(root)

    with pytest.raises(PathPolicyError, match="escapes"):
        policy.read_text("src/escape.py")


def test_symlink_cannot_alias_hidden_path_inside_repository(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    link = root / "src" / "hidden_alias"
    try:
        link.symlink_to(root / "hidden_tests", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symlink creation is unavailable on this Windows configuration: {error}")
    policy = RepositoryPathPolicy(root, hidden_paths=("hidden_tests/",))

    with pytest.raises(PathPolicyError, match="Hidden evaluator"):
        policy.read_text("src/hidden_alias/test_secret.py")


def test_bounded_file_reads_and_tree_listing_hide_sensitive_paths(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    policy = RepositoryPathPolicy(
        root,
        hidden_paths=("hidden_tests/",),
        max_file_bytes=8,
        max_tree_entries=20,
    )

    with pytest.raises(PathPolicyError, match="read bound"):
        policy.read_text("src/safe.py")
    paths = {entry.path for entry in policy.list_tree()}
    assert ".git" not in paths
    assert "hidden_tests" not in paths
    assert "src/safe.py" in paths


def test_tree_entry_bound_is_enforced(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    policy = RepositoryPathPolicy(root, max_tree_entries=1)

    with pytest.raises(PathPolicyError, match="entry bound"):
        policy.list_tree()
