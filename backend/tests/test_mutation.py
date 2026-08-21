from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.mutation import (
    MutationEnvironmentUnavailable,
    MutationParseError,
    MutmutAdapter,
    MutmutConfig,
    build_mutmut_commands,
    calculate_mutation_score,
    detect_mutmut_environment,
    parse_mutation_result,
)
from app.mutation.adapter import installed_mutmut_config


def _stats(**overrides: int) -> str:
    values = {
        "killed": 2,
        "survived": 2,
        "total": 10,
        "no_tests": 1,
        "skipped": 1,
        "suspicious": 1,
        "timeout": 1,
        "check_was_interrupted_by_user": 0,
        "segfault": 1,
    }
    values.update(overrides)
    return json.dumps(values)


def _statuses() -> str:
    return """\
    pkg.logic.x_f__mutmut_1: killed
    pkg.logic.x_f__mutmut_2: killed
    pkg.logic.x_f__mutmut_3: survived
    pkg.logic.x_f__mutmut_4: survived
    pkg.logic.x_f__mutmut_5: no tests
    pkg.logic.x_f__mutmut_6: skipped
    pkg.logic.x_f__mutmut_7: suspicious
    pkg.logic.x_f__mutmut_8: timeout
    pkg.logic.x_f__mutmut_9: segfault
    pkg.logic.x_f__mutmut_10: caught by type check
"""


def test_mutation_score_uses_only_killed_and_survived_mutants() -> None:
    assert calculate_mutation_score(3, 1) == 0.75
    assert calculate_mutation_score(0, 0) is None


def test_parse_mutmut_evidence_classifies_unusable_and_invalid_mutants() -> None:
    result = parse_mutation_result(
        _stats(),
        _statuses(),
        manual_exclusions={"pkg.logic.x_f__mutmut_4": "reviewed as equivalent"},
    )

    assert result.generated == 10
    assert result.killed == 2
    assert result.survived == 1
    assert result.excluded == 2
    assert result.skipped == 1
    assert result.invalid == 1
    assert result.unusable == 4
    assert result.mutation_score == pytest.approx(2 / 3)
    assert result.completed is True


def test_parser_rejects_totals_that_do_not_match_per_mutant_evidence() -> None:
    with pytest.raises(MutationParseError, match="total does not match"):
        parse_mutation_result(_stats(total=11), _statuses())


def test_config_and_commands_are_deterministic_argv_not_shell_strings() -> None:
    config = MutmutConfig(
        source_paths=("src/package",),
        test_selection=("tests", ".agentrace-hidden/tests"),
        pytest_args=("-q",),
        max_children=2,
    )

    assert config.render_setup_cfg() == (
        "[mutmut]\n"
        "source_paths =\n"
        "    src/package\n"
        "pytest_add_cli_args_test_selection =\n"
        "    tests\n"
        "    .agentrace-hidden/tests\n"
        "pytest_add_cli_args =\n"
        "    -q\n"
        "mutate_only_covered_lines = false\n"
        "timeout_multiplier = 15.0\n"
        "timeout_constant = 1.0\n"
        "use_git_change_detection = false\n"
    )
    assert build_mutmut_commands("/usr/bin/mutmut", config) == (
        ("/usr/bin/mutmut", "--version"),
        ("/usr/bin/mutmut", "run", "--max-children", "2"),
        ("/usr/bin/mutmut", "export-cicd-stats"),
        ("/usr/bin/mutmut", "results", "--all"),
    )


def test_temporary_config_restores_repository_file_exactly(tmp_path: Path) -> None:
    setup_cfg = tmp_path / "setup.cfg"
    original = b"[metadata]\nname = pilot\n"
    setup_cfg.write_bytes(original)

    with installed_mutmut_config(tmp_path, "[mutmut]\nsource_paths = src\n"):
        assert b"[mutmut]" in setup_cfg.read_bytes()

    assert setup_cfg.read_bytes() == original


def test_environment_unavailable_is_explicit_on_native_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.mutation.adapter.platform.system", lambda: "Windows")
    environment = detect_mutmut_environment()

    assert environment.available is False
    assert environment.reason is not None and "Linux Docker or WSL" in environment.reason
    with pytest.raises(MutationEnvironmentUnavailable, match="fork support"):
        MutmutAdapter().run(
            Path.cwd(),
            MutmutConfig(source_paths=("backend/app",), test_selection=("backend/tests",)),
        )
