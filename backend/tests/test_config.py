from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_settings_derive_local_runtime_paths() -> None:
    settings = Settings(state_dir=Path("runtime"))

    assert settings.effective_database_url == "sqlite:///runtime/agenttrace.sqlite3"
    assert settings.effective_workspace_root == Path("runtime/workspaces")
    assert settings.effective_artifact_root == Path("runtime/artifacts")
    assert settings.effective_verification_root == Path("runtime/verification")


def test_environment_overrides_are_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTRACE_ENVIRONMENT", "test")
    monkeypatch.setenv("AGENTTRACE_MAX_FILE_SIZE_BYTES", "2048")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.environment == "test"
    assert settings.max_file_size_bytes == 2048
    get_settings.cache_clear()


def test_settings_reject_unsafe_unbounded_file_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(max_file_size_bytes=100_000_000)


def test_settings_reject_overlapping_runtime_roots(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(
            workspace_root=tmp_path / "runtime",
            artifact_root=tmp_path / "runtime" / "artifacts",
        )
    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(
            workspace_root=tmp_path / "workspaces",
            artifact_root=tmp_path / "artifacts",
            verification_root=tmp_path / "workspaces" / "verification",
        )


def test_settings_reject_windows_aliasing_root_segments(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="unsafe filesystem segment"):
        Settings(
            workspace_root=tmp_path / "run",
            artifact_root=tmp_path / "run." / "artifacts",
        )
