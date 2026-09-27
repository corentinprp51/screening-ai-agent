"""Re-engaging a silent candidate: the Nudge or the deadline that is due, counted from their
last unanswered question. A Nudge does not restart the delays."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.models import CandidateState, ClientConfig


@dataclass(frozen=True)
class Nudge:
    number: int  # from 1, the index of its delay and template


@dataclass(frozen=True)
class Deadline:
    """The screening ends: Abandoned, Qualified to review, or erased before consent."""


def due_step(
    asked_at: datetime, last_nudge: int, now: datetime, config: ClientConfig
) -> Nudge | Deadline | None:
    """`last_nudge` is the number of the last Nudge sent since the question (0 for none).
    After a long gap only the latest due Nudge is sent: the missed ones are skipped."""
    elapsed = now - asked_at
    if elapsed >= timedelta(hours=config.deadline_hours):
        return Deadline()
    due = sum(elapsed >= timedelta(hours=hours) for hours in config.nudge_delays_hours)
    return Nudge(due) if due > last_nudge else None


def questions_left(state: CandidateState, config: ClientConfig) -> int:
    """The fields still to answer or confirm, plus the recap."""
    open_fields = sum(
        state.field(field_config.type).status in ("empty", "incomplete")
        or state.field(field_config.type).unconfirmed is not None
        for field_config in config.fields
    )
    return open_fields + (not state.recap_confirmed)
