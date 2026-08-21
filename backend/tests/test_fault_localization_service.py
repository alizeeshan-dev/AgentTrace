from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.artifacts import ArtifactStore
from app.benchmark import load_benchmark_task
from app.config import Settings
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import FaultLocalizationResult, Repository, Run, Task
from app.fault_localization import FaultLocalizationService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"


def test_real_pilot_localization_is_persisted_without_hidden_source(
    tmp_path: Path,
) -> None:
    manifest = BENCHMARK_ROOT / "tasks" / "boundary-empty-input.yaml"
    loaded = load_benchmark_task(manifest, benchmark_root=BENCHMARK_ROOT)
    assert loaded.repository_path is not None
    settings = Settings(state_dir=tmp_path / "runtime")
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
    bundle_before = loaded.repository_path.read_bytes()

    with sessions.begin() as session:
        session.add(
            Repository(
                repository_id="repo-boundary",
                name="boundary-empty-input",
                source=str(loaded.repository_path),
                base_commit=loaded.task.base_commit,
                python_version=">=3.12",
                test_command=loaded.task.visible_test_command,
            )
        )
        session.add(
            Task(
                task_id=loaded.task.task_id,
                repository_id="repo-boundary",
                title=loaded.task.title,
                description=loaded.task.description,
                task_category=loaded.task.task_category,
                difficulty=loaded.task.difficulty,
                allowed_paths=loaded.task.allowed_paths,
                forbidden_paths=loaded.task.forbidden_paths,
                visible_test_command=loaded.task.visible_test_command,
                hidden_test_command=loaded.task.hidden_test_command,
                property_profile=loaded.task.property_profile,
                symbolic_profile=loaded.task.symbolic_profile,
                known_correct_patch="fixture-patch-ref",
            )
        )

    with sessions.begin() as session:
        localized = FaultLocalizationService(session, settings=settings).localize(
            manifest,
            benchmark_root=BENCHMARK_ROOT,
            run_id="sbfl-boundary-test",
            top_k=5,
        )

    assert localized.passing_tests == 3
    assert localized.failing_tests == 1
    assert localized.skipped_tests == 0
    assert localized.metrics.true_fault_rank == 1
    assert localized.metrics.top_1 is True
    assert localized.result.ranked_locations[0]["file"] == "ministats/summary.py"
    assert localized.result.ranked_locations[0]["line"] == 7
    assert localized.result.ranked_locations[0]["symbol"] == "mean"
    assert loaded.repository_path.read_bytes() == bundle_before
    assert not any(settings.effective_workspace_root.iterdir())

    artifacts = ArtifactStore(settings.effective_artifact_root)
    raw_bytes = artifacts.read_bytes(localized.coverage_artifact)
    raw_text = raw_bytes.decode("utf-8")
    raw = json.loads(raw_text)
    assert raw["hidden_test_identifiers"] == "opaque-sha256"
    assert any(test["test_id"].startswith("hidden-test-") for test in raw["tests"])
    assert "test_empty_input.py" not in raw_text
    assert "test_empty_input_has_no_mean" not in raw_text
    assert str(loaded.hidden_tests_path) not in raw_text

    with sessions() as session:
        stored_run = session.scalar(select(Run))
        stored_result = session.scalar(select(FaultLocalizationResult))
    assert stored_run is not None
    assert stored_run.model == "not-applicable"
    assert stored_run.model_parameters == {"llm_used": False, "phase": 4}
    assert stored_result is not None
    assert stored_result.metric == "ochiai"
    assert stored_result.fault_rank_if_known == 1
    assert stored_result.coverage_artifact == localized.coverage_artifact.relative_path
    engine.dispose()
