from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import Repository, Run, Task
from app.schemas import Run as RunSchema


def test_database_creation_includes_all_research_entities(tmp_path) -> None:
    database_path = tmp_path / "agenttrace.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")

    init_database(engine)

    assert set(inspect(engine).get_table_names()) == {
        "benchmark_quality",
        "counterexamples",
        "fault_localization_results",
        "patch_artifacts",
        "repositories",
        "runs",
        "tasks",
        "trace_events",
        "verification_results",
    }
    engine.dispose()


def test_init_database_adds_phase7_counterexample_columns_to_older_sqlite(
    tmp_path,
) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'older.sqlite3').as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE counterexamples ("
                "counterexample_id VARCHAR(100) PRIMARY KEY, "
                "run_id VARCHAR(100) NOT NULL, attempt_number INTEGER NOT NULL, "
                "source VARCHAR(100) NOT NULL, gate VARCHAR(100) NOT NULL, "
                "input_summary TEXT, expected_summary TEXT, observed_summary TEXT NOT NULL, "
                "location_hints JSON NOT NULL, is_new_vs_baseline BOOLEAN NOT NULL, "
                "sanitized_feedback TEXT NOT NULL)"
            )
        )

    init_database(engine)

    columns = {item["name"] for item in inspect(engine).get_columns("counterexamples")}
    assert {"failure_type", "log_excerpt"}.issubset(columns)
    engine.dispose()


def test_research_records_round_trip_through_sqlite(tmp_path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'records.sqlite3').as_posix()}")
    init_database(engine)
    sessions = make_session_factory(engine)

    with sessions.begin() as session:
        session.add(
            Repository(
                repository_id="repo-001",
                name="example",
                source="fixtures/example",
                base_commit="a" * 40,
                python_version="3.12",
                test_command="pytest -q",
            )
        )
        session.flush()
        session.add(
            Task(
                task_id="task-001",
                repository_id="repo-001",
                title="Fix empty input",
                description="Return an empty result for empty input.",
                task_category="bug_fix",
                difficulty="easy",
                allowed_paths=["src"],
                forbidden_paths=["hidden_tests"],
                visible_test_command="pytest -q tests",
                hidden_test_command="pytest -q hidden_tests",
            )
        )
        session.flush()
        session.add(
            Run(
                run_id="run-001",
                task_id="task-001",
                configuration_id="A",
                model="fake-model",
                status="created",
                started_at=datetime.now(UTC),
                failure_category=None,
            )
        )

    with sessions() as session:
        stored = session.scalar(select(Run).where(Run.run_id == "run-001"))

    assert stored is not None
    assert stored.model_parameters == {}
    assert stored.failure_category is None
    assert stored.started_at.tzinfo is UTC
    assert RunSchema.model_validate(stored).run_id == "run-001"

    with sessions() as session:
        session.add(
            Run(
                run_id="Run-Uppercase",
                task_id="task-001",
                configuration_id="A",
                model="fake-model",
                status="created",
                started_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError, match="CHECK constraint"):
            session.commit()

    with sessions() as session:
        session.add(
            Run(
                run_id="con",
                task_id="task-001",
                configuration_id="A",
                model="fake-model",
                status="created",
                started_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError, match="CHECK constraint"):
            session.commit()
    engine.dispose()


def test_sqlite_foreign_keys_are_enforced(tmp_path) -> None:
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'foreign-keys.sqlite3').as_posix()}")
    init_database(engine)
    sessions = make_session_factory(engine)

    with sessions() as session:
        session.add(
            Task(
                task_id="orphan-task",
                repository_id="missing-repository",
                title="Invalid",
                description="This task has no repository.",
                task_category="bug_fix",
                difficulty="easy",
                allowed_paths=["src"],
                forbidden_paths=["hidden_tests"],
                visible_test_command="pytest tests",
                hidden_test_command="pytest hidden_tests",
            )
        )
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            session.commit()
    engine.dispose()
