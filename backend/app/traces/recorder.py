"""Canonical ordered TraceEvent persistence."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Run, TraceEvent

from .models import TraceOperation
from .redaction import TraceRedactor


class TraceRecordingError(RuntimeError):
    """The requested run cannot accept a trace event."""


class TraceRecorder:
    """Append redacted, causally ordered observable events for one run."""

    def __init__(
        self,
        session: Session,
        run_id: str,
        *,
        redactor: TraceRedactor | None = None,
    ) -> None:
        if session.get(Run, run_id) is None:
            raise TraceRecordingError(f"run does not exist: {run_id}")
        self.session = session
        self.run_id = run_id
        self.redactor = redactor or TraceRedactor(max_text_characters=2_000)

    def record(
        self,
        operation: TraceOperation,
        status: str,
        *,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        error_type: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        parent_event_id: str | None = None,
    ) -> TraceEvent:
        sequence = self._next_sequence()
        previous = self.session.scalar(
            select(TraceEvent)
            .where(TraceEvent.run_id == self.run_id)
            .order_by(TraceEvent.sequence_number.desc())
            .limit(1)
        )
        parent = parent_event_id if parent_event_id is not None else (
            previous.event_id if previous is not None else None
        )
        start = started_at or datetime.now(UTC)
        end = finished_at or start
        event_id = _event_id(self.run_id, sequence, operation)
        record = TraceEvent(
            event_id=event_id,
            run_id=self.run_id,
            sequence_number=sequence,
            parent_event_id=parent,
            operation=operation.value,
            started_at=start,
            finished_at=end,
            status=self.redactor.redact_text(status),
            input_summary=self.redactor.summary(input),
            output_summary=self.redactor.summary(output),
            error_type=(self.redactor.redact_text(error_type) if error_type else None),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def _next_sequence(self) -> int:
        latest = self.session.scalar(
            select(func.max(TraceEvent.sequence_number)).where(TraceEvent.run_id == self.run_id)
        )
        return 0 if latest is None else latest + 1


def _event_id(run_id: str, sequence: int, operation: TraceOperation) -> str:
    digest = hashlib.sha256(f"{run_id}:{sequence}:{operation.value}".encode()).hexdigest()[:24]
    return f"trace-{digest}"
