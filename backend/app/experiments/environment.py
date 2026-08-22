"""Immutable Windows experiment-environment manifests.

The manifest identifies the native toolchain used by verification.  It is a
reproducibility record, not a claim that Windows subprocesses provide a
container-grade security boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.schemas.common import ResearchSchema

_DISTRIBUTIONS = {
    "pytest": "pytest",
    "hypothesis": "hypothesis",
    "coverage": "coverage",
    "pytest_cov": "pytest-cov",
    "pytest_gremlins": "pytest-gremlins",
    "ruff": "ruff",
    "mypy": "mypy",
    "bandit": "bandit",
    "crosshair": "crosshair-tool",
    "z3": "z3-solver",
}


class WindowsToolVersions(ResearchSchema):
    """Verification and research tools bound into the environment identity."""

    pytest: str
    hypothesis: str
    coverage: str
    pytest_cov: str
    pytest_gremlins: str
    ruff: str
    mypy: str
    bandit: str
    crosshair: str | None = None
    z3: str | None = None


class WindowsEnvironmentManifest(ResearchSchema):
    """Frozen identity for the native Windows verification environment."""

    schema_version: Literal[1] = 1
    runner: Literal["native_windows"] = "native_windows"
    operating_system: Literal["Windows"] = "Windows"
    windows_version: str
    windows_release: str
    windows_build: str
    machine: str
    python_version: str
    python_implementation: str
    python_executable_name: str
    tools: WindowsToolVersions
    dependency_lock_path: str
    dependency_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agenttrace_source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    benchmark_version: str
    verification_profile: str
    environment_id: str
    environment_fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def fingerprint_payload(self) -> dict[str, object]:
        """Return the canonical fields that determine the fingerprint."""

        return self.model_dump(
            mode="json",
            exclude={"environment_id", "environment_fingerprint_sha256"},
        )


class EnvironmentManifestError(RuntimeError):
    """The active process cannot produce a valid frozen Windows manifest."""


def build_windows_environment_manifest(
    *,
    repository_root: str | Path,
    dependency_lock: str | Path,
    benchmark_version: str,
    verification_profile: str,
    require_clean_source: bool = True,
) -> WindowsEnvironmentManifest:
    """Capture installed native verification versions and derive a stable hash."""

    if platform.system() != "Windows":
        raise EnvironmentManifestError("native experiment manifests require Windows")
    root = Path(repository_root).resolve(strict=True)
    lock = Path(dependency_lock).resolve(strict=True)
    if not lock.is_file():
        raise EnvironmentManifestError("dependency lock must be a regular file")
    commit = _git(root, "rev-parse", "HEAD")
    if require_clean_source and _git(root, "status", "--porcelain"):
        raise EnvironmentManifestError("source worktree must be clean before environment freeze")

    values: dict[str, str | None] = {}
    for field_name, distribution in _DISTRIBUTIONS.items():
        try:
            values[field_name] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            values[field_name] = None
    required = {
        name for name in _DISTRIBUTIONS if name not in {"crosshair", "z3"}
    }
    missing = sorted(name for name in required if values[name] is None)
    if missing:
        raise EnvironmentManifestError(
            "required verification tools are not installed: " + ", ".join(missing)
        )

    windows_version = platform.win32_ver()
    provisional = WindowsEnvironmentManifest(
        windows_version=platform.version(),
        windows_release=windows_version[0] or platform.release(),
        windows_build=windows_version[1] or platform.version(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        python_implementation=platform.python_implementation(),
        python_executable_name=Path(sys.executable).name,
        tools=WindowsToolVersions.model_validate(values),
        dependency_lock_path=lock.relative_to(root).as_posix(),
        dependency_lock_sha256=_sha256_file(lock),
        agenttrace_source_commit=commit,
        benchmark_version=benchmark_version,
        verification_profile=verification_profile,
        environment_id="pending",
        environment_fingerprint_sha256="0" * 64,
    )
    fingerprint = _sha256_json(provisional.fingerprint_payload())
    return provisional.model_copy(
        update={
            "environment_id": f"windows-{fingerprint[:16]}",
            "environment_fingerprint_sha256": fingerprint,
        }
    )


def write_windows_environment_manifest(
    manifest: WindowsEnvironmentManifest,
    destination: str | Path,
) -> Path:
    """Write one manifest exactly once; frozen records are never overwritten."""

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        payload = manifest.model_dump_json(indent=2).encode("utf-8") + b"\n"
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return path


def load_windows_environment_manifest(path: str | Path) -> WindowsEnvironmentManifest:
    """Load a regular immutable manifest and verify its embedded fingerprint."""

    supplied = Path(path)
    if supplied.is_symlink():
        raise EnvironmentManifestError("environment manifest cannot be a symlink")
    resolved = supplied.resolve(strict=True)
    if not resolved.is_file():
        raise EnvironmentManifestError("environment manifest must be a regular file")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        manifest = WindowsEnvironmentManifest.model_validate(payload)
    except (OSError, ValueError) as error:
        raise EnvironmentManifestError("environment manifest is invalid") from error
    if not verify_environment_fingerprint(manifest):
        raise EnvironmentManifestError("environment manifest fingerprint does not match")
    return manifest


def verify_environment_fingerprint(manifest: WindowsEnvironmentManifest) -> bool:
    """Verify that a loaded manifest's canonical content matches its fingerprint."""

    return _sha256_json(manifest.fingerprint_payload()) == manifest.environment_fingerprint_sha256


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=30,
        env={
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            "TEMP": os.environ.get("TEMP", str(root)),
            "TMP": os.environ.get("TMP", str(root)),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    if completed.returncode != 0:
        raise EnvironmentManifestError("unable to read the AgentTrace Git identity")
    return completed.stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_json(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
