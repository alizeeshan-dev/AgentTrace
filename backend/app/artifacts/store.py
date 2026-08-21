"""Isolated, content-addressed storage for experiment artifacts."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from app.filesystem import validate_runtime_root
from app.repositories.identifiers import validate_safe_identifier

ArtifactKind = Literal[
    "logs",
    "patches",
    "coverage",
    "model",
    "verification",
    "mutation",
    "qualification",
    "other",
]
_KINDS = {
    "logs",
    "patches",
    "coverage",
    "model",
    "verification",
    "mutation",
    "qualification",
    "other",
}
_SUFFIX = re.compile(r"^\.[a-z0-9]{1,12}$")


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    relative_path: str
    sha256: str
    size_bytes: int
    kind: str


class ArtifactStore:
    """Store immutable bytes below per-run, per-kind directories."""

    def __init__(self, root: str | Path, *, max_artifact_bytes: int = 50 * 1024 * 1024) -> None:
        if max_artifact_bytes < 1:
            raise ValueError("max_artifact_bytes must be positive")
        candidate = validate_runtime_root(
            Path(root), field_name="artifact_root"
        )
        candidate.mkdir(parents=True, exist_ok=True, mode=0o700)
        validate_runtime_root(candidate, field_name="artifact_root")
        self.root = candidate.resolve(strict=True)
        if os.name != "nt":
            self.root.chmod(0o700)
        self.max_artifact_bytes = max_artifact_bytes

    def store_bytes(
        self,
        *,
        run_id: str,
        kind: ArtifactKind,
        data: bytes,
        suffix: str = ".bin",
    ) -> ArtifactReference:
        safe_run_id = validate_safe_identifier(run_id, field_name="run_id")
        if kind not in _KINDS:
            raise ValueError("Unsupported artifact kind")
        if not _SUFFIX.fullmatch(suffix):
            raise ValueError("Artifact suffix must be a short lowercase extension")
        if not isinstance(data, bytes):
            raise TypeError("Artifact data must be bytes")
        if len(data) > self.max_artifact_bytes:
            raise ValueError("Artifact exceeds the configured size bound")

        digest = hashlib.sha256(data).hexdigest()
        directory = self._safe_directory(safe_run_id, kind)
        destination = directory / f"{digest}{suffix}"
        if destination.exists():
            if destination.is_symlink():
                raise RuntimeError("Artifact destination cannot be a symlink")
            try:
                destination.resolve(strict=True).relative_to(self.root)
            except (OSError, RuntimeError, ValueError) as error:
                raise RuntimeError("Artifact destination escapes the store") from error
            if _sha256_file(destination) != digest:
                raise RuntimeError("Artifact path exists with unexpected content")
        else:
            temporary_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
                    temporary_name = temporary.name
                    temporary.write(data)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                Path(temporary_name).replace(destination)
            finally:
                if temporary_name is not None:
                    Path(temporary_name).unlink(missing_ok=True)

        relative = destination.relative_to(self.root).as_posix()
        return ArtifactReference(relative, digest, len(data), kind)

    def _safe_directory(self, run_id: str, kind: str) -> Path:
        current = self.root
        for segment in (run_id, kind):
            current = current / segment
            current.mkdir(exist_ok=True, mode=0o700)
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise RuntimeError("Artifact directories cannot be links or junctions")
            try:
                current.resolve(strict=True).relative_to(self.root)
            except (OSError, RuntimeError, ValueError) as error:
                raise RuntimeError("Artifact directory escapes the store") from error
            if not current.is_dir():
                raise RuntimeError("Artifact directory path is not a directory")
            if os.name != "nt":
                current.chmod(0o700)
        return current

    def store_text(
        self,
        *,
        run_id: str,
        kind: ArtifactKind,
        text: str,
        suffix: str = ".txt",
    ) -> ArtifactReference:
        return self.store_bytes(run_id=run_id, kind=kind, data=text.encode("utf-8"), suffix=suffix)

    def read_bytes(self, reference: ArtifactReference | str) -> bytes:
        relative = (
            reference.relative_path if isinstance(reference, ArtifactReference) else reference
        )
        path = self._resolve_reference(relative)
        with path.open("rb") as stream:
            data = stream.read(self.max_artifact_bytes + 1)
        if len(data) > self.max_artifact_bytes:
            raise ValueError("Artifact exceeds the configured size bound")
        if (
            isinstance(reference, ArtifactReference)
            and hashlib.sha256(data).hexdigest() != reference.sha256
        ):
            raise ValueError("Artifact content does not match its recorded hash")
        return data

    def _resolve_reference(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise ValueError("Artifact reference must be a POSIX-relative path")
        posix = PurePosixPath(relative)
        windows = PureWindowsPath(relative)
        if posix.is_absolute() or windows.is_absolute() or windows.drive:
            raise ValueError("Absolute artifact references are forbidden")
        if any(segment in {"", ".", ".."} for segment in posix.parts):
            raise ValueError("Artifact reference contains traversal")
        try:
            lexical = self.root
            for segment in posix.parts:
                lexical /= segment
                if lexical.is_symlink() or (
                    hasattr(lexical, "is_junction") and lexical.is_junction()
                ):
                    raise ValueError("Artifact references cannot traverse links or junctions")
            resolved = self.root.joinpath(*posix.parts).resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError("Artifact reference escapes or does not exist") from error
        if not resolved.is_file():
            raise ValueError("Artifact reference is not a file")
        return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
