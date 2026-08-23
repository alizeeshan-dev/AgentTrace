"""Explicit SQLAlchemy engine and session construction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base


def create_database_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create an engine with SQLite's foreign-key checks enabled."""

    is_sqlite = database_url.startswith("sqlite:")
    if database_url in {"sqlite://", "sqlite:///:memory:"}:
        engine = create_engine(
            database_url,
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    elif is_sqlite:
        engine = create_engine(
            database_url,
            echo=echo,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_engine(database_url, echo=echo)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a non-global session factory suitable for tests and services."""

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_database(engine: Engine) -> None:
    """Create the schema and apply the small additive compatibility upgrades."""

    from app.db import models  # noqa: F401

    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _upgrade_sqlite_schema(engine)


def _upgrade_sqlite_schema(engine: Engine) -> None:
    """Apply additive local-schema upgrades without rewriting research rows."""

    counterexample_columns = {
        item["name"] for item in inspect(engine).get_columns("counterexamples")
    }
    counterexample_additions = {
        "failure_type": "VARCHAR(200)",
        "log_excerpt": "TEXT",
    }
    repository_columns = {
        item["name"] for item in inspect(engine).get_columns("repositories")
    }
    repository_additions = {
        "source_type": "VARCHAR(30) NOT NULL DEFAULT 'local'",
        "repository_url": "TEXT",
        "default_branch": "VARCHAR(300)",
        "primary_language": "VARCHAR(50)",
        "registered_at": "DATETIME",
        "managed_source": "TEXT",
        "trusted_for_local_execution": "BOOLEAN NOT NULL DEFAULT 0",
        "trust_confirmed_at": "DATETIME",
        "repository_metadata": "JSON NOT NULL DEFAULT '{}'",
    }
    task_columns = {item["name"] for item in inspect(engine).get_columns("tasks")}
    task_additions = {
        "task_source": "VARCHAR(30) NOT NULL DEFAULT 'benchmark'",
        "verification_configured": "BOOLEAN NOT NULL DEFAULT 1",
        "definition_path": "TEXT",
        "created_at": "DATETIME",
    }
    with engine.begin() as connection:
        for name, sql_type in counterexample_additions.items():
            if name not in counterexample_columns:
                connection.execute(
                    text(f"ALTER TABLE counterexamples ADD COLUMN {name} {sql_type}")
                )
        for name, sql_type in repository_additions.items():
            if name not in repository_columns:
                connection.execute(
                    text(f"ALTER TABLE repositories ADD COLUMN {name} {sql_type}")
                )
        for name, sql_type in task_additions.items():
            if name not in task_columns:
                connection.execute(text(f"ALTER TABLE tasks ADD COLUMN {name} {sql_type}"))
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_repositories_repository_url "
                "ON repositories (repository_url)"
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_tasks_task_source ON tasks (task_source)")
        )


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a transaction and roll it back if the caller raises."""

    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
