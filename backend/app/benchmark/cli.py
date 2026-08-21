"""Command-line entry point for benchmark qualification."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from app.benchmark.qualification import BenchmarkQualificationService
from app.config import Settings
from app.db.engine import (
    create_database_engine,
    init_database,
    make_session_factory,
    session_scope,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qualify one AgentTrace benchmark task")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--benchmark-root", type=Path, default=Path("benchmark"))
    parser.add_argument("--state-dir", type=Path, default=Path(".agenttrace"))
    arguments = parser.parse_args(argv)

    settings = Settings(state_dir=arguments.state_dir)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(settings.effective_database_url)
    init_database(engine)
    sessions = make_session_factory(engine)
    try:
        with session_scope(sessions) as session:
            result = BenchmarkQualificationService(session, settings=settings).qualify(
                arguments.manifest,
                benchmark_root=arguments.benchmark_root,
            )
        print(
            json.dumps(
                {
                    "task_id": result.task_id,
                    "status": result.status,
                    "mutation_score": result.quality.mutation_score,
                    "mutants_generated": result.quality.mutants_generated,
                    "mutants_killed": result.quality.mutants_killed,
                    "mutants_survived": result.quality.mutants_survived,
                    "mutants_excluded": result.quality.mutants_excluded,
                    "mutation_artifact": result.mutation_artifact.relative_path,
                    "qualification_artifact": result.quality.qualification_artifact,
                },
                sort_keys=True,
            )
        )
        return 0 if result.status == "qualified" else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
