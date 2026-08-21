"""FastAPI application factory for the local AgentTrace service."""

from __future__ import annotations

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application instance without mutating persistent state."""

    application_settings = settings or get_settings()
    configure_logging(application_settings.log_level)
    application = FastAPI(
        title=application_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if application_settings.environment != "production" else None,
        redoc_url=None,
    )
    application.state.settings = application_settings

    @application.get("/health", tags=["service"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": application_settings.app_name}

    return application


app = create_app()
