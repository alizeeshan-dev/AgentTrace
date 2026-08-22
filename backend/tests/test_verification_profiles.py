from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.benchmark import load_benchmark_task
from app.configurations import ExperimentalConfiguration, resolve_research_techniques
from app.verification.properties import (
    build_property_execution_plan,
    load_property_profile,
    normalize_property_result,
)
from app.verification.symbolic import (
    build_symbolic_execution_plan,
    load_configured_symbolic_profile,
    normalize_symbolic_result,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"


def _property_fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "benchmark"
    (root / "property_profiles").mkdir(parents=True)
    test_dir = root / "property_tests" / "boundary-properties"
    test_dir.mkdir(parents=True)
    (test_dir / "test_properties.py").write_text(
        "# evaluator-owned property source\n",
        encoding="utf-8",
    )
    (root / "property_profiles" / "boundary-properties.yaml").write_text(
        """\
schema_version: 1
profile_id: boundary-properties
test_file: property_tests/boundary-properties/test_properties.py
max_examples: 25
deadline_ms: null
timeout_seconds: 12
""",
        encoding="utf-8",
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    return root, repository


def test_property_profile_builds_deterministic_native_plan(tmp_path: Path) -> None:
    root, repository = _property_fixture(tmp_path)

    loaded = load_property_profile(root, "boundary-properties", repository_path=repository)
    plan = build_property_execution_plan(loaded)

    assert plan.result_path.as_posix() == "/output/property-counterexamples.json"
    assert plan.timeout_seconds == 12
    assert plan.evaluator_mounts[0].read_only is True
    assert plan.evaluator_mounts[0].virtual_path.as_posix().startswith(
        "/evaluator/property-tests/"
    )
    plugin = next(
        file.content.decode("utf-8")
        for file in plan.generated_files
        if file.virtual_path.name == "agentrace_property_plugin.py"
    )
    assert "max_examples=25" in plugin
    assert "deadline=None" in plugin
    assert "derandomize=True" in plugin
    assert "database=None" in plugin
    assert "Phase.generate, Phase.shrink" in plugin
    assert str(loaded.test_path) not in " ".join(plan.argv)
    assert plan.argv[:3] == ("python", "-I", "-c")
    assert "--junitxml=/output/property-tests.xml" in plan.argv
    assert all(key != "PYTHONPATH" for key, _value in plan.environment)


def test_property_profile_rejects_repository_overlap(tmp_path: Path) -> None:
    root, _repository = _property_fixture(tmp_path)

    with pytest.raises(ValueError, match="outside the agent repository"):
        load_property_profile(root, "boundary-properties", repository_path=root)


def test_property_result_preserves_only_sanitized_shrunk_evidence() -> None:
    sidecar = json.dumps(
        {
            "schema_version": 1,
            "counterexamples": [
                {
                    "input": "",
                    "expected": [],
                    "observed": "IndexError",
                    "exception_type": "IndexError",
                    "location_hints": ["ministats/summary.py:7"],
                    "shrunk": True,
                }
            ],
        }
    ).encode()

    result = normalize_property_result(
        exit_code=1,
        duration_ms=45,
        timed_out=False,
        sidecar=sidecar,
    )

    assert result.status == "failed"
    assert result.counterexamples[0].input_summary == '""'
    assert result.counterexamples[0].expected_summary == "[]"
    assert result.counterexamples[0].observed_summary == '"IndexError"'
    assert result.counterexamples[0].shrunk is True
    assert "test_properties.py" not in repr(result)


def test_property_result_does_not_accept_hidden_source_in_sidecar() -> None:
    sidecar = json.dumps(
        {
            "schema_version": 1,
            "counterexamples": [],
            "hidden_source": "def secret_property(): ...",
        }
    ).encode()

    with pytest.raises(ValueError):
        normalize_property_result(
            exit_code=1,
            duration_ms=1,
            timed_out=False,
            sidecar=sidecar,
        )


def test_symbolic_profile_is_explicit_and_builds_crosshair_plan(tmp_path: Path) -> None:
    root = tmp_path / "benchmark"
    profiles = root / "symbolic_profiles"
    profiles.mkdir(parents=True)
    (profiles / "mean-contract.yaml").write_text(
        """\
schema_version: 1
profile_id: mean-contract
targets:
  - ministats.summary:mean
contract_kind: PEP316
per_condition_timeout_seconds: 3
per_path_timeout_seconds: 1
max_iterations: 200
timeout_seconds: 20
""",
        encoding="utf-8",
    )

    assert load_configured_symbolic_profile(root, None) is None
    loaded = load_configured_symbolic_profile(root, "mean-contract")
    assert loaded is not None
    plan = build_symbolic_execution_plan(loaded)

    assert plan.argv[:4] == ("python", "-m", "crosshair", "check")
    assert "--analysis_kind=PEP316" in plan.argv
    assert "ministats.summary:mean" in plan.argv
    assert plan.backend == "CrossHair+Z3"


def test_expanded_benchmark_profiles_are_executable_plans_and_enable_d3() -> None:
    property_task = load_benchmark_task(
        BENCHMARK_ROOT / "tasks" / "boundary-window-last.yaml",
        benchmark_root=BENCHMARK_ROOT,
    )
    assert property_task.task.property_profile == "boundary-window-last"
    property_plan = build_property_execution_plan(
        load_property_profile(BENCHMARK_ROOT, property_task.task.property_profile)
    )
    assert property_plan.profile_id == "boundary-window-last"

    symbolic_task = load_benchmark_task(
        BENCHMARK_ROOT / "tasks" / "validation-discount-threshold.yaml",
        benchmark_root=BENCHMARK_ROOT,
    )
    resolved = resolve_research_techniques(
        ExperimentalConfiguration.preset("D3"), symbolic_task.task
    )
    assert resolved.effective.enable_crosshair is True
    assert resolved.disabled_reasons == {}
    loaded_symbolic = load_configured_symbolic_profile(
        BENCHMARK_ROOT, symbolic_task.task.symbolic_profile
    )
    assert loaded_symbolic is not None
    symbolic_plan = build_symbolic_execution_plan(loaded_symbolic)
    assert "pricing.rules:discount_rate" in symbolic_plan.argv
    assert symbolic_plan.backend == "CrossHair+Z3"


def test_no_symbolic_counterexample_is_inconclusive_not_proof() -> None:
    result = normalize_symbolic_result(
        exit_code=0,
        duration_ms=50,
        timed_out=False,
        stdout="No counterexamples found",
        stderr="",
    )

    assert result.status == "no_counterexample"
    assert result.conclusion == "inconclusive"
    assert result.proves_correctness is False
    assert "not proof" in result.summary


def test_symbolic_counterexample_is_normalized_to_repository_location() -> None:
    result = normalize_symbolic_result(
        exit_code=1,
        duration_ms=75,
        timed_out=False,
        stdout=(
            "/workspace/ministats/summary.py:7: error: "
            "false when calling mean(values=[]) (which returns 0)"
        ),
        stderr="",
    )

    assert result.status == "counterexample_found"
    assert result.conclusion == "counterexample"
    assert result.counterexamples[0].location_hint == "ministats/summary.py:7"
    assert "/workspace" not in repr(result)
