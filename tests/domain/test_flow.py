import pytest

from app.domain.flow import Ask, Close, FollowUp, Greet, Recap, next_action, stage_of
from app.domain.models import (
    CandidateState,
    ClientConfig,
    FieldConfig,
    FieldState,
    License,
    OwnVehicle,
    Persona,
    Status,
    Templates,
)

CONFIG = ClientConfig(
    client_id="test",
    persona=Persona(agent_name="Lucía", client_name="Test"),
    default_language="es",
    fields=[FieldConfig(type="name"), FieldConfig(type="schedule")],
    review_delay_hours=24,
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


KNOCK_OUT_CONFIG = CONFIG.model_copy(
    update={
        "fields": [
            FieldConfig(type="name"),
            FieldConfig(type="license", knock_out=True),
            FieldConfig(type="own_vehicle", knock_out=True),
            FieldConfig(type="schedule"),
        ]
    }
)
NO_LICENSE = FieldState(status="valid", value=License(has_license=False))
LICENSE = FieldState(status="valid", value=License(has_license=True))
NO_VEHICLE = FieldState(status="valid", value=OwnVehicle(owns_vehicle=False))
SHARED_VEHICLE = FieldState(
    status="needs_review", value=OwnVehicle(owns_vehicle="shared"), missing=None
)
LICENSE_KNOCK_OUT = Close(
    status=Status.REJECTION_PROPOSED, reason="no_license", field="license", within_hours=24
)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"name": VALID_NAME}, Ask("license", attempt=0)),
        ({"name": VALID_NAME, "license": NO_LICENSE}, LICENSE_KNOCK_OUT),
        ({"license": NO_LICENSE}, LICENSE_KNOCK_OUT),
        (
            {"name": VALID_NAME, "license": LICENSE, "own_vehicle": NO_VEHICLE},
            Close(
                status=Status.REJECTION_PROPOSED,
                reason="no_own_vehicle",
                field="own_vehicle",
                within_hours=24,
            ),
        ),
        (
            {"name": VALID_NAME, "license": NEEDS_REVIEW, "own_vehicle": SHARED_VEHICLE},
            Ask("schedule", attempt=0),
        ),
        (
            {"name": VALID_NAME, "schedule": VALID_SCHEDULE, "license": LICENSE},
            Ask("own_vehicle", attempt=0),
        ),
    ],
)
def test_next_action_checks_the_knock_outs_before_walking_the_fields(fields, expected):
    state = CandidateState(consent=True, fields=fields)
    assert next_action(state, KNOCK_OUT_CONFIG) == expected


def test_a_no_on_a_field_without_the_knock_out_flag_does_not_propose_a_rejection():
    config = CONFIG.model_copy(
        update={"fields": [FieldConfig(type="license"), FieldConfig(type="schedule")]}
    )
    state = CandidateState(consent=True, fields={"license": NO_LICENSE})
    assert next_action(state, config) == Ask("schedule", attempt=0)


@pytest.mark.parametrize(
    ("action", "stage"),
    [
        (Greet(), "consent"),
        (Ask("name", attempt=0), "name"),
        (FollowUp("name", missing="surname"), "name"),
        (RECAP, "recap"),
        (Close(status=Status.QUALIFIED), "closed"),
        (LICENSE_KNOCK_OUT, "closed"),
    ],
)
def test_stage_of(action, stage):
    assert stage_of(action) == stage
