"""Optional CrossHair/Z3 profile planning and bounded result normalization.

Symbolic analysis is an explicit, evaluator-selected counterexample search.  A
run that finds no counterexample is always represented as inconclusive, never
as proof that a candidate patch is correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

import yaml
from pydantic import Field, field_validator

from app.benchmark.loader import _UniqueKeyLoader
from app.repositories.identifiers import validate_safe_identifier
from app.schemas.common import FilesystemIdentifier, ResearchSchema, validate_repository_path

_TARGET = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
    r"(?::[A-Za-z_][A-Za-z0-9_]*)?$"
)
_CROSSHAIR_LINE = re.compile(
    r"^(?P<file>.+?\.py):(?P<line>[1-9][0-9]*):\s*"
    r"(?P<kind>error|info):\s*(?P<message>.+)$"
)


class SymbolicProfile(ResearchSchema):
    """Settings for one explicitly selected, contract-friendly target set."""

    schema_version: Literal[1] = 1
    profile_id: FilesystemIdentifier
    targets: Annotated[list[str], Field(min_length=1, max_length=20)]
    contract_kind: Literal["PEP316", "icontract", "deal"] = "PEP316"
    per_condition_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 5.0
    per_path_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 2.0
    max_iterations: Annotated[int, Field(ge=1, le=100_000)] = 1_000
    timeout_seconds: Annotated[int, Field(ge=1, le=900)] = 30

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(not _TARGET.fullmatch(value) for value in values):
            raise ValueError("symbolic targets must be unique module or module:symbol names")
        return values


@dataclass(frozen=True, slots=True)
class LoadedSymbolicProfile:
    profile: SymbolicProfile
    profile_path: Path


@dataclass(frozen=True, slots=True)
class SymbolicExecutionPlan:
    profile_id: str
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    timeout_seconds: int
    backend: Literal["CrossHair+Z3"] = "CrossHair+Z3"


SymbolicStatus = Literal["counterexample_found", "no_counterexample", "timeout", "error"]


@dataclass(frozen=True, slots=True)
class SymbolicCounterexampleEvidence:
    location_hint: str | None
    observed_summary: str


@dataclass(frozen=True, slots=True)
class SymbolicEvaluation:
    status: SymbolicStatus
    exit_code: int | None
    duration_ms: int
    summary: str
    counterexamples: tuple[SymbolicCounterexampleEvidence, ...]
    conclusion: Literal["counterexample", "inconclusive"]
    proves_correctness: Literal[False] = False


def load_configured_symbolic_profile(
    benchmark_root: str | Path,
    profile_id: str | None,
) -> LoadedSymbolicProfile | None:
    """Return no plan for ordinary tasks; load only an explicit task profile."""

    if profile_id is None:
        return None
    identifier = validate_safe_identifier(profile_id, field_name="symbolic_profile")
    root = _canonical_directory(benchmark_root)
    profile_path = root / "symbolic_profiles" / f"{identifier}.yaml"
    _reject_linked_components(profile_path, root / "symbolic_profiles")
    canonical = profile_path.resolve(strict=True)
    canonical.relative_to((root / "symbolic_profiles").resolve(strict=True))
    if not canonical.is_file() or _is_link_like(profile_path):
        raise ValueError("symbolic profile must be a regular non-link file")
    payload = yaml.load(canonical.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(payload, dict):
        raise ValueError("symbolic profile must contain one mapping")
    profile = SymbolicProfile.model_validate(payload)
    if profile.profile_id != identifier:
        raise ValueError("symbolic profile_id must match its filename and task reference")
    return LoadedSymbolicProfile(profile, canonical)


def build_symbolic_execution_plan(loaded: LoadedSymbolicProfile) -> SymbolicExecutionPlan:
    """Describe a no-shell CrossHair invocation for the Docker runner."""

    profile = loaded.profile
    return SymbolicExecutionPlan(
        profile_id=profile.profile_id,
        argv=(
            "python",
            "-m",
            "crosshair",
            "check",
            f"--analysis_kind={profile.contract_kind}",
            f"--per_condition_timeout={profile.per_condition_timeout_seconds:g}",
            f"--per_path_timeout={profile.per_path_timeout_seconds:g}",
            f"--max_iterations={profile.max_iterations}",
            *profile.targets,
        ),
        environment=(
            ("PYTHONHASHSEED", "0"),
            ("PYTHONDONTWRITEBYTECODE", "1"),
            ("PYTHONNOUSERSITE", "1"),
            ("PYTHONPATH", "/workspace"),
        ),
        timeout_seconds=profile.timeout_seconds,
    )


def normalize_symbolic_result(
    *,
    exit_code: int | None,
    duration_ms: int,
    timed_out: bool,
    stdout: str,
    stderr: str,
    max_output_chars: int = 100_000,
) -> SymbolicEvaluation:
    """Extract bounded CrossHair counterexamples, discarding raw tool output."""

    if duration_ms < 0 or max_output_chars < 1:
        raise ValueError("duration and output bounds must be valid")
    if timed_out:
        return SymbolicEvaluation(
            "timeout",
            None,
            duration_ms,
            "Symbolic counterexample search exceeded its hard timeout.",
            (),
            "inconclusive",
        )
    combined = f"{stdout}\n{stderr}"[:max_output_chars]
    evidence = tuple(
        item for line in combined.splitlines() if (item := _parse_crosshair_line(line)) is not None
    )
    if evidence:
        return SymbolicEvaluation(
            "counterexample_found",
            exit_code,
            duration_ms,
            f"CrossHair reported {len(evidence)} potential contract counterexample(s).",
            evidence,
            "counterexample",
        )
    if exit_code == 0:
        return SymbolicEvaluation(
            "no_counterexample",
            exit_code,
            duration_ms,
            "CrossHair found no counterexample within the configured bounds; this is not proof.",
            (),
            "inconclusive",
        )
    return SymbolicEvaluation(
        "error",
        exit_code,
        duration_ms,
        "Symbolic analysis did not complete with a usable result.",
        (),
        "inconclusive",
    )


def _parse_crosshair_line(line: str) -> SymbolicCounterexampleEvidence | None:
    match = _CROSSHAIR_LINE.match(line.strip())
    if match is None or match.group("kind") != "error":
        return None
    raw_file = match.group("file").replace("\\", "/")
    workspace_marker = "/workspace/"
    marker_index = raw_file.find(workspace_marker)
    if marker_index >= 0:
        relative = raw_file[marker_index + len(workspace_marker) :]
    elif raw_file.startswith("/evaluator/") or raw_file.startswith("/output/"):
        relative = ""
    else:
        relative = raw_file.removeprefix("./")
    location: str | None = None
    if relative:
        posix = PurePosixPath(relative)
        windows = PureWindowsPath(relative)
        try:
            normalized = validate_repository_path(relative)
        except ValueError:
            pass
        else:
            if not posix.is_absolute() and not windows.is_absolute() and not windows.drive:
                location = f"{normalized}:{match.group('line')}"
    message = match.group("message").strip()
    if len(message) > 2_000:
        message = f"{message[:2_000]}..."
    return SymbolicCounterexampleEvidence(location, message)


def _canonical_directory(path: str | Path) -> Path:
    supplied = Path(path)
    if _is_link_like(supplied):
        raise ValueError("benchmark root cannot be a link or junction")
    canonical = supplied.resolve(strict=True)
    if not canonical.is_dir():
        raise ValueError("benchmark root must be a directory")
    return canonical


def _reject_linked_components(candidate: Path, root: Path) -> None:
    canonical_root = root.resolve(strict=True)
    current = candidate
    while current != root:
        if _is_link_like(current):
            raise ValueError("symbolic profile cannot use links or junctions")
        if root not in current.parents:
            raise ValueError("symbolic profile escapes its evaluator root")
        current = current.parent
    if canonical_root != root.resolve(strict=True):
        raise ValueError("symbolic profile root is invalid")


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
