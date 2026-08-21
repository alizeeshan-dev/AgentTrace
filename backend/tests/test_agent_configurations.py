from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.agent import (
    AgentBudgets,
    AgentRunService,
    FakeModelProvider,
    ListTreeArguments,
    ModelUsage,
    ReadFileArguments,
    SubmitPatchAction,
    ToolCallAction,
)
from app.artifacts import ArtifactStore
from app.benchmark import load_benchmark_task
from app.config import Settings
from app.db.engine import create_database_engine, init_database, make_session_factory
from app.db.models import PatchArtifact, Repository, Run, Task

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"
MANIFEST = BENCHMARK_ROOT / "tasks" / "boundary-empty-input.yaml"


def _database_with_task() -> tuple[object, object]:
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    assert loaded.repository_path is not None
    engine = create_database_engine("sqlite://")
    init_database(engine)
    sessions = make_session_factory(engine)
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
                known_correct_patch="qualified-patch-artifact",
            )
        )
    return engine, sessions


def _correct_patch() -> str:
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    return loaded.known_correct_patch_path.read_text(encoding="utf-8")


def test_configuration_a_produces_and_stores_exactly_one_patch(tmp_path: Path) -> None:
    engine, sessions = _database_with_task()
    settings = Settings(state_dir=tmp_path / "runtime")
    loaded = load_benchmark_task(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    assert loaded.repository_path is not None
    source_before = loaded.repository_path.read_bytes()
    action = SubmitPatchAction(
        unified_diff=_correct_patch(),
        rationale="Handle the explicitly requested empty-input boundary.",
    )
    provider = FakeModelProvider(
        [action],
        usage_per_action=ModelUsage(input_tokens=31, output_tokens=19),
        latency_ms=2,
    )

    with sessions.begin() as session:
        result = AgentRunService(session, settings=settings, provider=provider).run_direct(
            MANIFEST,
            benchmark_root=BENCHMARK_ROOT,
            run_id="phase5-direct",
            model_identifier="fake-model",
            model_parameters={"temperature": 0},
        )

    assert result.status == "patch_submitted"
    assert result.patch is not None and result.patch.applied_successfully is True
    assert result.context_sha256 is not None
    assert len(provider.requests) == 1
    assert provider.requests[0].available_tools == []
    prompt = provider.requests[0].messages[-1].content
    assert "ministats/summary.py" in prompt
    assert "hidden_test_command" not in prompt
    assert "known_correct_patch" not in prompt
    assert loaded.repository_path.read_bytes() == source_before
    assert not any(settings.effective_workspace_root.iterdir())

    assert result.patch_artifact is not None
    stored_patch = ArtifactStore(settings.effective_artifact_root).read_bytes(result.patch_artifact)
    assert stored_patch.decode("utf-8") == action.unified_diff

    repeat_provider = FakeModelProvider([action])
    with sessions.begin() as session:
        repeated = AgentRunService(session, settings=settings, provider=repeat_provider).run_direct(
            MANIFEST,
            benchmark_root=BENCHMARK_ROOT,
            run_id="phase5-direct-repeat",
            model_identifier="fake-model",
            model_parameters={"temperature": 0},
        )
    assert repeated.context_sha256 == result.context_sha256
    assert repeat_provider.requests[0].messages == provider.requests[0].messages

    with sessions() as session:
        run = session.scalar(select(Run).where(Run.run_id == "phase5-direct"))
        patches = session.scalars(
            select(PatchArtifact).where(PatchArtifact.run_id == "phase5-direct")
        ).all()
    assert run is not None
    assert run.configuration_id == "A"
    assert run.input_tokens == 31
    assert run.output_tokens == 19
    assert run.tool_calls == 0
    assert run.final_resolution is None
    assert run.model_parameters["artifact_references"] == {
        "model": result.model_artifact.relative_path,
        "patch": result.patch_artifact.relative_path,
    }
    assert len(patches) == 1
    engine.dispose()


def test_configuration_b_uses_a_bounded_tool_then_submits_patch(tmp_path: Path) -> None:
    engine, sessions = _database_with_task()
    settings = Settings(state_dir=tmp_path / "runtime")
    provider = FakeModelProvider(
        [
            ToolCallAction(
                tool="read_file",
                arguments=ReadFileArguments(path="ministats/summary.py"),
            ),
            SubmitPatchAction(
                unified_diff=_correct_patch(),
                rationale="The empty collection needs an explicit sentinel return.",
            ),
        ],
        usage_per_action=ModelUsage(input_tokens=10, output_tokens=4),
    )

    with sessions.begin() as session:
        result = AgentRunService(session, settings=settings, provider=provider).run_tool_agent(
            MANIFEST,
            benchmark_root=BENCHMARK_ROOT,
            run_id="phase5-tool-agent",
            model_identifier="fake-model",
        )

    assert result.status == "patch_submitted"
    assert result.patch is not None and result.patch.applied_successfully is True
    assert result.context_sha256 is None
    assert len(provider.requests) == 2
    assert provider.requests[0].available_tools == ["list_tree", "read_file", "search_code"]
    assert "return sum(values) / len(values)" in provider.requests[1].messages[-1].content
    with sessions() as session:
        run = session.get(Run, "phase5-tool-agent")
    assert run is not None
    assert run.configuration_id == "B"
    assert run.tool_calls == 1
    assert run.files_read == 1
    assert run.input_tokens == 20
    assert run.output_tokens == 8
    engine.dispose()


def test_configuration_b_records_tool_budget_exhaustion(tmp_path: Path) -> None:
    engine, sessions = _database_with_task()
    settings = Settings(state_dir=tmp_path / "runtime")
    provider = FakeModelProvider(
        [
            ToolCallAction(tool="list_tree", arguments=ListTreeArguments()),
            ToolCallAction(
                tool="read_file",
                arguments=ReadFileArguments(path="ministats/summary.py"),
            ),
        ]
    )
    budgets = AgentBudgets(max_model_turns=3, max_tool_calls=1)

    with sessions.begin() as session:
        result = AgentRunService(session, settings=settings, provider=provider).run_tool_agent(
            MANIFEST,
            benchmark_root=BENCHMARK_ROOT,
            run_id="phase5-budget-stop",
            model_identifier="fake-model",
            budgets=budgets,
        )

    assert result.status == "budget_exhausted"
    assert result.budget_exhausted == "max_tool_calls"
    assert result.patch is None
    with sessions() as session:
        run = session.get(Run, "phase5-budget-stop")
    assert run is not None
    assert run.status == "budget_exhausted"
    assert run.tool_calls == 1
    assert run.failure_category == "TOOL_MISUSE"
    engine.dispose()
