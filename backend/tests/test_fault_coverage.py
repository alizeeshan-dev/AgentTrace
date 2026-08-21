from __future__ import annotations

from pathlib import Path

from app.fault_localization.coverage import PerTestCoverageCollector


def test_collects_per_test_lines_and_anonymizes_hidden_tests(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    package = workspace / "mathlib"
    visible_tests = workspace / "tests"
    hidden_tests = tmp_path / "evaluator-hidden"
    package.mkdir(parents=True)
    visible_tests.mkdir()
    hidden_tests.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core.py").write_text(
        "def classify(value: int) -> str:\n"
        "    if value > 0:\n"
        "        return 'positive'\n"
        "    return 'other'\n",
        encoding="utf-8",
    )
    (visible_tests / "test_core.py").write_text(
        "from mathlib.core import classify\n\n"
        "def test_positive():\n"
        "    assert classify(2) == 'positive'\n",
        encoding="utf-8",
    )
    (hidden_tests / "test_zero_boundary.py").write_text(
        "from mathlib.core import classify\n\n"
        "def test_zero_is_positive():\n"
        "    assert classify(0) == 'positive'\n",
        encoding="utf-8",
    )

    result = PerTestCoverageCollector().collect(
        workspace=workspace,
        visible_test_command="python -m pytest -q tests",
        hidden_test_command="python -m pytest -q {hidden_tests}",
        hidden_tests=hidden_tests,
        source_paths=("mathlib/core.py",),
        timeout_seconds=30,
    )

    assert [test.outcome for test in result.tests] == ["passed", "failed"]
    assert result.tests[0].test_id == "tests/test_core.py::test_positive"
    assert result.tests[1].test_id.startswith("hidden-test-")
    assert "zero_boundary" not in repr(result)
    assert result.source_files == ("mathlib/core.py",)
    assert {line.line for line in result.tests[0].executed_lines} >= {2, 3}
    assert {line.line for line in result.tests[1].executed_lines} >= {2, 4}
    assert [execution.exit_code for execution in result.executions] == [0, 1]
