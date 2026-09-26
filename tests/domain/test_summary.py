import pytest

from app.domain.models import CandidateState, FieldState, License, Status
from app.domain.summary import recruiter_action, summary_facts
from tests.domain.test_flow import KNOCK_OUT_CONFIG, NEEDS_REVIEW, VALID_NAME

NO_LICENSE = FieldState(status="valid", value=License(has_license=False), raw_answer="no")


@pytest.mark.parametrize(
    ("status", "fields", "expected"),
    [
        (Status.QUALIFIED, {}, "Call within 48 h"),
        (
            Status.QUALIFIED_TO_REVIEW,
            {"license": NEEDS_REVIEW, "schedule": NEEDS_REVIEW},
            "Check the needs-review fields: license, schedule",
        ),
        (Status.QUALIFIED_TO_REVIEW, {}, "Check the answers: the recap was not confirmed"),
        (Status.REJECTION_PROPOSED, {}, "Confirm or override the proposed rejection"),
        (Status.IN_PROGRESS, {}, None),
        (Status.REJECTED, {}, None),
        (Status.WITHDRAWN, {}, None),
    ],
)
def test_recruiter_action(status, fields, expected):
    state = CandidateState(consent=True, fields=fields)
    assert recruiter_action(status, state, KNOCK_OUT_CONFIG) == expected


def test_summary_facts_hold_the_valid_values_the_fields_to_review_and_the_failed_rule():
    state = CandidateState(
        consent=True,
        fields={"name": VALID_NAME, "license": NO_LICENSE, "schedule": NEEDS_REVIEW},
        flags=["message_after_close"],
    )

    facts = summary_facts(Status.REJECTION_PROPOSED, state, KNOCK_OUT_CONFIG)

    assert facts == {
        "status": "rejection_proposed",
        "fields": {"name": "Ana López", "license": "no"},
        "needs_review": ["schedule"],
        "flags": ["message_after_close"],
        "next_action": "Confirm or override the proposed rejection",
        "rule": "no_license",
    }
