from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.repositories.registration import register_local_repository


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "sample"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "AgentTrace Tests")
    _git(repository, "config", "user.email", "tests@example.invalid")
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "pyproject.toml", "module.py")
    _git(repository, "commit", "-m", "base")
    return repository


def test_registers_local_repository_at_immutable_commit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    registration = register_local_repository(repository, test_command="pytest -q")

    assert registration.name == "sample"
    assert registration.source_path == repository.resolve()
    assert registration.base_commit == _git(repository, "rev-parse", "HEAD")
    assert registration.python_version == ">=3.12"
    assert registration.test_command == "pytest -q"
    assert registration.identity.startswith("local-")
    assert len(registration.identity) == len("local-") + 64


def test_registration_identity_is_stable_for_the_same_path(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    first = register_local_repository(repository, test_command="pytest")
    second = register_local_repository(repository / ".", test_command="pytest")

    assert first.identity == second.identity


def test_registration_identity_distinguishes_base_commits(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = register_local_repository(repository, test_command="pytest")
    (repository / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repository, "add", "module.py")
    _git(repository, "commit", "-m", "second")

    second = register_local_repository(repository, test_command="pytest")

    assert first.base_commit != second.base_commit
    assert first.identity != second.identity


def test_registration_rejects_subdirectory_and_invalid_test_command(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    package = repository / "package"
    package.mkdir()

    with pytest.raises(ValueError, match="work-tree root"):
        register_local_repository(package, test_command="pytest")
    with pytest.raises(ValueError, match="single non-empty line"):
        register_local_repository(repository, test_command="pytest\nmalicious")


def test_python_detection_does_not_follow_metadata_symlink(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "pyproject.toml").unlink()
    outside = tmp_path / "outside.toml"
    outside.write_text(
        '[project]\nname = "outside"\nversion = "1"\nrequires-python = "3.99"\n',
        encoding="utf-8",
    )
    try:
        (repository / "pyproject.toml").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Symlink creation is unavailable on this Windows configuration: {error}")

    registration = register_local_repository(repository, test_command="pytest")

    assert registration.python_version is None
