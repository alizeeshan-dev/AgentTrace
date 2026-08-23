"""Configuration D composition over persisted SBFL and bounded CEGIS.

This module deliberately does not collect coverage.  Configuration D consumes
pre-computed, persisted fault-localization evidence so repository code remains
outside the agent process and experiment preparation stays reproducible.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.budgets import AgentBudgets
from app.agent.provider import ModelMessage, ModelProvider, ModelRequest, ModelResponse
from app.cegis.counterexamples import CounterexampleExtractor
from app.cegis.service import ConfigurationCResult, ConfigurationCService, VerificationOracle
from app.config import Settings
from app.db.models import FaultLocalizationResult, Run
from app.fault_localization import localization_run_id as deterministic_localization_run_id
from app.tasks import LoadedTaskDefinition, load_task_definition
from app.verification.service import VerificationFeatures, VerificationService

from .service import ConfigurationExecution

ConfigurationDCondition = Literal["D", "D1", "D2", "D3"]
_MAX_SBFL_TOP_K = 50
_MAX_SBFL_EVIDENCE_CHARS = 8_000


class ConfigurationDError(RuntimeError):
    """Configuration D cannot be executed under its declared evidence contract."""


class TechniqueSelection(Protocol):
    """Structural protocol implemented by the shared ablation configuration."""

    enable_sbfl: bool
    enable_hypothesis: bool
    enable_crosshair: bool


@dataclass(frozen=True, slots=True)
class EffectiveTechniques:
    """Requested and task-applicable research interventions for one D run."""

    sbfl: bool
    hypothesis: bool
    crosshair: bool


@dataclass(frozen=True, slots=True)
class FaultLocalizationEvidence:
    """Bounded, agent-safe projection of a persisted Ochiai result."""

    source_run_id: str
    metric: str
    entries: tuple[dict[str, JsonValue], ...]
    rendered: str


@dataclass(frozen=True, slots=True)
class ConfigurationDResult:
    """Configuration C result plus the research evidence actually enabled."""

    configuration_id: Literal["D"]
    condition: ConfigurationDCondition
    cegis: ConfigurationCResult
    requested_techniques: EffectiveTechniques
    effective_techniques: EffectiveTechniques
    fault_localization: FaultLocalizationEvidence | None


class EvidenceAugmentingProvider:
    """Inject category-labelled D evidence without changing provider adapters."""

    def __init__(
        self,
        delegate: ModelProvider,
        *,
        condition: ConfigurationDCondition,
        techniques: EffectiveTechniques,
        fault_localization: FaultLocalizationEvidence | None,
    ) -> None:
        self.delegate = delegate
        self.provider_name = delegate.provider_name
        self.condition = condition
        self.techniques = techniques
        self.fault_localization = fault_localization
        self._initial_request_seen = False

    def generate(self, request: ModelRequest) -> ModelResponse:
        stage = request.metadata.get("stage")
        is_repair = stage == "repair"
        if not self._initial_request_seen and not is_repair:
            request = self._initial_request(request)
            self._initial_request_seen = True
        elif is_repair:
            request = self._repair_request(request)
        else:
            request = self._metadata(request, stage="initial-exploration")
        return self.delegate.generate(request)

    def _initial_request(self, request: ModelRequest) -> ModelRequest:
        messages = list(request.messages)
        if not messages or messages[0].role != "system":
            raise ConfigurationDError("the tool-agent request has no system contract")
        messages[0] = ModelMessage(
            role="system",
            content=(
                f"Configuration {self.condition}: return exactly one structured action "
                "per turn. You may use only list_tree, read_file, and search_code, then finish "
                "with one submit_patch. No shell or hidden-test access is available. Verification "
                "feedback is unavailable before the initial candidate. At most one complete "
                "replacement patch may be requested later. Fault-localization rankings, when "
                "present, are probabilistic evidence and are not guaranteed fault truth; you may "
                "inspect any repository file permitted by the repository tools."
            ),
        )
        if self.fault_localization is not None:
            messages.append(
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "category": "FAULT LOCALIZATION EVIDENCE",
                            "interpretation": (
                                "Probabilistic suspiciousness ranking only; not guaranteed truth."
                            ),
                            "metric": self.fault_localization.metric,
                            "ranking": list(self.fault_localization.entries),
                            "rendered_summary": self.fault_localization.rendered,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        return request.model_copy(
            update={
                "messages": messages,
                "metadata": self._metadata_values(request.metadata, "initial-exploration"),
            }
        )

    def _repair_request(self, request: ModelRequest) -> ModelRequest:
        messages = list(request.messages)
        if not messages or messages[0].role != "system":
            raise ConfigurationDError("the repair request has no system contract")
        messages[0] = ModelMessage(
            role="system",
            content=(
                f"Configuration {self.condition} repair: return one structured action per "
                "turn. You may use only list_tree, read_file, and search_code, then submit exactly "
                "one complete replacement unified diff against the original base commit. The "
                "replacement must not be incremental against P0. No further repair is permitted. "
                "Concrete verification evidence is bounded and may still be incomplete; advisory "
                "warnings are not guaranteed defect truth."
            ),
        )
        for index, message in enumerate(messages[1:], start=1):
            if message.role != "user":
                continue
            try:
                original_payload = json.loads(message.content)
            except json.JSONDecodeError as error:
                raise ConfigurationDError("repair evidence is not structured JSON") from error
            if not isinstance(original_payload, dict):
                raise ConfigurationDError("repair evidence must be a structured object")
            messages[index] = ModelMessage(
                role="user",
                content=json.dumps(
                    {
                        "task": original_payload.get("task"),
                        "failed_candidate": original_payload.get("failed_candidate"),
                        "DETERMINISTIC VERIFICATION EVIDENCE": {
                            "counterexample": original_payload.get("counterexample"),
                            "interpretation": (
                                "Concrete bounded failure evidence; it may be incomplete and is "
                                "not a proof of the unique underlying defect."
                            ),
                        },
                        "ADVISORY WARNINGS": {
                            "included_in_repair_feedback": False,
                            "interpretation": (
                                "Advisory checks are recorded separately and are not ground truth."
                            ),
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            break
        if self.fault_localization is not None:
            messages.append(
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "category": "FAULT LOCALIZATION EVIDENCE",
                            "interpretation": (
                                "Probabilistic suspiciousness ranking only; not guaranteed truth."
                            ),
                            "metric": self.fault_localization.metric,
                            "ranking": list(self.fault_localization.entries),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            )
        return request.model_copy(
            update={
                "messages": messages,
                "metadata": self._metadata_values(request.metadata, "repair"),
            }
        )

    def _metadata(self, request: ModelRequest, *, stage: str) -> ModelRequest:
        return request.model_copy(
            update={"metadata": self._metadata_values(request.metadata, stage)}
        )

    def _metadata_values(self, original: dict[str, JsonValue], stage: str) -> dict[str, JsonValue]:
        return {
            **original,
            "configuration_id": "D",
            "condition": self.condition,
            "phase": 8,
            "stage": stage,
            "research_techniques": asdict(self.techniques),
        }


class ConfigurationDService:
    """Compose SBFL evidence with the existing one-repair CEGIS service."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider: ModelProvider,
        verifier: VerificationOracle | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider
        self.verifier = verifier

    def run(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        model_identifier: str,
        techniques: TechniqueSelection,
        condition: ConfigurationDCondition = "D",
        model_parameters: dict[str, JsonValue] | None = None,
        budgets: AgentBudgets | None = None,
        benchmark_root: str | Path | None = None,
        localization_run_id: str | None = None,
        sbfl_top_k: int = 10,
    ) -> ConfigurationDResult:
        """Run enhanced CEGIS once; a configured verifier controls optional gates."""

        if isinstance(sbfl_top_k, bool) or not 1 <= sbfl_top_k <= _MAX_SBFL_TOP_K:
            raise ValueError(f"sbfl_top_k must be between 1 and {_MAX_SBFL_TOP_K}")
        loaded = load_task_definition(manifest_path, benchmark_root=benchmark_root)
        requested = EffectiveTechniques(
            sbfl=techniques.enable_sbfl,
            hypothesis=techniques.enable_hypothesis,
            crosshair=techniques.enable_crosshair,
        )
        evidence: FaultLocalizationEvidence | None = None
        if requested.sbfl:
            try:
                evidence = self._load_fault_localization(
                    loaded,
                    localization_run_id=localization_run_id,
                    top_k=sbfl_top_k,
                )
            except ConfigurationDError:
                if loaded.task.task_source != "external":
                    raise
        effective = EffectiveTechniques(
            sbfl=requested.sbfl and evidence is not None,
            hypothesis=requested.hypothesis and loaded.task.property_profile is not None,
            crosshair=requested.crosshair and loaded.task.symbolic_profile is not None,
        )
        enhanced_provider = EvidenceAugmentingProvider(
            self.provider,
            condition=condition,
            techniques=effective,
            fault_localization=evidence,
        )
        verifier = self.verifier or VerificationService(
            self.session,
            settings=self.settings,
            features=VerificationFeatures(
                enable_hypothesis=effective.hypothesis,
                enable_symbolic=effective.crosshair,
                symbolic_counterexamples_actionable=effective.crosshair,
            ),
        )
        result = ConfigurationCService(
            self.session,
            settings=self.settings,
            provider=enhanced_provider,
            verifier=verifier,
            extractor=CounterexampleExtractor(self.session),
        ).run(
            manifest_path,
            run_id=run_id,
            model_identifier=model_identifier,
            model_parameters=model_parameters or {},
            budgets=budgets,
            benchmark_root=benchmark_root,
        )
        run = self.session.get(Run, run_id)
        if run is None:
            raise ConfigurationDError("the enhanced run was not persisted")
        run.configuration_id = "D"
        parameters = dict(run.model_parameters)
        parameters.update(
            {
                "phase": 8,
                "protocol_version": "configuration-d-enhanced-cegis-v1",
                "condition": condition,
                "requested_research_techniques": asdict(requested),
                "effective_research_techniques": asdict(effective),
                "sbfl_evidence": (
                    {
                        "metric": evidence.metric,
                        "source_run_id": evidence.source_run_id,
                        "top_k": len(evidence.entries),
                    }
                    if evidence is not None
                    else None
                ),
                "unavailable_research_evidence": {
                    "sbfl": (
                        "No applicable persisted execution spectrum was available."
                        if requested.sbfl and evidence is None
                        else None
                    ),
                    "hypothesis": (
                        "No property profile is configured for this task."
                        if requested.hypothesis and not effective.hypothesis
                        else None
                    ),
                    "crosshair": (
                        "No symbolic profile is configured for this task."
                        if requested.crosshair and not effective.crosshair
                        else None
                    ),
                },
            }
        )
        run.model_parameters = parameters
        self.session.flush()
        return ConfigurationDResult(
            "D",
            condition,
            result,
            requested,
            effective,
            evidence,
        )

    def _load_fault_localization(
        self,
        loaded: LoadedTaskDefinition,
        *,
        localization_run_id: str | None,
        top_k: int,
    ) -> FaultLocalizationEvidence:
        selected_run_id = localization_run_id or deterministic_localization_run_id(
            loaded.task.task_id, loaded.task.base_commit
        )
        statement = (
            select(FaultLocalizationResult, Run)
            .join(Run, Run.run_id == FaultLocalizationResult.run_id)
            .where(Run.task_id == loaded.task.task_id)
            .where(FaultLocalizationResult.run_id == selected_run_id)
        )
        row = self.session.execute(statement).first()
        if row is None:
            raise ConfigurationDError(
                "SBFL is enabled but no persisted localization exists for this task"
            )
        record, source_run = row
        if (
            source_run.task_id != loaded.task.task_id
            or source_run.status != "localized"
            or source_run.finished_at is None
            or record.metric.casefold() != "ochiai"
        ):
            raise ConfigurationDError("fault-localization evidence does not match the task")
        entries = _safe_localization_entries(record.ranked_locations, loaded, top_k=top_k)
        if not entries:
            raise ConfigurationDError("persisted SBFL evidence has no safe ranked locations")
        rendered = _render_localization(entries)
        if len(rendered) > _MAX_SBFL_EVIDENCE_CHARS:
            raise ConfigurationDError("fault-localization evidence exceeds its content bound")
        return FaultLocalizationEvidence(record.run_id, "ochiai", entries, rendered)


class ConfigurationDExecutor:
    """Adapter implementing the common A/B/C/D execution interface."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        provider: ModelProvider,
    ) -> None:
        self.session = session
        self.settings = settings
        self.provider = provider

    def execute(self, execution: ConfigurationExecution) -> ConfigurationDResult:
        """Resolve D's gate switches per run and delegate to the shared CEGIS loop."""

        if execution.configuration.configuration_id != "D":
            raise ConfigurationDError("ConfigurationDExecutor accepts only D conditions")
        if execution.configuration.repair_allowance != 1:
            raise ConfigurationDError("Configuration D requires exactly one repair allowance")
        effective = execution.research_techniques.effective
        verifier = VerificationService(
            self.session,
            settings=self.settings,
            features=VerificationFeatures(
                enable_hypothesis=effective.enable_hypothesis,
                enable_symbolic=effective.enable_crosshair,
                symbolic_counterexamples_actionable=effective.enable_crosshair,
            ),
        )
        service = ConfigurationDService(
            self.session,
            settings=self.settings,
            provider=self.provider,
            verifier=verifier,
        )
        return service.run(
            execution.manifest_path,
            run_id=execution.run_id,
            model_identifier=execution.model.model,
            techniques=effective,
            condition=cast(ConfigurationDCondition, execution.configuration.condition.value),
            model_parameters=execution.provider_parameters,
            budgets=execution.budgets,
            benchmark_root=execution.benchmark_root,
        )


def _safe_localization_entries(
    raw: Sequence[dict[str, Any]],
    loaded: LoadedTaskDefinition,
    *,
    top_k: int,
) -> tuple[dict[str, JsonValue], ...]:
    entries: list[dict[str, JsonValue]] = []
    for expected_rank, item in enumerate(raw[:top_k], start=1):
        if not isinstance(item, dict):
            raise ConfigurationDError("persisted SBFL ranking is malformed")
        rank = item.get("rank")
        file = item.get("file")
        line = item.get("line")
        score = item.get("ochiai")
        symbol = item.get("symbol")
        if (
            isinstance(rank, bool)
            or rank != expected_rank
            or not isinstance(file, str)
            or isinstance(line, bool)
            or not isinstance(line, int)
            or line < 1
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or not 0.0 <= float(score) <= 1.0
            or (symbol is not None and (not isinstance(symbol, str) or len(symbol) > 200))
        ):
            raise ConfigurationDError("persisted SBFL ranking is malformed")
        normalized = _safe_relative_path(file)
        if not _is_allowed_source(normalized, loaded.task.allowed_paths):
            raise ConfigurationDError("SBFL ranking contains a location outside allowed sources")
        entries.append(
            {
                "rank": rank,
                "file": normalized,
                "line": line,
                "symbol": symbol,
                "ochiai": round(float(score), 6),
            }
        )
    return tuple(entries)


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            part.casefold() in {".git", "hidden_tests", ".agenttrace-evaluator"}
            for part in path.parts
        )
    ):
        raise ConfigurationDError("SBFL ranking contains an unsafe source path")
    return path.as_posix()


def _is_allowed_source(path: str, allowed_paths: Sequence[str]) -> bool:
    return any(
        path == entry.rstrip("/")
        or (entry.endswith("/") and path.startswith(f"{entry.rstrip('/')}/"))
        for entry in allowed_paths
    )


def _render_localization(entries: Sequence[dict[str, JsonValue]]) -> str:
    lines = ["FAULT LOCALIZATION EVIDENCE — probabilistic; not guaranteed fault truth"]
    for item in entries:
        symbol = f" [{item['symbol']}]" if item["symbol"] else ""
        lines.append(
            f"{item['rank']}. {item['file']}:{item['line']}{symbol} — "
            f"Ochiai {float(str(item['ochiai'])):.6f}"
        )
    return "\n".join(lines)
