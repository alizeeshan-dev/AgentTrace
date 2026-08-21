"""Explicit execution budgets shared by the Phase 5 agent configurations."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from pydantic import Field

from app.schemas.common import ResearchSchema


class BudgetExhausted(RuntimeError):
    """Raised before an action would cross a configured experiment budget."""

    def __init__(self, budget: str) -> None:
        self.budget = budget
        super().__init__(f"Agent budget exhausted: {budget}")


class AgentBudgets(ResearchSchema):
    """Small, deterministic limits for model, tool, context, and patch use."""

    max_model_turns: int = Field(default=8, ge=1, le=100)
    max_tool_calls: int = Field(default=6, ge=0, le=100)
    max_files_read: int = Field(default=8, ge=0, le=1_000)
    max_files_exposed: int = Field(default=30, ge=1, le=10_000)
    max_content_characters: int = Field(default=80_000, ge=1, le=10_000_000)
    max_file_bytes: int = Field(default=100_000, ge=1, le=10_000_000)
    max_tree_entries: int = Field(default=500, ge=1, le=10_000)
    max_search_matches: int = Field(default=100, ge=1, le=10_000)
    max_search_result_characters: int = Field(default=20_000, ge=1, le=1_000_000)
    max_patch_bytes: int = Field(default=50_000, ge=1, le=10_000_000)
    max_patch_lines: int = Field(default=1_000, ge=1, le=100_000)
    max_changed_files: int = Field(default=5, ge=1, le=1_000)
    wall_clock_seconds: float = Field(default=300.0, gt=0, le=86_400)


@dataclass(slots=True)
class BudgetTracker:
    """Track one run's resource consumption and reject over-budget actions."""

    limits: AgentBudgets
    started_at: float = field(default_factory=time.monotonic)
    model_turns: int = 0
    tool_calls: int = 0
    content_characters: int = 0
    lines_exposed: int = 0
    reserved_files_read: int = 0
    reserved_files_exposed: int = 0
    _files_read: set[str] = field(default_factory=set)
    _files_exposed: set[str] = field(default_factory=set)

    @property
    def files_read(self) -> int:
        return self.reserved_files_read + len(self._files_read)

    @property
    def files_exposed(self) -> int:
        return self.reserved_files_exposed + len(self._files_exposed)

    @classmethod
    def resumed(
        cls,
        limits: AgentBudgets,
        *,
        model_turns: int,
        tool_calls: int,
        files_read: int,
        files_exposed: int,
        content_characters: int,
        lines_exposed: int,
        started_at: float,
    ) -> BudgetTracker:
        """Resume cumulative accounting conservatively in a fresh workspace."""

        counters = (
            model_turns,
            tool_calls,
            files_read,
            files_exposed,
            content_characters,
            lines_exposed,
        )
        if any(value < 0 for value in counters):
            raise ValueError("resumed budget counters cannot be negative")
        return cls(
            limits=limits,
            started_at=started_at,
            model_turns=model_turns,
            tool_calls=tool_calls,
            content_characters=content_characters,
            lines_exposed=lines_exposed,
            reserved_files_read=files_read,
            reserved_files_exposed=files_exposed,
        )

    @property
    def remaining_content_characters(self) -> int:
        return self.limits.max_content_characters - self.content_characters

    @property
    def remaining_files_read(self) -> int:
        return self.limits.max_files_read - self.files_read

    @property
    def remaining_files_exposed(self) -> int:
        return self.limits.max_files_exposed - self.files_exposed

    def check_wall_clock(self) -> None:
        if time.monotonic() - self.started_at >= self.limits.wall_clock_seconds:
            raise BudgetExhausted("wall_clock_seconds")

    def begin_model_turn(self) -> None:
        self.check_wall_clock()
        if self.model_turns >= self.limits.max_model_turns:
            raise BudgetExhausted("max_model_turns")
        self.model_turns += 1

    def begin_tool_call(self) -> None:
        self.check_wall_clock()
        if self.tool_calls >= self.limits.max_tool_calls:
            raise BudgetExhausted("max_tool_calls")
        self.tool_calls += 1

    def is_file_exposed(self, path: str) -> bool:
        return path in self._files_exposed

    def is_file_read(self, path: str) -> bool:
        return path in self._files_read

    def record_exposure(
        self,
        content: str,
        *,
        paths: Iterable[str] = (),
        files_read: Iterable[str] = (),
    ) -> None:
        """Atomically account for exactly the content returned to the model."""

        self.check_wall_clock()
        exposed = set(paths)
        read = set(files_read)
        new_exposed = exposed - self._files_exposed
        new_read = read - self._files_read
        if len(new_exposed) > self.remaining_files_exposed:
            raise BudgetExhausted("max_files_exposed")
        if len(new_read) > self.remaining_files_read:
            raise BudgetExhausted("max_files_read")
        if len(content) > self.remaining_content_characters:
            raise BudgetExhausted("max_content_characters")

        self._files_exposed.update(exposed)
        self._files_read.update(read)
        self.content_characters += len(content)
        self.lines_exposed += len(content.splitlines())
