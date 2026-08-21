from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.schemas import (
    BenchmarkQuality,
    Counterexample,
    FaultLocalizationResult,
    PatchArtifact,
    Repository,
    Run,
    Task,
    TraceEvent,
    VerificationResult,
)


def valid_task_data() -> dict[str, object]:
    return {
        "task_id": "parser-001",
        "repository_id": "repo-001",
        "title": "Handle empty input",
        "description": "The parser should return an empty collection for empty input.",
        "task_category": "bug_fix",
        "difficulty": "easy",
        "allowed_paths": ["src/parser.py"],
        "forbidden_paths": ["hidden_tests"],
        "visible_test_command": "pytest -q tests/test_parser.py",
        "hidden_test_command": "pytest -q hidden_tests/test_parser.py",
    }


def test_repository_requires_full_lowercase_commit_sha() -> None:
    repository = Repository(
        repository_id="repo-001",
        name="parser",
        source="fixtures/parser",
        base_commit="a" * 40,
        test_command="pytest -q",
    )

    assert repository.base_commit == "a" * 40

    with pytest.raises(ValidationError):
        Repository(
            repository_id="repo-001",
            name="parser",
            source="fixtures/parser",
            base_commit="main",
            test_command="pytest -q",
        )


@pytest.mark.parametrize(
    "unsafe_path",
    ["../secrets", "/etc/passwd", "C:/secrets", "src\\module.py", ".git/config"],
)
def test_task_rejects_unsafe_repository_paths(unsafe_path: str) -> None:
    data = valid_task_data()
    data["allowed_paths"] = [unsafe_path]

    with pytest.raises(ValidationError):
        Task.model_validate(data)


def test_task_rejects_duplicate_paths() -> None:
    data = valid_task_data()
    data["allowed_paths"] = ["src/parser.py", "src/parser.py"]

    with pytest.raises(ValidationError):
        Task.model_validate(data)


def test_task_accepts_normalized_directory_prefixes() -> None:
    data = valid_task_data()
    data["allowed_paths"] = ["src/"]
    data["forbidden_paths"] = ["hidden_tests/"]

    task = Task.model_validate(data)

    assert task.allowed_paths == ["src/"]
    assert task.forbidden_paths == ["hidden_tests/"]


def test_benchmark_quality_bounds_mutation_statistics() -> None:
    quality = BenchmarkQuality(
        task_id="parser-001", baseline_status="qualified", mutation_score=0.75
    )
    assert quality.mutants_killed == 0

    with pytest.raises(ValidationError):
        BenchmarkQuality(task_id="parser-001", baseline_status="qualified", mutation_score=1.01)


def test_run_keeps_failure_category_nullable_and_validates_time_order() -> None:
    started = datetime.now(UTC)
    run = Run(
        run_id="run-001",
        task_id="parser-001",
        configuration_id="C",
        model="fake-model",
        status="running",
        started_at=started,
        failure_category=None,
    )
    assert run.failure_category is None

    for unsafe_run_id in ("Run-Uppercase", "con", "nul.txt"):
        with pytest.raises(ValidationError):
            Run(
                run_id=unsafe_run_id,
                task_id="parser-001",
                configuration_id="C",
                model="fake-model",
                status="running",
                started_at=started,
            )

    with pytest.raises(ValidationError):
        Run(
            run_id="run-002",
            task_id="parser-001",
            configuration_id="C",
            model="fake-model",
            status="finished",
            started_at=started,
            finished_at=started - timedelta(seconds=1),
        )


def test_patch_artifact_enforces_one_repair_maximum() -> None:
    with pytest.raises(ValidationError):
        PatchArtifact(
            run_id="run-001",
            attempt_number=3,
            unified_diff="",
        )


def test_trace_event_requires_aware_timestamps() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            event_id="event-001",
            run_id="run-001",
            sequence_number=0,
            operation="prepare_workspace",
            started_at=datetime.now(),
            status="started",
        )


def test_verification_localization_and_counterexample_schemas() -> None:
    localization = FaultLocalizationResult(
        run_id="run-001",
        metric="ochiai",
        ranked_locations=[{"path": "src/parser.py", "line": 8, "score": 0.9}],
        top_k=5,
    )
    verification = VerificationResult(
        run_id="run-001",
        attempt_number=1,
        gate="visible_tests",
        required=True,
        status="failed",
        exit_code=1,
        duration_ms=25,
        summary="One test failed",
    )
    counterexample = Counterexample(
        counterexample_id="ce-001",
        run_id="run-001",
        attempt_number=1,
        source="PYTEST_FAILURE",
        gate="visible_tests",
        observed_summary="Expected [] but raised IndexError",
        location_hints=["src/parser.py:8"],
        is_new_vs_baseline=True,
        sanitized_feedback="The empty-input test raised IndexError.",
    )

    assert localization.ranked_locations[0]["line"] == 8
    assert verification.required is True
    assert counterexample.location_hints == ["src/parser.py:8"]
