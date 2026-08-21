"""Evaluator-owned Hypothesis profiles and container result normalization.

This module never imports or executes benchmark repository code.  It validates
the evaluator's property profile, describes the files and command that a Docker
runner must use, and consumes a small structured sidecar after execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import yaml
from pydantic import Field, JsonValue, StringConstraints, field_validator

from app.benchmark.loader import _UniqueKeyLoader
from app.repositories.identifiers import validate_safe_identifier
from app.schemas.common import FilesystemIdentifier, ResearchSchema, validate_repository_path

_MAX_SIDECAR_BYTES = 32 * 1024
_ISOLATED_PYTEST_LAUNCHER = (
    "import sys; import pytest; "
    "sys.path[:0] = ['/workspace', '/evaluator/runtime']; "
    "raise SystemExit(pytest.main())"
)
_MAX_COUNTEREXAMPLE_BYTES = 16 * 1024
_SUMMARY_CHARS = 2_000
_PROPERTY_RESULT_PATH = PurePosixPath("/output/property-counterexamples.json")


class PropertyProfile(ResearchSchema):
    """Bounded deterministic settings for one evaluator-owned property test."""

    schema_version: Literal[1] = 1
    profile_id: FilesystemIdentifier
    test_file: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    max_examples: Annotated[int, Field(ge=1, le=1_000)] = 50
    deadline_ms: Annotated[int, Field(ge=1, le=60_000)] | None = 1_000
    timeout_seconds: Annotated[int, Field(ge=1, le=900)] = 30

    @field_validator("test_file")
    @classmethod
    def validate_test_file(cls, value: str) -> str:
        normalized = validate_repository_path(value)
        path = PurePosixPath(normalized)
        if path.suffix != ".py" or path.parts[:1] != ("property_tests",):
            raise ValueError("test_file must be a Python file below property_tests/")
        return normalized


@dataclass(frozen=True, slots=True)
class LoadedPropertyProfile:
    """Validated profile plus its private evaluator source location."""

    profile: PropertyProfile
    profile_path: Path
    test_path: Path


@dataclass(frozen=True, slots=True)
class EvaluatorFileMount:
    """One evaluator file to mount read-only in the verification container."""

    source_path: Path
    container_path: PurePosixPath
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class GeneratedContainerFile:
    """Evaluator runtime content staged by the Docker verifier, never the agent."""

    container_path: PurePosixPath
    content: bytes


@dataclass(frozen=True, slots=True)
class PropertyExecutionPlan:
    """Complete container-only plan for a bounded Hypothesis gate."""

    profile_id: str
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    evaluator_mounts: tuple[EvaluatorFileMount, ...]
    generated_files: tuple[GeneratedContainerFile, ...]
    result_path: PurePosixPath
    timeout_seconds: int


class PropertyCounterexamplePayload(ResearchSchema):
    """Private JSON protocol emitted by the in-container evaluator plugin."""

    input: JsonValue
    expected: JsonValue | None = None
    observed: JsonValue
    exception_type: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=100,
            pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$",
        ),
    ] = "AssertionError"
    location_hints: Annotated[list[str], Field(default_factory=list, max_length=10)]
    shrunk: Literal[True] = True

    @field_validator("location_hints")
    @classmethod
    def validate_location_hints(cls, values: list[str]) -> list[str]:
        for value in values:
            path, separator, line = value.rpartition(":")
            if not separator or not line.isdigit() or int(line) < 1:
                raise ValueError("location hints must use repository/path.py:line syntax")
            validate_repository_path(path)
        return values


class PropertyResultEnvelope(ResearchSchema):
    schema_version: Literal[1] = 1
    counterexamples: Annotated[
        list[PropertyCounterexamplePayload], Field(default_factory=list, max_length=10)
    ]


@dataclass(frozen=True, slots=True)
class PropertyCounterexampleEvidence:
    """Bounded evidence safe to persist and later transform for Phase 7."""

    input_summary: str
    expected_summary: str | None
    observed_summary: str
    exception_type: str
    location_hints: tuple[str, ...]
    shrunk: bool


PropertyStatus = Literal["passed", "failed", "timeout", "error"]


@dataclass(frozen=True, slots=True)
class PropertyEvaluation:
    status: PropertyStatus
    exit_code: int | None
    duration_ms: int
    summary: str
    counterexamples: tuple[PropertyCounterexampleEvidence, ...]


def load_property_profile(
    benchmark_root: str | Path,
    profile_id: str,
    *,
    repository_path: str | Path | None = None,
) -> LoadedPropertyProfile:
    """Load ``property_profiles/<id>.yaml`` and its external test source.

    ``repository_path`` is optional because bundle-backed tasks do not have an
    unpacked repository at load time.  When supplied, physical overlap with the
    evaluator source is rejected.
    """

    identifier = validate_safe_identifier(profile_id, field_name="property_profile")
    root = _canonical_directory(benchmark_root, label="benchmark root")
    profile_path = _regular_file_below(
        root / "property_profiles" / f"{identifier}.yaml",
        root=root,
        label="property profile",
    )
    payload = yaml.load(profile_path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    if not isinstance(payload, dict):
        raise ValueError("property profile must contain one mapping")
    profile = PropertyProfile.model_validate(payload)
    if profile.profile_id != identifier:
        raise ValueError("property profile_id must match its filename and task reference")
    test_path = _regular_file_below(
        root.joinpath(*PurePosixPath(profile.test_file).parts),
        root=root / "property_tests",
        label="property test",
    )
    if repository_path is not None:
        repository = _canonical_directory(repository_path, label="repository")
        if _paths_overlap(repository, test_path):
            raise ValueError("property tests must be physically outside the agent repository")
    return LoadedPropertyProfile(profile, profile_path, test_path)


def build_property_execution_plan(loaded: LoadedPropertyProfile) -> PropertyExecutionPlan:
    """Build a Docker-runner plan with deterministic, database-free Hypothesis settings."""

    profile = loaded.profile
    container_test = PurePosixPath(
        "/evaluator/property-tests", profile.profile_id, loaded.test_path.name
    )
    runtime_path = PurePosixPath("/evaluator/runtime")
    environment = (
        ("AGENTTRACE_COUNTEREXAMPLE_FILE", str(_PROPERTY_RESULT_PATH)),
        ("HYPOTHESIS_PROFILE", "agentrace"),
        ("PYTHONHASHSEED", "0"),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PYTHONNOUSERSITE", "1"),
    )
    return PropertyExecutionPlan(
        profile_id=profile.profile_id,
        argv=(
            "python",
            "-I",
            "-c",
            _ISOLATED_PYTEST_LAUNCHER,
            "-q",
            "-p",
            "agentrace_property_plugin",
            "-p",
            "no:cacheprovider",
            "--tb=short",
            "--disable-warnings",
            "--junitxml=/output/property-tests.xml",
            str(container_test),
        ),
        environment=environment,
        evaluator_mounts=(EvaluatorFileMount(loaded.test_path, container_test),),
        generated_files=(
            GeneratedContainerFile(
                runtime_path / "agentrace_property_runtime.py",
                _runtime_module().encode("utf-8"),
            ),
            GeneratedContainerFile(
                runtime_path / "agentrace_property_plugin.py",
                _plugin_module(profile).encode("utf-8"),
            ),
        ),
        result_path=_PROPERTY_RESULT_PATH,
        timeout_seconds=profile.timeout_seconds,
    )


def normalize_property_result(
    *,
    exit_code: int | None,
    duration_ms: int,
    timed_out: bool,
    sidecar: bytes | None,
) -> PropertyEvaluation:
    """Normalize a container result without retaining raw hidden-test output."""

    if duration_ms < 0:
        raise ValueError("duration_ms cannot be negative")
    if timed_out:
        return PropertyEvaluation(
            "timeout",
            None,
            duration_ms,
            "Hypothesis property execution exceeded its hard timeout.",
            (),
        )
    counterexamples = _load_counterexamples(sidecar)
    evidence = tuple(_to_evidence(item) for item in counterexamples)
    if exit_code == 0:
        if evidence:
            return PropertyEvaluation(
                "error",
                exit_code,
                duration_ms,
                "Property runner reported counterexamples despite a successful exit.",
                evidence,
            )
        return PropertyEvaluation(
            "passed",
            exit_code,
            duration_ms,
            "Bounded Hypothesis properties found no failing example.",
            (),
        )
    if exit_code == 1:
        summary = (
            f"Hypothesis found {len(evidence)} shrunk counterexample(s)."
            if evidence
            else "A property failed without structured counterexample evidence."
        )
        return PropertyEvaluation("failed", exit_code, duration_ms, summary, evidence)
    return PropertyEvaluation(
        "error",
        exit_code,
        duration_ms,
        "Hypothesis property execution did not complete as a valid pass/fail run.",
        evidence,
    )


def _load_counterexamples(sidecar: bytes | None) -> list[PropertyCounterexamplePayload]:
    if sidecar is None or not sidecar:
        return []
    if len(sidecar) > _MAX_SIDECAR_BYTES:
        raise ValueError("property counterexample sidecar exceeds its size bound")
    try:
        payload = json.loads(sidecar.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("property counterexample sidecar is invalid JSON") from error
    envelope = PropertyResultEnvelope.model_validate(payload)
    for counterexample in envelope.counterexamples:
        encoded = json.dumps(
            counterexample.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        if len(encoded) > _MAX_COUNTEREXAMPLE_BYTES:
            raise ValueError("property counterexample exceeds its size bound")
    return envelope.counterexamples


def _to_evidence(item: PropertyCounterexamplePayload) -> PropertyCounterexampleEvidence:
    return PropertyCounterexampleEvidence(
        input_summary=_json_summary(item.input),
        expected_summary=None if item.expected is None else _json_summary(item.expected),
        observed_summary=_json_summary(item.observed),
        exception_type=item.exception_type,
        location_hints=tuple(item.location_hints),
        shrunk=item.shrunk,
    )


def _json_summary(value: JsonValue) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return rendered if len(rendered) <= _SUMMARY_CHARS else f"{rendered[:_SUMMARY_CHARS]}..."


def _runtime_module() -> str:
    return '''"""Private helper for evaluator-owned AgentTrace property tests."""
from __future__ import annotations

from typing import NoReturn


class PropertyCounterexample(AssertionError):
    def __init__(self, *, input_value, expected, observed, exception_type, location_hints=()):
        super().__init__("AgentTrace property counterexample")
        self.input_value = input_value
        self.expected = expected
        self.observed = observed
        self.exception_type = exception_type
        self.location_hints = tuple(location_hints)


def fail(
    *, input_value, expected, observed, exception_type="AssertionError", location_hints=()
) -> NoReturn:
    raise PropertyCounterexample(
        input_value=input_value,
        expected=expected,
        observed=observed,
        exception_type=exception_type,
        location_hints=location_hints,
    )
'''


def _plugin_module(profile: PropertyProfile) -> str:
    deadline = "None" if profile.deadline_ms is None else str(profile.deadline_ms)
    return f'''"""Generated deterministic Hypothesis profile and sidecar writer."""
from __future__ import annotations

import json
import os
from pathlib import Path

from hypothesis import Phase, settings
from agentrace_property_runtime import PropertyCounterexample

settings.register_profile(
    "agentrace",
    max_examples={profile.max_examples},
    deadline={deadline},
    derandomize=True,
    database=None,
    phases=(Phase.generate, Phase.shrink),
    report_multiple_bugs=False,
    print_blob=True,
)
settings.load_profile("agentrace")


def pytest_runtest_makereport(item, call):
    del item
    if call.when != "call" or call.excinfo is None:
        return
    error = call.excinfo.value
    if not isinstance(error, PropertyCounterexample):
        return
    payload = {{
        "schema_version": 1,
        "counterexamples": [{{
            "input": error.input_value,
            "expected": error.expected,
            "observed": error.observed,
            "exception_type": error.exception_type,
            "location_hints": list(error.location_hints),
            "shrunk": True,
        }}],
    }}
    destination = Path(os.environ["AGENTTRACE_COUNTEREXAMPLE_FILE"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(destination)
'''


def _canonical_directory(path: str | Path, *, label: str) -> Path:
    supplied = Path(path)
    if _is_link_like(supplied):
        raise ValueError(f"{label} cannot be a link or junction")
    canonical = supplied.resolve(strict=True)
    if not canonical.is_dir():
        raise ValueError(f"{label} must be a directory")
    return canonical


def _regular_file_below(candidate: Path, *, root: Path, label: str) -> Path:
    canonical_root = _canonical_directory(root, label=f"{label} root")
    _reject_linked_components(candidate, canonical_root, label=label)
    canonical = candidate.resolve(strict=True)
    try:
        canonical.relative_to(canonical_root)
    except ValueError as error:
        raise ValueError(f"{label} escapes its evaluator root") from error
    if not canonical.is_file() or _is_link_like(candidate):
        raise ValueError(f"{label} must be a regular non-link file")
    return canonical


def _reject_linked_components(candidate: Path, root: Path, *, label: str) -> None:
    current = candidate
    while current != root:
        if _is_link_like(current):
            raise ValueError(f"{label} cannot use links or junctions")
        if root not in current.parents:
            raise ValueError(f"{label} escapes its evaluator root")
        current = current.parent


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
