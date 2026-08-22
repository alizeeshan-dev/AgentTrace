"""FastAPI application factory for the local AgentTrace service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.db import create_database_engine, init_database, make_session_factory
from app.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the local application and initialize persistence at startup."""

    application_settings = settings or get_settings()
    configure_logging(application_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application_settings.state_dir.mkdir(parents=True, exist_ok=True)
        engine = create_database_engine(application_settings.effective_database_url)
        init_database(engine)
        application.state.engine = engine
        application.state.sessions = make_session_factory(engine)
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title=application_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if application_settings.environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = application_settings

    @application.get("/health", tags=["service"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": application_settings.app_name}

    return application


app = create_app()
