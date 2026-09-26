"""The flow: the next action is derived from the candidate state (ADR 0001)."""

from dataclasses import dataclass

from app.domain.fields import KNOCK_OUTS, MAX_ATTEMPTS
from app.domain.models import CandidateState, ClientConfig, Status


@dataclass(frozen=True)
class Greet:
    """The greeting, which carries the consent question."""


@dataclass(frozen=True)
class Ask:
    field: str
    attempt: int


@dataclass(frozen=True)
class FollowUp:
    field: str
    missing: str


@dataclass(frozen=True)
class Confirm:
    """Checks the field's pending value with the candidate."""

    field: str


@dataclass(frozen=True)
class Recap:
    """Lists every field, in config order."""

    fields: tuple[str, ...]


@dataclass(frozen=True)
class AskCorrection:
    """After a recap answered no without a correction: which answer should change?"""

    attempt: int


@dataclass(frozen=True)
class Close:
    """For a proposed rejection, `reason` is the failed rule, `field` the field that failed
    it and `within_hours` the delay in which a recruiter replies."""

    status: Status | None
    reason: str | None = None
    field: str | None = None
    within_hours: int | None = None


Action = Greet | Ask | FollowUp | Confirm | Recap | AskCorrection | Close


def next_action(state: CandidateState, config: ClientConfig) -> Action:
    """Consent → knock-outs → pending confirmations → fields in config order
    (needs-review fields are skipped) → recap → close. An opt-out after consent closes
    as Withdrawn from any stage. A knock-out a recruiter overrode is ignored from then on.
    A recap answered neither yes nor with a correction asks what to change; the last
    attempt leaves it unconfirmed."""
    if state.consent is None:
        return Greet()
    if state.consent is False:
        return Close(status=None, reason="consent_declined")
    if state.opted_out:
        return Close(status=Status.WITHDRAWN)
    for field_config in config.fields:
        field = state.field(field_config.type)
        if not (field_config.knock_out and field.status == "valid"):
            continue
        knock_out = KNOCK_OUTS[field_config.type]
        if knock_out.rule in state.overridden_knock_outs:
            continue
        if knock_out.fails(field.value):
            return Close(
                status=Status.REJECTION_PROPOSED,
                reason=knock_out.rule,
                field=field_config.type,
                within_hours=config.review_delay_hours,
            )
    for field_config in config.fields:
        if state.field(field_config.type).unconfirmed is not None:
            return Confirm(field_config.type)
    for field_config in config.fields:
        field = state.field(field_config.type)
        if field.status == "empty":
            return Ask(field_config.type, attempt=field.attempts)
        if field.status == "incomplete":
            return FollowUp(field_config.type, missing=field.missing)
    if not state.recap_confirmed:
        if state.recap_attempts == 0:
            return Recap(fields=tuple(field_config.type for field_config in config.fields))
        if state.recap_attempts < MAX_ATTEMPTS:
            return AskCorrection(attempt=state.recap_attempts)
    if not state.recap_confirmed or any(
        state.field(field_config.type).status == "needs_review" for field_config in config.fields
    ):
        return Close(status=Status.QUALIFIED_TO_REVIEW)
    return Close(status=Status.QUALIFIED)


def stage_of(action: Action) -> str:
    match action:
        case Greet():
            return "consent"
        case Ask(field=field) | FollowUp(field=field) | Confirm(field=field):
            return field
        case Recap() | AskCorrection():
            return "recap"
        case Close():
            return "closed"
