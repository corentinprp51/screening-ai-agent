import pytest

from app.domain.flow import Ask, Close, FollowUp, Greet, Recap, next_action, stage_of
from app.domain.models import (
    CandidateState,
    ClientConfig,
    FieldConfig,
    FieldState,
    Persona,
    Status,
    Templates,
)

CONFIG = ClientConfig(
    client_id="test",
    persona=Persona(agent_name="Lucía", client_name="Test"),
    default_language="es",
    fields=[FieldConfig(type="name"), FieldConfig(type="schedule")],
    templates=Templates(
        greeting={"es": "hola", "en": "hi"},
        fallback={"es": "perdona", "en": "sorry"},
        after_close={"es": "gracias", "en": "thanks"},
    ),
)
VALID_NAME = FieldState(status="valid", value="Ana López")
VALID_SCHEDULE = FieldState(status="valid", value="evening")
NEEDS_REVIEW = FieldState(status="needs_review", attempts=3)
RECAP = Recap(fields=("name", "schedule"))


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (CandidateState(), Greet()),
        (CandidateState(consent=False), Close(status=None, reason="consent_declined")),
        (CandidateState(consent=True), Ask("name", attempt=0)),
        (
            CandidateState(consent=True, fields={"name": FieldState(attempts=1)}),
            Ask("name", attempt=1),
        ),
        (
            CandidateState(
                consent=True,
                fields={"name": FieldState(status="incomplete", value="Ana", missing="surname")},
            ),
            FollowUp("name", missing="surname"),
        ),
        (CandidateState(consent=True, fields={"name": VALID_NAME}), Ask("schedule", attempt=0)),
        (
            CandidateState(consent=True, fields={"schedule": VALID_SCHEDULE}),
            Ask("name", attempt=0),
        ),
        (
            CandidateState(consent=True, fields={"name": NEEDS_REVIEW}),
            Ask("schedule", attempt=0),
        ),
        (
            CandidateState(consent=True, fields={"name": VALID_NAME, "schedule": VALID_SCHEDULE}),
            RECAP,
        ),
        (
            CandidateState(
                consent=True,
                fields={"name": VALID_NAME, "schedule": VALID_SCHEDULE},
                recap_confirmed=True,
            ),
            Close(status=Status.QUALIFIED),
        ),
        (
            CandidateState(
                consent=True,
                fields={"name": VALID_NAME, "schedule": NEEDS_REVIEW},
                recap_confirmed=True,
            ),
            Close(status=Status.QUALIFIED_TO_REVIEW),
        ),
        (
            CandidateState(consent=True, opted_out=True, fields={"name": VALID_NAME}),
            Close(status=Status.WITHDRAWN),
        ),
        (
            CandidateState(
                consent=True,
                opted_out=True,
                fields={"name": VALID_NAME, "schedule": VALID_SCHEDULE},
                recap_confirmed=True,
            ),
            Close(status=Status.WITHDRAWN),
        ),
    ],
)
def test_next_action(state, expected):
    assert next_action(state, CONFIG) == expected


@pytest.mark.parametrize(
    ("action", "stage"),
    [
        (Greet(), "consent"),
        (Ask("name", attempt=0), "name"),
        (FollowUp("name", missing="surname"), "name"),
        (RECAP, "recap"),
        (Close(status=Status.QUALIFIED), "closed"),
    ],
)
def test_stage_of(action, stage):
    assert stage_of(action) == stage
