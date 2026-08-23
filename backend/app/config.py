"""Typed configuration for local AgentTrace services."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.filesystem import paths_overlap, validate_runtime_root


class Settings(BaseSettings):
    """Application settings loaded from ``AGENTTRACE_`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTTRACE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "AgentTrace"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    state_dir: Path = Path(".agenttrace")
    database_url: str | None = None
    workspace_root: Path | None = None
    artifact_root: Path | None = None
    verification_root: Path | None = None
    external_repository_root: Path | None = None
    max_file_size_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    max_artifact_size_bytes: int = Field(default=16_777_216, ge=1, le=268_435_456)
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "AGENTTRACE_GEMINI_API_KEY"),
        repr=False,
    )

    @model_validator(mode="after")
    def runtime_roots_do_not_overlap(self) -> Settings:
        workspace = validate_runtime_root(
            self.effective_workspace_root, field_name="workspace_root"
        ).resolve(strict=False)
        artifacts = validate_runtime_root(
            self.effective_artifact_root, field_name="artifact_root"
        ).resolve(strict=False)
        verification = validate_runtime_root(
            self.effective_verification_root, field_name="verification_root"
        ).resolve(strict=False)
        external_repositories = validate_runtime_root(
            self.effective_external_repository_root,
            field_name="external_repository_root",
        ).resolve(strict=False)
        roots = {
            "workspace": workspace,
            "artifact": artifacts,
            "verification": verification,
            "external_repository": external_repositories,
        }
        pairs = (
            ("workspace", "artifact"),
            ("workspace", "verification"),
            ("artifact", "verification"),
            ("workspace", "external_repository"),
            ("artifact", "external_repository"),
            ("verification", "external_repository"),
        )
        for first, second in pairs:
            if paths_overlap(roots[first], roots[second]):
                raise ValueError(f"{first} and {second} roots must not overlap")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_database_url(self) -> str:
        if self.database_url is not None:
            return self.database_url
        return f"sqlite:///{(self.state_dir / 'agenttrace.sqlite3').as_posix()}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_workspace_root(self) -> Path:
        return self.workspace_root or self.state_dir / "workspaces"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_artifact_root(self) -> Path:
        return self.artifact_root or self.state_dir / "artifacts"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_verification_root(self) -> Path:
        return self.verification_root or self.state_dir / "verification"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_external_repository_root(self) -> Path:
        return self.external_repository_root or self.state_dir / "external_repositories"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable-by-convention settings instance."""

    return Settings()
