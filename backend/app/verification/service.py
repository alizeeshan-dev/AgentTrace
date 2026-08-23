"""Deterministic verification for explicitly trusted Python repositories.

Repository Python runs in managed disposable Git workspaces through the native
Windows verification boundary.  Each attempt receives a temporary Python
environment, fixed working directories, sanitized process variables, bounded
output, and hard timeouts.  This is intentionally weaker than VM/container
isolation and is not intended for arbitrary untrusted repositories.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.budgets import AgentBudgets
from app.agent.patches import PatchValidationError, PatchValidator, apply_validated_patch
from app.artifacts import ArtifactStore
from app.config import Settings
from app.db.models import PatchArtifact, Repository, Run, Task, VerificationResult
from app.repositories.workspace import DisposableWorkspace, WorkspaceManager
from app.services.workspaces import LoadedTaskWorkspace, TaskWorkspaceLoader
from app.tasks import LoadedTaskDefinition, load_task_definition

from .gates import GateOutcome, GateSpec, StandardGateFactory, StandardGateRunner
from .junit import TestInventory, compare_inventories, read_junit
from .native import (
    NativeEnvironmentError,
    WindowsExecutionEnvironment,
    WindowsVerificationRunner,
)
from .properties import (
    PropertyEvaluation,
    build_property_execution_plan,
    load_property_profile,
    normalize_property_result,
)
from .symbolic import (
    build_symbolic_execution_plan,
    load_configured_symbolic_profile,
    normalize_symbolic_result,
)

_HIDDEN_PATHS = (".agenttrace-evaluator/", "hidden_tests/")


class VerificationServiceError(RuntimeError):
    """The requested persisted run cannot be verified under this protocol."""


@dataclass(frozen=True, slots=True)
class NormalizedGate:
    gate: str
    required: bool
    status: str
    exit_code: int | None
    duration_ms: int
    summary: str
    baseline_difference: dict[str, Any] | None = None
    artifact_reference: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"


@dataclass(frozen=True, slots=True)
class VerificationRun:
    run_id: str
    attempt_number: int
    resolved: bool | None
    regression: bool
    environment_kind: str | None
    results: tuple[NormalizedGate, ...]


@dataclass(frozen=True, slots=True)
class VerificationFeatures:
    """Explicit research gates used by configuration and ablation profiles."""

    enable_hypothesis: bool = True
    enable_symbolic: bool = True
    symbolic_counterexamples_actionable: bool = False


class VerificationService:
    """Compare a candidate against a clean baseline using isolated gates."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        runner: WindowsVerificationRunner | None = None,
        features: VerificationFeatures | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.features = features or VerificationFeatures()
        self.workspaces = WorkspaceManager(settings.effective_workspace_root)
        Path(settings.effective_workspace_root).mkdir(parents=True, exist_ok=True)
        verification_root = settings.effective_verification_root
        verification_root.mkdir(parents=True, exist_ok=True)
        self.verification_root = verification_root.resolve(strict=True)
        self.artifacts = ArtifactStore(
            settings.effective_artifact_root,
            max_artifact_bytes=settings.max_artifact_size_bytes,
        )
        self.runner = runner or WindowsVerificationRunner(
            settings.effective_workspace_root,
            settings.effective_verification_root,
        )

    def verify(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        attempt_number: int = 1,
        benchmark_root: str | Path | None = None,
    ) -> VerificationRun:
        """Verify one immutable submitted patch and persist every normalized gate."""

        run = self.session.get(Run, run_id)
        patch = self.session.get(PatchArtifact, (run_id, attempt_number))
        if run is None or patch is None:
            raise VerificationServiceError("run must contain a patch artifact before verification")
        if (
            self.session.scalar(
                select(VerificationResult).where(
                    VerificationResult.run_id == run_id,
                    VerificationResult.attempt_number == attempt_number,
                )
            )
            is not None
        ):
            raise VerificationServiceError("verification attempt is immutable and already exists")
        loaded = load_task_definition(manifest_path, benchmark_root=benchmark_root)
        task = self.session.get(Task, run.task_id)
        if task is None or task.task_id != loaded.task.task_id:
            raise VerificationServiceError("persisted run and benchmark manifest do not match")
        self._require_manifest_binding(task, loaded)
        if task.task_source == "external" and not task.verification_configured:
            gate = self._store_gate(
                run_id,
                attempt_number,
                NormalizedGate(
                    "verification_configuration",
                    False,
                    "not_configured",
                    None,
                    0,
                    "No trusted pytest verification command is configured for this external task.",
                ),
            )
            parameters = dict(run.model_parameters)
            parameters["verification"] = {
                "runner": "native_windows",
                "protocol_version": "native-windows-v1",
                "task_source": "external",
                "verification_configured": False,
                "hidden_tests_available": False,
                "hypothesis_enabled": False,
                "symbolic_enabled": False,
            }
            run.model_parameters = parameters
            run.status = "verification_not_configured"
            run.final_resolution = None
            run.failure_category = None
            self.session.flush()
            return VerificationRun(run_id, attempt_number, None, False, None, (gate,))

        baseline = self._load_workspace(task.task_id, f"{run_id}-vbase")
        candidate = self._load_workspace(task.task_id, f"{run_id}-vcand")
        results: list[NormalizedGate] = []
        environment_kind: str | None = None
        regression = False
        try:
            parameters = dict(run.model_parameters)
            parameters["verification"] = {
                "runner": "native_windows",
                "gate_order": [
                    "patch_applied",
                    "python_compile",
                    "visible_tests",
                    "existing_tests",
                    "hidden_tests",
                    "hypothesis_properties",
                ],
                "process_controls": {
                    "dedicated_virtual_environment": True,
                    "sanitized_environment": True,
                    "shell": False,
                    "hard_timeouts": True,
                    "trusted_repositories_only": True,
                },
                "task_source": task.task_source,
                "verification_configured": task.verification_configured,
                "hidden_tests_available": loaded.hidden_tests_path is not None,
                "property_profile": loaded.task.property_profile,
                "hypothesis_enabled": self.features.enable_hypothesis,
                "protocol_version": "native-windows-v1",
                "symbolic_profile": loaded.task.symbolic_profile,
                "symbolic_enabled": self.features.enable_symbolic,
                "symbolic_counterexamples_actionable": (
                    self.features.symbolic_counterexamples_actionable
                ),
            }
            run.model_parameters = parameters

            with tempfile.TemporaryDirectory(dir=self.verification_root) as temporary:
                stage = Path(temporary)
                try:
                    baseline_environment = self.runner.prepare_environment(
                        stage / "baseline-venv"
                    )
                    candidate_environment = self.runner.prepare_environment(
                        stage / "candidate-venv"
                    )
                except NativeEnvironmentError as error:
                    gate = self._store_gate(
                        run_id,
                        attempt_number,
                        NormalizedGate(
                            "verification_environment",
                            True,
                            "error",
                            None,
                            0,
                            f"Native verification environment unavailable: {error}",
                        ),
                    )
                    results.append(gate)
                    run.status = "verification_infrastructure_failure"
                    run.final_resolution = None
                    run.failure_category = "INFRASTRUCTURE_FAILURE"
                    self.session.flush()
                    return VerificationRun(
                        run_id, attempt_number, None, False, None, tuple(results)
                    )
                environment_kind = "native_windows_venv"
                evaluator = self._stage_evaluator(loaded, stage)
                baseline_results, baseline_inventory, baseline_status = self._run_baseline(
                    baseline.workspace,
                    loaded,
                    baseline_environment,
                    evaluator,
                    stage,
                    run_id,
                    attempt_number,
                )
                results.extend(baseline_results)
                if any(item.required and item.status == "error" for item in baseline_results):
                    run.status = "verification_infrastructure_failure"
                    run.final_resolution = None
                    run.failure_category = "INFRASTRUCTURE_FAILURE"
                    self.session.flush()
                    return VerificationRun(
                        run_id, attempt_number, None, False, environment_kind, tuple(results)
                    )

                patch_gate = self._apply_patch(candidate, patch)
                patch_gate = self._store_gate(run_id, attempt_number, patch_gate)
                results.append(patch_gate)
                if patch_gate.passed:
                    candidate_results, regression = self._run_candidate(
                        candidate.workspace,
                        loaded,
                        candidate_environment,
                        evaluator,
                        stage,
                        baseline_inventory,
                        baseline_status,
                        run_id,
                        attempt_number,
                    )
                    results.extend(candidate_results)
                else:
                    results.extend(
                        self._skipped_candidate_gates(loaded, run_id=run_id, attempt=attempt_number)
                    )

            required = [
                item for item in results if item.required and not item.gate.startswith("baseline_")
            ]
            if any(item.status == "error" for item in required):
                run.status = "verification_infrastructure_failure"
                run.final_resolution = None
                run.failure_category = "INFRASTRUCTURE_FAILURE"
                self.session.flush()
                return VerificationRun(
                    run_id, attempt_number, None, regression, environment_kind, tuple(results)
                )
            resolved = bool(required) and all(item.passed for item in required)
            run.final_resolution = resolved
            run.status = "verified_pass" if resolved else "verified_fail"
            run.failure_category = None if resolved else self._failure_category(results, regression)
            self.session.flush()
            return VerificationRun(
                run_id, attempt_number, resolved, regression, environment_kind, tuple(results)
            )
        finally:
            self.workspaces.remove(baseline.workspace)
            self.workspaces.remove(candidate.workspace)

    def _load_workspace(self, task_id: str, workspace_id: str) -> LoadedTaskWorkspace:
        return TaskWorkspaceLoader(
            self.session,
            self.workspaces,
            max_file_bytes=self.settings.max_file_size_bytes,
        ).load(task_id=task_id, run_id=workspace_id, hidden_paths=_HIDDEN_PATHS)

    def _require_manifest_binding(self, task: Task, loaded: LoadedTaskDefinition) -> None:
        repository = self.session.get(Repository, task.repository_id)
        if repository is None or loaded.repository_path is None:
            raise VerificationServiceError("verification requires a persisted local repository")
        try:
            source = Path(repository.source).resolve(strict=True)
        except OSError as error:
            raise VerificationServiceError("persisted repository source is unavailable") from error
        if source != loaded.repository_path or repository.base_commit != loaded.task.base_commit:
            raise VerificationServiceError("persisted repository binding differs from the manifest")
        if (
            repository.source_type == "external_git"
            and not repository.trusted_for_local_execution
        ):
            raise VerificationServiceError(
                "External repository execution is blocked until explicit trust is granted"
            )
        expected = {
            "allowed_paths": loaded.task.allowed_paths,
            "forbidden_paths": loaded.task.forbidden_paths,
            "hidden_test_command": loaded.task.hidden_test_command or "",
            "property_profile": loaded.task.property_profile,
            "symbolic_profile": loaded.task.symbolic_profile,
            "visible_test_command": loaded.task.visible_test_command,
        }
        if any(getattr(task, field) != value for field, value in expected.items()):
            raise VerificationServiceError("persisted task contract differs from the manifest")

    def _stage_evaluator(
        self, loaded: LoadedTaskDefinition, stage: Path
    ) -> Path | None:
        if loaded.hidden_tests_path is None and loaded.task.property_profile is None:
            return None
        evaluator = stage / "evaluator"
        if loaded.hidden_tests_path is not None:
            hidden = evaluator / "hidden_tests"
            self._copy_evaluator_tree(loaded.hidden_tests_path, hidden)
        if self.features.enable_hypothesis and loaded.task.property_profile is not None:
            profile = load_property_profile(
                loaded.benchmark_root,
                loaded.task.property_profile,
            )
            plan = build_property_execution_plan(profile)
            for mount in plan.evaluator_mounts:
                relative = mount.virtual_path.relative_to(PurePosixPath("/evaluator"))
                destination = evaluator.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(mount.source_path, destination)
            for generated in plan.generated_files:
                relative = generated.virtual_path.relative_to(PurePosixPath("/evaluator"))
                destination = evaluator.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(generated.content)
        return evaluator

    def _copy_evaluator_tree(self, source: Path, destination: Path) -> None:
        """Copy only bounded regular evaluator files, never links or devices."""

        destination.mkdir(parents=True)
        entries = 0
        total_bytes = 0
        for root_text, directories, filenames in os.walk(source, followlinks=False):
            root = Path(root_text)
            if _is_link_like(root):
                raise VerificationServiceError("evaluator directories cannot be links")
            relative_root = root.relative_to(source)
            target_root = destination / relative_root
            target_root.mkdir(exist_ok=True)
            excluded_directories = {"__pycache__", ".pytest_cache", ".hypothesis"}
            directories[:] = [name for name in directories if name not in excluded_directories]
            filenames = [name for name in filenames if not name.endswith((".pyc", ".pyo"))]
            for name in [*directories, *filenames]:
                entries += 1
                if entries > 2_000:
                    raise VerificationServiceError("evaluator tree exceeds its entry bound")
                candidate = root / name
                if _is_link_like(candidate):
                    raise VerificationServiceError("evaluator files cannot be links")
            for name in filenames:
                candidate = root / name
                if not candidate.is_file():
                    raise VerificationServiceError("evaluator entries must be regular files")
                size = candidate.stat().st_size
                total_bytes += size
                if size > self.settings.max_file_size_bytes or total_bytes > 16_777_216:
                    raise VerificationServiceError("evaluator files exceed their size bound")
                shutil.copyfile(candidate, target_root / name)

    def _standard_specs(self, loaded: LoadedTaskDefinition) -> tuple[GateSpec, ...]:
        timeout = loaded.task.timeout_seconds
        specs: list[GateSpec] = [
            StandardGateFactory.compile(timeout_seconds=min(timeout, 60)),
            StandardGateFactory.visible_tests(
                loaded.task.visible_test_command or "", timeout_seconds=timeout
            ),
        ]
        if loaded.task.task_source == "benchmark":
            specs.append(StandardGateFactory.existing_tests("pytest -q", timeout_seconds=timeout))
        if loaded.hidden_tests_path is not None and loaded.task.hidden_test_command is not None:
            specs.append(
                StandardGateFactory.hidden_tests(
                    loaded.task.hidden_test_command, timeout_seconds=timeout
                )
            )
        return tuple(specs)

    def _run_baseline(
        self,
        workspace: DisposableWorkspace,
        loaded: LoadedTaskDefinition,
        execution_environment: WindowsExecutionEnvironment,
        evaluator: Path | None,
        stage: Path,
        run_id: str,
        attempt: int,
    ) -> tuple[list[NormalizedGate], dict[str, TestInventory], dict[str, str]]:
        results: list[NormalizedGate] = []
        inventories: dict[str, TestInventory] = {}
        statuses: dict[str, str] = {}
        runner = StandardGateRunner(self.runner, execution_environment)
        for spec in self._standard_specs(loaded):
            output = self._output_dir(stage, f"baseline-{spec.gate}")
            outcome = runner.run(
                spec,
                workspace=workspace,
                evaluator_root=evaluator if spec.gate == "hidden_tests" else None,
                output_root=output,
            )
            inventory = self._inventory(output, spec.gate)
            inventories[spec.gate] = inventory
            gate = self._from_standard(
                f"baseline_{spec.gate}", outcome, inventory, private=spec.gate == "hidden_tests"
            )
            if spec.gate in {"visible_tests", "existing_tests", "hidden_tests"}:
                gate = self._require_baseline_test_evidence(gate, inventory)
            statuses[spec.gate] = gate.status
            results.append(self._store_gate(run_id, attempt, gate))
        for spec in (
            StandardGateFactory.ruff(),
            StandardGateFactory.mypy(),
            StandardGateFactory.bandit(),
        ):
            output = self._output_dir(stage, f"baseline-{spec.gate}")
            outcome = runner.run(spec, workspace=workspace, output_root=output)
            gate = self._from_standard(f"baseline_{spec.gate}", outcome, TestInventory((), (), ()))
            statuses[spec.gate] = gate.status
            results.append(self._store_gate(run_id, attempt, gate))
        if self.features.enable_hypothesis and loaded.task.property_profile is not None:
            if evaluator is None:
                raise VerificationServiceError(
                    "property verification requires an evaluator-owned profile"
                )
            gate = self._run_property(
                workspace,
                loaded,
                execution_environment,
                evaluator,
                stage,
                baseline=True,
            )
            statuses["hypothesis_properties"] = gate.status
            results.append(self._store_gate(run_id, attempt, gate))
        return results, inventories, statuses

    def _run_candidate(
        self,
        workspace: DisposableWorkspace,
        loaded: LoadedTaskDefinition,
        execution_environment: WindowsExecutionEnvironment,
        evaluator: Path | None,
        stage: Path,
        baseline: dict[str, TestInventory],
        baseline_status: dict[str, str],
        run_id: str,
        attempt: int,
    ) -> tuple[list[NormalizedGate], bool]:
        results: list[NormalizedGate] = []
        regression = False
        runner = StandardGateRunner(self.runner, execution_environment)
        required_failed = False
        for spec in self._standard_specs(loaded):
            if required_failed:
                results.append(
                    self._store_gate(
                        run_id,
                        attempt,
                        NormalizedGate(
                            spec.gate,
                            True,
                            "skipped",
                            None,
                            0,
                            "Skipped by required-gate fail-fast policy.",
                        ),
                    )
                )
                continue
            output = self._output_dir(stage, f"candidate-{spec.gate}")
            outcome = runner.run(
                spec,
                workspace=workspace,
                evaluator_root=evaluator if spec.gate == "hidden_tests" else None,
                output_root=output,
            )
            inventory = self._inventory(output, spec.gate)
            difference = compare_inventories(
                baseline.get(spec.gate, TestInventory((), (), ())), inventory
            )
            difference["baseline_status"] = baseline_status.get(spec.gate)
            if difference["new_failures"]:
                regression = True
            gate = self._from_standard(
                spec.gate,
                outcome,
                inventory,
                difference=difference,
                private=spec.gate == "hidden_tests",
            )
            if spec.gate in {"visible_tests", "existing_tests", "hidden_tests"}:
                gate = self._require_candidate_test_evidence(
                    gate,
                    inventory,
                    baseline.get(spec.gate, TestInventory((), (), ())),
                )
            results.append(self._store_gate(run_id, attempt, gate))
            if baseline_status.get(spec.gate) == "passed" and not gate.passed:
                regression = True
            required_failed = not gate.passed

        if self.features.enable_hypothesis and loaded.task.property_profile is not None:
            if evaluator is None:
                raise VerificationServiceError(
                    "property verification requires an evaluator-owned profile"
                )
            if required_failed:
                results.append(
                    self._store_gate(
                        run_id,
                        attempt,
                        NormalizedGate(
                            "hypothesis_properties",
                            True,
                            "skipped",
                            None,
                            0,
                            "Skipped by required-gate fail-fast policy.",
                        ),
                    )
                )
            else:
                property_gate = self._run_property(
                    workspace,
                    loaded,
                    execution_environment,
                    evaluator,
                    stage,
                    baseline=False,
                )
                property_difference = dict(property_gate.baseline_difference or {})
                property_difference["baseline_status"] = baseline_status.get(
                    "hypothesis_properties"
                )
                property_gate = NormalizedGate(
                    **{
                        **asdict(property_gate),
                        "baseline_difference": property_difference,
                    }
                )
                results.append(self._store_gate(run_id, attempt, property_gate))
                if (
                    baseline_status.get("hypothesis_properties") == "passed"
                    and not property_gate.passed
                ):
                    regression = True
                required_failed = not property_gate.passed

        advisory = (
            StandardGateFactory.ruff(),
            StandardGateFactory.mypy(),
            StandardGateFactory.bandit(),
        )
        for spec in advisory:
            if required_failed:
                gate = NormalizedGate(
                    spec.gate, False, "skipped", None, 0, "Skipped after a required gate failed."
                )
            else:
                output = self._output_dir(stage, f"candidate-{spec.gate}")
                gate = self._from_standard(
                    spec.gate,
                    runner.run(spec, workspace=workspace, output_root=output),
                    TestInventory((), (), ()),
                    difference={"baseline_status": baseline_status.get(spec.gate)},
                )
            results.append(self._store_gate(run_id, attempt, gate))

        symbolic = (
            load_configured_symbolic_profile(loaded.benchmark_root, loaded.task.symbolic_profile)
            if self.features.enable_symbolic
            else None
        )
        if symbolic is not None:
            if required_failed:
                gate = NormalizedGate(
                    "symbolic", False, "skipped", None, 0, "Skipped after a required gate failed."
                )
            else:
                plan = build_symbolic_execution_plan(symbolic)
                output = self._output_dir(stage, "candidate-symbolic")
                execution = self.runner.run(
                    workspace=workspace,
                    execution_environment=execution_environment,
                    command=plan.argv,
                    timeout_seconds=plan.timeout_seconds,
                    output_root=output,
                    environment=plan.environment,
                )
                evaluation = normalize_symbolic_result(
                    exit_code=execution.exit_code,
                    duration_ms=execution.duration_ms,
                    timed_out=execution.timed_out,
                    stdout=execution.stdout,
                    stderr=execution.stderr,
                )
                gate = NormalizedGate(
                    "symbolic",
                    (
                        self.features.symbolic_counterexamples_actionable
                        and evaluation.status == "counterexample_found"
                    ),
                    evaluation.status,
                    evaluation.exit_code,
                    evaluation.duration_ms,
                    evaluation.summary,
                    {
                        "conclusion": evaluation.conclusion,
                        "proves_correctness": False,
                        "counterexamples": [asdict(item) for item in evaluation.counterexamples],
                    },
                )
            results.append(self._store_gate(run_id, attempt, gate))
        return results, regression

    def _skipped_candidate_gates(
        self, loaded: LoadedTaskDefinition, *, run_id: str, attempt: int
    ) -> list[NormalizedGate]:
        names: list[tuple[str, bool]] = [(spec.gate, True) for spec in self._standard_specs(loaded)]
        if self.features.enable_hypothesis and loaded.task.property_profile is not None:
            names.append(("hypothesis_properties", True))
        names.extend((name, False) for name in ("ruff", "mypy", "bandit"))
        if self.features.enable_symbolic and loaded.task.symbolic_profile is not None:
            names.append(("symbolic", False))
        return [
            self._store_gate(
                run_id,
                attempt,
                NormalizedGate(
                    name,
                    required,
                    "skipped",
                    None,
                    0,
                    "Skipped because the candidate patch did not apply.",
                ),
            )
            for name, required in names
        ]

    def _run_property(
        self,
        workspace: DisposableWorkspace,
        loaded: LoadedTaskDefinition,
        execution_environment: WindowsExecutionEnvironment,
        evaluator: Path,
        stage: Path,
        *,
        baseline: bool,
    ) -> NormalizedGate:
        assert loaded.task.property_profile is not None
        profile = load_property_profile(loaded.benchmark_root, loaded.task.property_profile)
        plan = build_property_execution_plan(profile)
        output = self._output_dir(stage, f"{'baseline' if baseline else 'candidate'}-property")
        execution = self.runner.run(
            workspace=workspace,
            execution_environment=execution_environment,
            command=plan.argv,
            timeout_seconds=plan.timeout_seconds,
            evaluator_root=evaluator,
            output_root=output,
            environment=plan.environment,
        )
        sidecar_path = output.joinpath(
            *plan.result_path.relative_to(PurePosixPath("/output")).parts
        )
        sidecar = _read_bounded_regular_file(sidecar_path, max_bytes=32 * 1024)
        try:
            evaluation: PropertyEvaluation = normalize_property_result(
                exit_code=execution.exit_code,
                duration_ms=execution.duration_ms,
                timed_out=execution.timed_out,
                sidecar=sidecar,
            )
        except ValueError:
            evaluation = PropertyEvaluation(
                "error" if baseline else "failed",
                execution.exit_code,
                execution.duration_ms,
                "Property result evidence was invalid or exceeded its bound.",
                (),
            )
        property_inventory = read_junit(
            output / "property-tests.xml", private_ids=True
        )
        if not property_inventory.evidence_valid or not property_inventory.all_tests:
            evaluation = PropertyEvaluation(
                "error" if baseline else "failed",
                execution.exit_code,
                execution.duration_ms,
                "Property test gate did not produce complete bounded JUnit evidence.",
                (),
            )
        evidence = {"counterexamples": [asdict(item) for item in evaluation.counterexamples]}
        return NormalizedGate(
            f"{'baseline_' if baseline else ''}hypothesis_properties",
            True,
            evaluation.status,
            evaluation.exit_code,
            evaluation.duration_ms,
            evaluation.summary,
            evidence,
        )

    def _apply_patch(self, workspace: LoadedTaskWorkspace, patch: PatchArtifact) -> NormalizedGate:
        try:
            run = self.session.get(Run, patch.run_id)
            if run is None:
                raise VerificationServiceError("patch run is missing")
            budgets_payload = run.model_parameters.get("agent_budgets", {})
            budgets = (
                AgentBudgets.model_validate(budgets_payload) if budgets_payload else AgentBudgets()
            )
            validation = PatchValidator(workspace.paths, budgets).validate(patch.unified_diff)
            apply_validated_patch(patch.unified_diff, validation, workspace.workspace.path)
        except (PatchValidationError, ValueError) as error:
            return NormalizedGate(
                "patch_applied", True, "failed", None, 0, f"Candidate patch was rejected: {error}"
            )
        return NormalizedGate(
            "patch_applied",
            True,
            "passed",
            0,
            0,
            "Candidate patch applied to a fresh disposable checkout.",
        )

    def _from_standard(
        self,
        name: str,
        outcome: GateOutcome,
        inventory: TestInventory,
        *,
        difference: dict[str, object] | None = None,
        private: bool = False,
    ) -> NormalizedGate:
        detail: dict[str, Any] = {
            "passed": len(inventory.passed),
            "failed": len(inventory.failed),
            "skipped": len(inventory.skipped),
        }
        if difference is not None:
            detail.update(difference)
        if outcome.status == "error":
            detail["infrastructure_error"] = True
        return NormalizedGate(
            name,
            outcome.required,
            outcome.status,
            outcome.exit_code,
            outcome.duration_ms,
            outcome.summary,
            detail,
        )

    def _inventory(self, output: Path, gate: str) -> TestInventory:
        names = {
            "visible_tests": "visible-tests.xml",
            "existing_tests": "existing-tests.xml",
            "hidden_tests": "hidden-tests.xml",
        }
        name = names.get(gate)
        return (
            TestInventory((), (), ())
            if name is None
            else read_junit(output / name, private_ids=gate == "hidden_tests")
        )

    @staticmethod
    def _require_baseline_test_evidence(
        gate: NormalizedGate, inventory: TestInventory
    ) -> NormalizedGate:
        if inventory.evidence_valid and inventory.all_tests:
            return gate
        detail = dict(gate.baseline_difference or {})
        detail["result_evidence"] = inventory.evidence_error or "no_tests_collected"
        return NormalizedGate(
            gate.gate,
            gate.required,
            "error",
            gate.exit_code,
            gate.duration_ms,
            "Baseline test gate did not produce complete bounded JUnit evidence.",
            detail,
            gate.artifact_reference,
        )

    @staticmethod
    def _require_candidate_test_evidence(
        gate: NormalizedGate,
        inventory: TestInventory,
        baseline: TestInventory,
    ) -> NormalizedGate:
        missing = baseline.all_tests - inventory.all_tests
        if inventory.evidence_valid and inventory.all_tests and not missing:
            return gate
        detail = dict(gate.baseline_difference or {})
        if not inventory.evidence_valid:
            detail["result_evidence"] = inventory.evidence_error or "invalid"
        elif not inventory.all_tests:
            detail["result_evidence"] = "no_tests_collected"
        if missing:
            detail["missing_tests"] = sorted(missing)
        return NormalizedGate(
            gate.gate,
            gate.required,
            "failed",
            gate.exit_code,
            gate.duration_ms,
            "Candidate test gate did not produce complete bounded JUnit evidence.",
            detail,
            gate.artifact_reference,
        )

    def _output_dir(self, stage: Path, name: str) -> Path:
        output = stage / name
        output.mkdir()
        return output

    def _store_gate(self, run_id: str, attempt: int, gate: NormalizedGate) -> NormalizedGate:
        payload = {"schema_version": 1, "run_id": run_id, "attempt_number": attempt, **asdict(gate)}
        reference = self.artifacts.store_text(
            run_id=run_id,
            kind="verification",
            text=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            suffix=".json",
        )
        stored = NormalizedGate(**{**asdict(gate), "artifact_reference": reference.relative_path})
        self.session.add(
            VerificationResult(
                run_id=run_id,
                attempt_number=attempt,
                gate=gate.gate,
                required=gate.required,
                status=gate.status,
                exit_code=gate.exit_code,
                duration_ms=gate.duration_ms,
                baseline_difference=gate.baseline_difference,
                summary=gate.summary,
                log_artifact=reference.relative_path,
            )
        )
        self.session.flush()
        return stored

    @staticmethod
    def _failure_category(results: list[NormalizedGate], regression: bool) -> str:
        failed = next(
            (
                item
                for item in results
                if item.required and not item.passed and not item.gate.startswith("baseline_")
            ),
            None,
        )
        if failed is None:
            return "INFRASTRUCTURE_FAILURE"
        if failed.status == "timed_out":
            return "TIMEOUT"
        if regression:
            return "REGRESSION"
        return {
            "patch_applied": "PATCH_DID_NOT_APPLY",
            "python_compile": "INVALID_PATCH",
            "visible_tests": "VISIBLE_TEST_FAILURE",
            "existing_tests": "REGRESSION",
            "hidden_tests": "HIDDEN_TEST_FAILURE",
            "hypothesis_properties": "HYPOTHESIS_COUNTEREXAMPLE",
        }.get(failed.gate, "INFRASTRUCTURE_FAILURE")


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _read_bounded_regular_file(path: Path, *, max_bytes: int) -> bytes | None:
    if _is_link_like(path) or not path.is_file():
        return None
    try:
        with path.open("rb") as stream:
            return stream.read(max_bytes + 1)
    except OSError:
        return None
