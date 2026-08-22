"""Local command line for planning and running frozen experiments."""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from sqlalchemy.orm import Session

from app.agent import (
    FakeModelProvider,
    ReadFileArguments,
    SubmitPatchAction,
    ToolCallAction,
)
from app.agent.provider import ModelProvider
from app.artifacts import ArtifactStore
from app.benchmark import load_benchmark_task
from app.config import Settings
from app.configurations import ConfigurationRunner
from app.db import create_database_engine, init_database, make_session_factory
from app.db.models import Run
from app.traces import RunTraceExporter

from .environment import EnvironmentManifestError, load_windows_environment_manifest
from .loader import load_experiment_config
from .models import ExperimentConfig
from .runner import ExperimentRunner, ExperimentSlot

type ProviderFactory = Callable[[ExperimentSlot], ModelProvider]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a frozen AgentTrace experiment matrix")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--benchmark-root", type=Path, default=Path("benchmark"))
    parser.add_argument("--state-dir", type=Path, default=Path(".agenttrace"))
    provider = parser.add_mutually_exclusive_group(required=True)
    provider.add_argument(
        "--fake-known-correct",
        action="store_true",
        help="offline integration mode; submits the evaluator's known-correct patch",
    )
    provider.add_argument(
        "--provider-factory",
        metavar="MODULE:FUNCTION",
        help="provider-neutral factory accepting one ExperimentSlot",
    )
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)

    config = load_experiment_config(arguments.config)
    _require_frozen_environment(config, Path.cwd())
    root = arguments.benchmark_root.resolve(strict=True)
    settings = Settings(state_dir=arguments.state_dir.resolve())
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(settings.effective_database_url)
    init_database(engine)
    sessions = make_session_factory(engine)
    try:
        provider_factory = (
            _known_correct_provider_factory(config.tasks, root)
            if arguments.fake_known_correct
            else _load_provider_factory(arguments.provider_factory)
        )

        def configuration_factory(
            session: Session,
            slot: ExperimentSlot,
        ) -> ConfigurationRunner:
            return ConfigurationRunner.from_services(
                session,
                settings=settings,
                provider=provider_factory(slot),
            )

        artifact_store = ArtifactStore(
            settings.effective_artifact_root,
            max_artifact_bytes=settings.max_artifact_size_bytes,
        )

        def raw_export(
            session: Session,
            slot: ExperimentSlot,
            run: Run,
        ) -> dict[str, object]:
            del slot
            return RunTraceExporter(session, artifact_store).build(run.run_id).model_dump(
                mode="json"
            )

        runner = ExperimentRunner(
            sessions,
            settings=settings,
            benchmark_root=root,
            configuration_runner_factory=configuration_factory,
            raw_export_factory=raw_export,
        )
        if arguments.dry_run:
            print(
                json.dumps(
                    [slot.model_dump(mode="json") for slot in runner.plan(config)],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        outcome = runner.run(config)
        print(outcome.model_dump_json(indent=2))
        return 0
    finally:
        engine.dispose()


def _known_correct_provider_factory(
    task_references: list[str],
    benchmark_root: Path,
) -> ProviderFactory:
    fixtures: dict[str, tuple[str, str]] = {}
    for reference in task_references:
        loaded = load_benchmark_task(
            benchmark_root / Path(*reference.split("/")),
            benchmark_root=benchmark_root,
        )
        fixtures[loaded.task.task_id] = (
            loaded.known_correct_patch_path.read_text(encoding="utf-8"),
            loaded.task.allowed_paths[0],
        )

    def build(slot: ExperimentSlot) -> ModelProvider:
        patch, readable_path = fixtures[slot.task_id]
        steps: list[ToolCallAction | SubmitPatchAction] = []
        if slot.condition != "A":
            steps.append(
                ToolCallAction(
                    tool="read_file",
                    arguments=ReadFileArguments(path=readable_path),
                )
            )
        steps.append(
            SubmitPatchAction(
                unified_diff=patch,
                rationale=(
                    "Offline Phase 9 integration fixture: submit the evaluator-provided "
                    "known-correct patch. This is not a model-quality measurement."
                ),
            )
        )
        return FakeModelProvider(
            steps
        )

    return build


def _load_provider_factory(reference: str | None) -> ProviderFactory:
    if reference is None or reference.count(":") != 1:
        raise ValueError("provider factory must use MODULE:FUNCTION syntax")
    module_name, function_name = reference.split(":", 1)
    factory = getattr(importlib.import_module(module_name), function_name, None)
    if not callable(factory):
        raise ValueError("provider factory reference is not callable")
    return cast(ProviderFactory, factory)


def _require_frozen_environment(
    config: ExperimentConfig,
    repository_root: Path,
) -> None:
    """Bind schema-v2 execution to its immutable native Windows manifest."""

    if config.environment is None:
        return
    relative = Path(*config.environment.manifest_path.split("/"))
    root = repository_root.resolve(strict=True)
    manifest_path = (root / relative).resolve(strict=True)
    if not manifest_path.is_relative_to(root):
        raise EnvironmentManifestError("environment manifest escapes repository root")
    manifest = load_windows_environment_manifest(manifest_path)
    if (
        manifest.environment_id != config.environment.environment_id
        or manifest.environment_fingerprint_sha256
        != config.environment.fingerprint_sha256
    ):
        raise EnvironmentManifestError(
            "experiment configuration and environment manifest do not match"
        )


if __name__ == "__main__":
    raise SystemExit(main())
