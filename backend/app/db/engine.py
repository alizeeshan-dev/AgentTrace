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
    """Upgrade databases created before Phase 7 without rewriting raw rows."""

    columns = {
        item["name"] for item in inspect(engine).get_columns("counterexamples")
    }
    additions = {
        "failure_type": "VARCHAR(200)",
        "log_excerpt": "TEXT",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(
                    text(f"ALTER TABLE counterexamples ADD COLUMN {name} {sql_type}")
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
