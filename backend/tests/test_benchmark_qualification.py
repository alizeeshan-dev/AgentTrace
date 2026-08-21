from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.artifacts import ArtifactStore
from app.benchmark import BenchmarkQualificationService
from app.config import Settings
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import BenchmarkQuality
from app.mutation import MutationCounts, MutationExecution, MutmutConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"


class _SuccessfulMutationRunner:
    def run(
        self,
        workspace: str | Path,
        config: MutmutConfig,
        *,
        manual_exclusions: Mapping[str, str] | None = None,
    ) -> MutationExecution:
        root = Path(workspace)
        assert (root / ".agenttrace-evaluator" / "hidden_tests" / "test_empty_input.py").is_file()
        assert config.source_paths == ("ministats/summary.py",)
        assert manual_exclusions == {}
        timestamp = datetime.now(UTC)
        return MutationExecution(
            counts=MutationCounts(
                generated=5,
                killed=4,
                survived=1,
                excluded=0,
                skipped=0,
                invalid=0,
                unusable=0,
                mutation_score=0.8,
                completed=True,
                status_counts={"killed": 4, "survived": 1},
                exclusion_reasons={},
            ),
            tool="mutmut",
            tool_version="3.7.0",
            commands=(("mutmut", "run"),),
            config_sha256="a" * 64,
            started_at=timestamp,
            finished_at=timestamp,
            duration_ms=25,
            platform="linux-test",
            python_version="3.12.0",
            run_stdout="mutation run complete",
            run_stderr="",
            export_stdout="stats exported",
            export_stderr="",
            results_output="four killed; one survived",
            raw_stats_json='{"killed":4,"survived":1,"total":5}',
        )


def test_qualification_persists_quality_and_content_addressed_artifacts(
    tmp_path: Path,
) -> None:
    settings = Settings(state_dir=tmp_path / "runtime")
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
    bundle = BENCHMARK_ROOT / "repositories" / "boundary-empty-input.bundle"
    bundle_hash_before = bundle.read_bytes()

    with sessions.begin() as session:
        result = BenchmarkQualificationService(
            session,
            settings=settings,
            mutation_runner=_SuccessfulMutationRunner(),
        ).qualify(
            BENCHMARK_ROOT / "tasks" / "boundary-empty-input.yaml",
            benchmark_root=BENCHMARK_ROOT,
        )

    assert result.status == "qualified"
    assert result.baseline_visible.exit_code == 0
    assert result.baseline_hidden.exit_code == 1
    assert result.corrected_visible.exit_code == 0
    assert result.corrected_hidden.exit_code == 0
    assert result.quality.mutation_score == 0.8
    assert bundle.read_bytes() == bundle_hash_before
    assert not any(settings.effective_workspace_root.glob("qual-*"))

    with sessions() as session:
        stored = session.scalar(select(BenchmarkQuality))
    assert stored is not None
    assert stored.mutants_generated == 5
    assert stored.mutants_killed == 4
    assert stored.mutants_survived == 1
    assert stored.mutation_completed is True

    artifacts = ArtifactStore(settings.effective_artifact_root)
    assert artifacts.read_bytes(result.known_patch_artifact).startswith(b"diff --git")
    assert artifacts.read_bytes(result.mutation_artifact).startswith(b'{"commands"')
    assert result.quality.qualification_artifact is not None
    assert artifacts.read_bytes(result.quality.qualification_artifact)
    engine.dispose()
