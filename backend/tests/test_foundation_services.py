from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import Repository, Task
from app.repositories.path_policy import PathPolicyError
from app.repositories.workspace import WorkspaceManager
from app.services import RepositoryRegistry, TaskWorkspaceLoader


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


def _source_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.name", "AgentTrace Tests")
    _git(source, "config", "user.email", "tests@example.invalid")
    (source / "src").mkdir()
    (source / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "hidden_tests").mkdir()
    (source / "hidden_tests" / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "base")
    return source, _git(source, "rev-parse", "HEAD")


def _database(tmp_path: Path) -> tuple[sessionmaker[Session], Engine]:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}")
    init_database(engine)
    return make_session_factory(engine), engine


def test_registration_is_persisted_and_idempotent(tmp_path: Path) -> None:
    source, commit = _source_repository(tmp_path)
    sessions, engine = _database(tmp_path)
    settings = Settings(state_dir=tmp_path / "runtime")

    with sessions.begin() as session:
        registry = RepositoryRegistry(session, settings=settings)
        first = registry.register_local(source, test_command="pytest -q")
        second = registry.register_local(source, test_command="pytest -q")
        assert first is second

    with sessions() as session:
        stored = session.scalar(select(Repository))

    assert stored is not None
    assert stored.base_commit == commit
    assert Path(stored.source) == source.resolve()
    engine.dispose()


def test_registration_rejects_runtime_storage_overlap(tmp_path: Path) -> None:
    source, _ = _source_repository(tmp_path)
    sessions, engine = _database(tmp_path)
    settings = Settings(
        workspace_root=source / "workspaces",
        artifact_root=tmp_path / "artifacts",
    )

    with sessions() as session:
        registry = RepositoryRegistry(session, settings=settings)
        with pytest.raises(ValueError, match="must not overlap"):
            registry.register_local(source, test_command="pytest")
    engine.dispose()


def test_task_loads_at_recorded_commit_with_hidden_tests_inaccessible(tmp_path: Path) -> None:
    source, commit = _source_repository(tmp_path)
    original_status = _git(source, "status", "--porcelain")
    sessions, engine = _database(tmp_path)
    settings = Settings(state_dir=tmp_path / "runtime")

    with sessions.begin() as session:
        repository = RepositoryRegistry(session, settings=settings).register_local(
            source, test_command="pytest -q"
        )
        session.add(
            Task(
                task_id="task-001",
                repository_id=repository.repository_id,
                title="Fix module",
                description="Correct the module behavior.",
                task_category="bug_fix",
                difficulty="easy",
                allowed_paths=["src/"],
                forbidden_paths=["hidden_tests/"],
                visible_test_command="pytest -q tests",
                hidden_test_command="pytest -q hidden_tests",
            )
        )

    with sessions() as session:
        manager = WorkspaceManager(settings.effective_workspace_root)
        loaded = TaskWorkspaceLoader(
            session,
            manager,
            max_file_bytes=1_024,
        ).load(
            task_id="task-001",
            run_id="run-001",
            hidden_paths=("hidden_tests/",),
        )

        assert _git(loaded.workspace.path, "rev-parse", "HEAD") == commit
        assert loaded.paths.read_text("src/module.py").splitlines() == ["VALUE = 1"]
        with pytest.raises(PathPolicyError, match="Hidden evaluator"):
            loaded.paths.read_text("hidden_tests/secret.py")
        manager.remove(loaded.workspace)

    assert _git(source, "status", "--porcelain") == original_status
    assert (source / "src" / "module.py").read_text(encoding="utf-8").splitlines() == ["VALUE = 1"]
    engine.dispose()
