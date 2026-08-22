"""Separated immutable-raw and mutable-derived experiment namespaces."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.filesystem import validate_runtime_root
from app.repositories.identifiers import validate_safe_identifier
from app.schemas.common import validate_repository_path


@dataclass(frozen=True, slots=True)
class ExperimentDataLayout:
    root: Path
    raw: Path
    derived: Path

    @classmethod
    def create(
        cls,
        state_dir: str | Path,
        experiment_id: str,
        *,
        raw_location: str = "raw/",
        derived_location: str = "derived/",
    ) -> ExperimentDataLayout:
        safe_id = validate_safe_identifier(experiment_id, field_name="experiment_id")
        state = validate_runtime_root(Path(state_dir), field_name="state_dir")
        root = state / "experiments" / safe_id
        raw_relative = validate_repository_path(raw_location).rstrip("/")
        derived_relative = validate_repository_path(derived_location).rstrip("/")
        raw = root.joinpath(*raw_relative.split("/"))
        derived = root.joinpath(*derived_relative.split("/"))
        if raw == derived or raw in derived.parents or derived in raw.parents:
            raise ValueError("raw and derived experiment locations must be disjoint")
        raw.mkdir(parents=True, exist_ok=True)
        derived.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()
        resolved_raw = raw.resolve()
        resolved_derived = derived.resolve()
        resolved_raw.relative_to(resolved_root)
        resolved_derived.relative_to(resolved_root)
        return cls(resolved_root, resolved_raw, resolved_derived)

    def write_raw_once(self, name: str, payload: dict[str, Any]) -> Path:
        """Create one canonical raw JSON record and refuse every overwrite."""

        safe_name = validate_safe_identifier(name, field_name="raw record name")
        destination = self.raw / f"{safe_name}.json"
        data = _json_bytes(payload)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def write_derived(self, name: str, payload: dict[str, Any]) -> Path:
        """Write a replaceable analysis record only in the derived namespace."""

        safe_name = validate_safe_identifier(name, field_name="derived record name")
        destination = self.derived / f"{safe_name}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_bytes(_json_bytes(payload))
        temporary.replace(destination)
        return destination

    def existing_raw(self, name: str) -> tuple[Path, str] | None:
        """Return a validated existing raw record and its content digest."""

        safe_name = validate_safe_identifier(name, field_name="raw record name")
        path = self.raw / f"{safe_name}.json"
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("raw experiment record is not a regular file")
        resolved = path.resolve(strict=True)
        resolved.relative_to(self.raw)
        data = resolved.read_bytes()
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise RuntimeError("raw experiment record must contain one JSON object")
        return resolved, hashlib.sha256(data).hexdigest()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
