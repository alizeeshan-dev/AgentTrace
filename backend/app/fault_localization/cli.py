"""Command-line entry point for pre-agent benchmark fault localization."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from app.config import Settings
from app.db.engine import (
    create_database_engine,
    init_database,
    make_session_factory,
    session_scope,
)
from app.fault_localization.service import FaultLocalizationService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Localize one qualified benchmark task")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--benchmark-root", type=Path, default=Path("benchmark"))
    parser.add_argument("--state-dir", type=Path, default=Path(".agenttrace"))
    parser.add_argument("--run-id")
    parser.add_argument("--top-k", type=int, default=10)
    arguments = parser.parse_args(argv)

    settings = Settings(state_dir=arguments.state_dir)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(settings.effective_database_url)
    init_database(engine)
    sessions = make_session_factory(engine)
    try:
        with session_scope(sessions) as session:
            localized = FaultLocalizationService(session, settings=settings).localize(
                arguments.manifest,
                benchmark_root=arguments.benchmark_root,
                run_id=arguments.run_id,
                top_k=arguments.top_k,
            )
        print(
            json.dumps(
                {
                    "task_id": localized.task_id,
                    "run_id": localized.result.run_id,
                    "metric": localized.result.metric,
                    "passing_tests": localized.passing_tests,
                    "failing_tests": localized.failing_tests,
                    "skipped_tests": localized.skipped_tests,
                    "true_fault_rank": localized.metrics.true_fault_rank,
                    "top_1": localized.metrics.top_1,
                    "top_5": localized.metrics.top_5,
                    "top_10": localized.metrics.top_10,
                    "ranked_locations": localized.result.ranked_locations,
                    "coverage_artifact": localized.result.coverage_artifact,
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
