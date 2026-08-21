"""Typed configuration for local AgentTrace services."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.filesystem import paths_overlap, validate_runtime_root


class Settings(BaseSettings):
    """Application settings loaded from ``AGENTTRACE_`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTTRACE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AgentTrace"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    state_dir: Path = Path(".agenttrace")
    database_url: str | None = None
    workspace_root: Path | None = None
    artifact_root: Path | None = None
    verification_root: Path | None = None
    verification_image: str = "agentrace-verifier:phase6"
    docker_executable: str = "docker"
    verification_cpus: float = Field(default=1.0, ge=0.1, le=8.0)
    verification_memory_mb: int = Field(default=512, ge=64, le=8192)
    verification_pids: int = Field(default=128, ge=16, le=4096)
    verification_tmpfs_mb: int = Field(default=64, ge=8, le=1024)
    max_file_size_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)
    max_artifact_size_bytes: int = Field(default=16_777_216, ge=1, le=268_435_456)

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
        roots = {
            "workspace": workspace,
            "artifact": artifacts,
            "verification": verification,
        }
        pairs = (
            ("workspace", "artifact"),
            ("workspace", "verification"),
            ("artifact", "verification"),
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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide immutable-by-convention settings instance."""

    return Settings()
