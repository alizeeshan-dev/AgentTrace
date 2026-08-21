"""Per-test source coverage collection for spectrum-based fault localization.

The collector runs evaluator-authored pytest commands in a disposable workspace.
Coverage.py's pytest dynamic contexts associate executed lines with individual
tests.  Hidden node ids are replaced with stable opaque ids before data leaves
this module, and Coverage.py's temporary data files are then discarded.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic_ns
from typing import Any, Literal

TestOutcome = Literal["passed", "failed", "skipped"]
TestSuite = Literal["visible", "hidden"]

_RESULTS_ENVIRONMENT_VARIABLE = "AGENTTRACE_PYTEST_RESULTS_FILE"
_PLUGIN_REPORTS: dict[str, TestOutcome] = {}


class CoverageCollectionError(RuntimeError):
    """Raised when trustworthy per-test coverage cannot be collected."""


@dataclass(frozen=True, slots=True, order=True)
class ExecutedLine:
    """One repository-relative source line executed by a test."""

    file: str
    line: int


@dataclass(frozen=True, slots=True)
class CoveredTest:
    """A test outcome and the source lines attributed to its dynamic context."""

    test_id: str
    suite: TestSuite
    outcome: TestOutcome
    executed_lines: tuple[ExecutedLine, ...]


@dataclass(frozen=True, slots=True)
class SuiteExecution:
    """Bounded process metadata that contains no test output or hidden paths."""

    suite: TestSuite
    exit_code: int
    duration_ms: int
    test_count: int


@dataclass(frozen=True, slots=True)
class PerTestCoverage:
    """Sanitized, reproducible test outcomes and their executed source lines."""

    tests: tuple[CoveredTest, ...]
    executions: tuple[SuiteExecution, ...]
    source_files: tuple[str, ...]


class PerTestCoverageCollector:
    """Collect Coverage.py dynamic contexts from visible and hidden pytest suites."""

    def __init__(
        self,
        *,
        max_output_chars: int = 100_000,
        max_tests: int = 10_000,
        max_source_files: int = 2_000,
        max_line_observations: int = 1_000_000,
    ) -> None:
        if min(max_output_chars, max_tests, max_source_files, max_line_observations) < 1:
            raise ValueError("coverage collection bounds must be positive")
        self.max_output_chars = max_output_chars
        self.max_tests = max_tests
        self.max_source_files = max_source_files
        self.max_line_observations = max_line_observations

    def collect(
        self,
        *,
        workspace: str | Path,
        visible_test_command: str,
        hidden_test_command: str,
        hidden_tests: str | Path,
        source_paths: Sequence[str],
        timeout_seconds: int,
    ) -> PerTestCoverage:
        """Run both suites and return only repository source coverage.

        ``hidden_tests`` must be outside the disposable workspace.  Its source,
        path, and pytest node ids are never returned.  ``source_paths`` accepts
        repository-relative Python files or directories and defines the only
        files eligible for the returned spectrum.
        """

        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir() or _is_link_like(Path(workspace)):
            raise CoverageCollectionError("workspace must be a non-linked directory")
        hidden_root = Path(hidden_tests).resolve(strict=True)
        if not hidden_root.is_dir() or _is_link_like(Path(hidden_tests)):
            raise CoverageCollectionError("hidden tests must be a non-linked directory")
        if _paths_overlap(root, hidden_root):
            raise CoverageCollectionError("hidden tests must be outside the workspace")
        selections = _resolve_source_selections(root, source_paths)

        visible_argv = _pytest_argv(visible_test_command, hidden_tests=None)
        hidden_argv = _pytest_argv(hidden_test_command, hidden_tests=hidden_root)
        collected_tests: list[CoveredTest] = []
        executions: list[SuiteExecution] = []
        source_files: set[str] = set()
        observations = 0

        with tempfile.TemporaryDirectory(prefix="agentrace-sbfl-") as temporary:
            temporary_root = Path(temporary)
            coverage_config = temporary_root / "coveragerc"
            coverage_config.write_text(
                "[run]\nbranch = false\nparallel = false\nrelative_files = false\n",
                encoding="utf-8",
            )
            suite_commands: tuple[tuple[TestSuite, tuple[str, ...]], ...] = (
                ("visible", visible_argv),
                ("hidden", hidden_argv),
            )
            for suite, argv in suite_commands:
                suite_tests, execution = self._collect_suite(
                    suite=suite,
                    argv=argv,
                    workspace=root,
                    source_selections=selections,
                    temporary_root=temporary_root,
                    coverage_config=coverage_config,
                    timeout_seconds=timeout_seconds,
                    hidden_root=hidden_root if suite == "hidden" else None,
                )
                observations += sum(len(test.executed_lines) for test in suite_tests)
                if observations > self.max_line_observations:
                    raise CoverageCollectionError("per-test line observations exceed the bound")
                collected_tests.extend(suite_tests)
                executions.append(execution)
                for test in suite_tests:
                    source_files.update(line.file for line in test.executed_lines)

        if len(collected_tests) > self.max_tests:
            raise CoverageCollectionError("collected test count exceeds the bound")
        if len(source_files) > self.max_source_files:
            raise CoverageCollectionError("measured source file count exceeds the bound")
        return PerTestCoverage(
            tests=tuple(collected_tests),
            executions=tuple(executions),
            source_files=tuple(sorted(source_files)),
        )

    def _collect_suite(
        self,
        *,
        suite: TestSuite,
        argv: tuple[str, ...],
        workspace: Path,
        source_selections: tuple[Path, ...],
        temporary_root: Path,
        coverage_config: Path,
        timeout_seconds: int,
        hidden_root: Path | None,
    ) -> tuple[list[CoveredTest], SuiteExecution]:
        coverage_file = temporary_root / f".{suite}.coverage"
        results_file = temporary_root / f"{suite}-results.json"
        arguments = (
            *argv,
            "-p",
            "pytest_cov",
            "-p",
            "app.fault_localization.coverage",
            "--cov=.",
            "--cov-context=test",
            "--cov-report=",
            f"--cov-config={coverage_config}",
        )
        environment = _collection_environment(
            workspace,
            coverage_file=coverage_file,
            results_file=results_file,
        )
        started_ns = monotonic_ns()
        try:
            completed = subprocess.run(
                arguments,
                cwd=workspace,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            _bounded_text(error.stdout, self.max_output_chars)
            _bounded_text(error.stderr, self.max_output_chars)
            raise CoverageCollectionError(f"{suite} test coverage collection timed out") from error

        duration_ms = (monotonic_ns() - started_ns) // 1_000_000
        stdout = _bounded_text(completed.stdout, self.max_output_chars)
        stderr = _bounded_text(completed.stderr, self.max_output_chars)
        if not results_file.is_file() or not coverage_file.is_file():
            # Output is deliberately not included: hidden-suite output can contain
            # evaluator paths or assertion source.
            del stdout, stderr
            raise CoverageCollectionError(f"{suite} suite produced incomplete coverage data")

        outcomes = _read_outcomes(results_file, max_tests=self.max_tests)
        if completed.returncode not in {0, 1} or not outcomes:
            raise CoverageCollectionError(
                f"{suite} suite did not produce a valid pass/fail test run"
            )
        coverage_by_node = _read_dynamic_contexts(
            coverage_file,
            workspace=workspace,
            source_selections=source_selections,
            max_source_files=self.max_source_files,
        )
        tests: list[CoveredTest] = []
        for node_id, outcome in sorted(outcomes.items()):
            public_id = (
                node_id
                if hidden_root is None
                else _opaque_hidden_test_id(
                    node_id,
                    hidden_root=hidden_root,
                    workspace=workspace,
                )
            )
            tests.append(
                CoveredTest(
                    test_id=public_id,
                    suite=suite,
                    outcome=outcome,
                    executed_lines=tuple(sorted(coverage_by_node.get(node_id, set()))),
                )
            )
        return tests, SuiteExecution(suite, completed.returncode, duration_ms, len(tests))


def pytest_runtest_logreport(report: Any) -> None:
    """Pytest plugin hook used only in collector subprocesses."""

    if _RESULTS_ENVIRONMENT_VARIABLE not in os.environ:
        return
    node_id = str(report.nodeid)
    current = _PLUGIN_REPORTS.get(node_id)
    if bool(report.failed):
        _PLUGIN_REPORTS[node_id] = "failed"
    elif current != "failed" and bool(report.skipped):
        _PLUGIN_REPORTS[node_id] = "skipped"
    elif current is None and str(report.when) == "call" and bool(report.passed):
        _PLUGIN_REPORTS[node_id] = "passed"


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    """Write evaluator-private outcome data for the parent collector process."""

    del session, exitstatus
    destination = os.environ.get(_RESULTS_ENVIRONMENT_VARIABLE)
    if destination is None:
        return
    Path(destination).write_text(
        json.dumps(_PLUGIN_REPORTS, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    _PLUGIN_REPORTS.clear()


def _pytest_argv(command: str, *, hidden_tests: Path | None) -> tuple[str, ...]:
    arguments = shlex.split(command, posix=True)
    if not arguments:
        raise CoverageCollectionError("test command cannot be empty")
    if hidden_tests is None and "{hidden_tests}" in arguments:
        raise CoverageCollectionError("visible test command cannot reference hidden tests")
    arguments = [
        str(hidden_tests) if value == "{hidden_tests}" and hidden_tests is not None else value
        for value in arguments
    ]
    if "{hidden_tests}" in arguments:
        raise CoverageCollectionError("hidden test command requires an evaluator path")
    if arguments[0] in {"pytest", "py.test"}:
        pytest_arguments = arguments[1:]
    elif (
        arguments[0] in {"python", "python3", sys.executable}
        and len(arguments) >= 3
        and arguments[1:3] == ["-m", "pytest"]
    ):
        pytest_arguments = arguments[3:]
    else:
        raise CoverageCollectionError("SBFL collection supports pytest commands only")
    if any(value == "--cov" or value.startswith("--cov=") for value in pytest_arguments):
        raise CoverageCollectionError("test command cannot override coverage collection")
    return (sys.executable, "-m", "pytest", *pytest_arguments)


def _collection_environment(
    workspace: Path,
    *,
    coverage_file: Path,
    results_file: Path,
) -> dict[str, str]:
    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "TEMP", "TMP", "WINDIR")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    backend_root = Path(__file__).resolve().parents[2]
    environment.update(
        {
            "COVERAGE_FILE": str(coverage_file),
            _RESULTS_ENVIRONMENT_VARIABLE: str(results_file),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join((str(workspace), str(backend_root))),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
    )
    return environment


def _resolve_source_selections(root: Path, source_paths: Sequence[str]) -> tuple[Path, ...]:
    if not source_paths:
        raise CoverageCollectionError("at least one source path is required")
    selections: list[Path] = []
    for value in source_paths:
        pure = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise CoverageCollectionError("source paths must be literal repository paths")
        candidate = root.joinpath(*pure.parts)
        _reject_linked_components(candidate, root)
        canonical = candidate.resolve(strict=True)
        if not _is_within(canonical, root) or not (canonical.is_file() or canonical.is_dir()):
            raise CoverageCollectionError("source path is outside the workspace")
        selections.append(canonical)
    return tuple(selections)


def _read_outcomes(path: Path, *, max_tests: int) -> dict[str, TestOutcome]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or len(payload) > max_tests:
        raise CoverageCollectionError("pytest outcome data is invalid or exceeds the bound")
    outcomes: dict[str, TestOutcome] = {}
    for node_id, outcome in payload.items():
        if not isinstance(node_id, str) or outcome not in {"passed", "failed", "skipped"}:
            raise CoverageCollectionError("pytest outcome data is invalid")
        outcomes[node_id] = outcome
    return outcomes


def _read_dynamic_contexts(
    path: Path,
    *,
    workspace: Path,
    source_selections: tuple[Path, ...],
    max_source_files: int,
) -> dict[str, set[ExecutedLine]]:
    try:
        from coverage import CoverageData
    except ImportError as error:
        raise CoverageCollectionError("Coverage.py is required for SBFL collection") from error

    data = CoverageData(basename=str(path))
    data.read()
    selected_files: list[tuple[str, Path]] = []
    for measured_file in data.measured_files():
        canonical = Path(measured_file).resolve(strict=False)
        if not _is_within(canonical, workspace):
            continue
        if not any(_matches_selection(canonical, selection) for selection in source_selections):
            continue
        selected_files.append((measured_file, canonical))
    if len(selected_files) > max_source_files:
        raise CoverageCollectionError("measured source file count exceeds the bound")

    by_test: dict[str, set[ExecutedLine]] = {}
    for measured_file, canonical in selected_files:
        relative = canonical.relative_to(workspace).as_posix()
        for line_number, contexts in data.contexts_by_lineno(measured_file).items():
            executed_line = ExecutedLine(relative, line_number)
            for context in contexts:
                node_id = _node_id_from_context(context)
                if node_id is not None:
                    by_test.setdefault(node_id, set()).add(executed_line)
    return by_test


def _node_id_from_context(context: str) -> str | None:
    if "|" not in context:
        return None
    node_id, phase = context.rsplit("|", maxsplit=1)
    if not node_id or phase not in {"setup", "run", "teardown"}:
        return None
    return node_id


def _opaque_hidden_test_id(
    node_id: str,
    *,
    hidden_root: Path,
    workspace: Path,
) -> str:
    path_text, separator, test_name = node_id.partition("::")
    if not separator:
        raise CoverageCollectionError("hidden pytest node identifier is invalid")
    node_path = Path(path_text)
    candidates = [node_path] if node_path.is_absolute() else [
        workspace / node_path,
        hidden_root.parent / node_path,
    ]
    if hidden_root.name in node_path.parts:
        hidden_index = node_path.parts.index(hidden_root.name)
        candidates.append(hidden_root.joinpath(*node_path.parts[hidden_index + 1 :]))
    relative_path: Path | None = None
    for candidate in candidates:
        try:
            relative_path = candidate.resolve(strict=True).relative_to(hidden_root)
            break
        except (OSError, RuntimeError, ValueError):
            continue
    if relative_path is None:
        raise CoverageCollectionError(
            "hidden pytest node identifier escaped the evaluator directory"
        )
    stable_private_id = f"{relative_path.as_posix()}::{test_name}"
    digest = hashlib.sha256(stable_private_id.encode("utf-8")).hexdigest()[:20]
    return f"hidden-test-{digest}"


def _matches_selection(candidate: Path, selection: Path) -> bool:
    return candidate == selection or (selection.is_dir() and _is_within(candidate, selection))


def _reject_linked_components(candidate: Path, root: Path) -> None:
    current = candidate
    while current != root:
        if _is_link_like(current):
            raise CoverageCollectionError("source paths cannot use links or junctions")
        if root not in current.parents:
            raise CoverageCollectionError("source path escapes the workspace")
        current = current.parent


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _bounded_text(value: str | bytes | None, limit: int) -> str:
    if value is None:
        return ""
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[output truncated by AgentTrace]"
