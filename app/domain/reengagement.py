"""Re-engaging a silent candidate: the Nudge or the deadline that is due, counted from their
last unanswered question. A Nudge does not restart the delays; a recruiter's Reopen does."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import takewhile

from app.domain.models import CandidateState, ClientConfig, Event, Message, Status


@dataclass(frozen=True)
class Nudge:
    number: int  # from 1, the index of its delay and template


@dataclass(frozen=True)
class Deadline:
    """The screening ends: Abandoned, Qualified to review, or erased before consent."""


@dataclass(frozen=True)
class Silence:
    asked_at: datetime  # the last unanswered question
    last_nudge: int  # the number of the last Nudge sent since (0 for none)


def silence(messages: list[Message], events: list[Event]) -> Silence | None:
    """The silence since the candidate's last message, None when they wrote last. The agent
    messages after it are the question (possibly after another message, e.g. a recruiter's
    override following a close, or the reopen message), then one per Nudge sent since. A
    Reopen starts a new silence: the Nudges sent before it no longer count."""
    unanswered = list(takewhile(lambda m: m.role == "agent", reversed(messages)))[::-1]
    if not unanswered:
        return None
    answered = messages[: -len(unanswered)]
    starts = [event.created_at for event in events if event.type == "reopened"]
    if answered:
        starts.append(answered[-1].created_at)
    start = max(starts, default=None)
    nudges = [
        event.payload["number"]
        for event in events
        if event.type == "nudge_sent" and (start is None or event.created_at > start)
    ]
    return Silence(unanswered[-len(nudges) - 1].created_at, max(nudges, default=0))


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


def silent_outcome(state: CandidateState, config: ClientConfig) -> Status:
    """At the deadline: every field answered (only the recap left) is Qualified to review,
    anything else Abandoned. A correction left unconfirmed keeps the valid value."""
    settled = all(
        state.field(field_config.type).status in ("valid", "needs_review")
        for field_config in config.fields
    )
    return Status.QUALIFIED_TO_REVIEW if settled else Status.ABANDONED


def questions_left(state: CandidateState, config: ClientConfig) -> int:
    """The fields still to answer or confirm, plus the recap."""
    open_fields = sum(
        state.field(field_config.type).status in ("empty", "incomplete")
        or state.field(field_config.type).unconfirmed is not None
        for field_config in config.fields
    )
    return open_fields + (not state.recap_confirmed)
