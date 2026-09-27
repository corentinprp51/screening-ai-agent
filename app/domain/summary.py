"""The recruiter handoff: the next action and the facts the summary is phrased from.
Code decides both; the LLM only phrases the facts."""

from pydantic import JsonValue

from app.domain.fields import format_value
from app.domain.flow import Close, next_action
from app.domain.models import CandidateState, ClientConfig, Status

# The statuses where the questions stop and a summary is written. Rejected is not one:
# it follows a recruiter's confirmation, who has already read the summary.
SUMMARIZED_STATUSES = {
    Status.QUALIFIED,
    Status.QUALIFIED_TO_REVIEW,
    Status.REJECTION_PROPOSED,
    Status.WITHDRAWN,
}


def recruiter_action(status: Status, state: CandidateState, config: ClientConfig) -> str | None:
    """What the recruiter should do next, or None when nothing is waiting on them."""
    match status:
        case Status.QUALIFIED:
            return f"Call within {config.call_within_hours} h"
        case Status.QUALIFIED_TO_REVIEW:
            fields = needs_review_fields(state, config)
            if fields:
                return "Check the needs-review fields: " + ", ".join(fields)
            return "Check the answers: the recap was not confirmed"
        case Status.REJECTION_PROPOSED:
            return "Confirm or override the proposed rejection"
        case Status.IN_PROGRESS if "wants_human" in state.flags:
            return "Call: the candidate asked for it"
        case _:
            return None


def needs_review_fields(state: CandidateState, config: ClientConfig) -> list[str]:
    return [
        field_config.type
        for field_config in config.fields
        if state.field(field_config.type).status == "needs_review"
    ]


def summary_facts(
    status: Status, state: CandidateState, config: ClientConfig
) -> dict[str, JsonValue]:
    """Everything the summary may say: the valid field values, the fields to review, the
    flags, the status, the failed rule for a proposed rejection, and the next action."""
    facts: dict[str, JsonValue] = {
        "status": status.value,
        "fields": {
            field_config.type: format_value(state.field(field_config.type).value)
            for field_config in config.fields
            if state.field(field_config.type).status == "valid"
        },
        "needs_review": needs_review_fields(state, config),
        "flags": state.all_flags(),
        "next_action": recruiter_action(status, state, config),
    }
    proposal = next_action(state, config)
    if status == Status.REJECTION_PROPOSED and isinstance(proposal, Close):
        facts["rule"] = proposal.reason
    return facts
