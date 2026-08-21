from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.budgets import AgentBudgets, BudgetExhausted, BudgetTracker
from app.agent.tools import ConstrainedRepositoryTools
from app.repositories.path_policy import PathPolicyError, RepositoryPathPolicy


def _tools(tmp_path: Path, *, max_tool_calls: int = 6) -> ConstrainedRepositoryTools:
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    (root / "src" / "maths.py").write_text(
        "def add(left: int, right: int) -> int:\n    return left - right\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_maths.py").write_text("def test_visible(): pass\n", encoding="utf-8")
    (root / "hidden_tests").mkdir()
    (root / "hidden_tests" / "test_secret.py").write_text("SECRET = 42\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret\n", encoding="utf-8")
    policy = RepositoryPathPolicy(
        root,
        allowed_paths=("src/",),
        forbidden_paths=("tests/",),
        hidden_paths=("hidden_tests/",),
        max_file_bytes=1_000,
    )
    budgets = AgentBudgets(
        max_tool_calls=max_tool_calls,
        max_files_read=2,
        max_files_exposed=5,
        max_content_characters=2_000,
    )
    return ConstrainedRepositoryTools(policy, BudgetTracker(budgets))


def test_tools_return_bounded_repository_evidence_without_sensitive_paths(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    tree = tools.execute("list_tree", {"path": "."})
    source = tools.execute("read_file", {"path": "src/maths.py"})
    search = tools.execute("search_code", {"query": "return", "path": "src"})

    assert "src/maths.py" in tree.content
    assert "hidden_tests" not in tree.content
    assert ".git" not in tree.content
    assert "left - right" in source.content
    assert search.content == "src/maths.py:2:    return left - right"
    assert tools.tracker.tool_calls == 3
    assert tools.tracker.files_read == 1


def test_traversal_and_hidden_test_access_are_rejected(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    with pytest.raises(PathPolicyError):
        tools.execute("read_file", {"path": "../outside.py"})
    with pytest.raises(PathPolicyError, match="Hidden evaluator"):
        tools.execute("read_file", {"path": "hidden_tests/test_secret.py"})


def test_tool_call_budget_stops_an_excessive_run(tmp_path: Path) -> None:
    tools = _tools(tmp_path, max_tool_calls=1)
    tools.execute("list_tree", {"path": "."})

    with pytest.raises(BudgetExhausted, match="max_tool_calls"):
        tools.execute("search_code", {"query": "return", "path": "src"})


def test_search_result_and_file_exposure_are_bounded(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    tools.tracker.limits = AgentBudgets(
        max_tool_calls=2,
        max_files_read=1,
        max_files_exposed=1,
        max_content_characters=40,
        max_search_result_characters=40,
        max_search_matches=1,
    )

    result = tools.search_code("return", path="src")

    assert result.truncated is False
    assert len(result.content) <= 40
    assert tools.tracker.files_exposed == 1
