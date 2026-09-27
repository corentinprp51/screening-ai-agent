import pytest

from app.domain.flow import (
    Ask,
    AskCorrection,
    Close,
    Confirm,
    FollowUp,
    Greet,
    Recap,
    next_action,
    stage_of,
)
from app.domain.models import (
    CandidateState,
    ClientConfig,
    FieldConfig,
    FieldState,
    ImpactConfig,
    ImpactTargets,
    License,
    OpenShifts,
    OwnVehicle,
    Persona,
    ScoreWeights,
    Scoring,
    Status,
    Templates,
    TokenPrices,
)

CONFIG = ClientConfig(
    client_id="test",
    persona=Persona(agent_name="Lucía", client_name="Test"),
    default_language="es",
    fields=[FieldConfig(type="name"), FieldConfig(type="schedule")],
    review_delay_hours=24,
    call_within_hours=48,
    confidence_threshold=0.7,
    nudge_delays_hours=[1],
    deadline_hours=72,
    service_areas={"ES": {"Madrid": []}},
    platforms=["Glovo"],
    scoring=Scoring(
        weights=ScoreWeights(availability=30, schedule=20, start_date=30, experience=20),
        open_shifts=OpenShifts(availability=["full_time"], schedule=["evening"]),
    ),
    impact=ImpactConfig(
        currency="EUR",
        recruiters=1,
        calls_per_recruiter_per_day=15,
        working_days_per_week=5,
        call_minutes=15,
        unanswered_calls_per_candidate=1,
        no_answer_rate=0.6,
        unqualified_time_share=0.8,
        recruiter_hourly_cost=20,
        llm_price_per_million_tokens=TokenPrices(input=0.4, output=1.6),
        targets=ImpactTargets(
            completion_rate=0.6,
            first_message_minutes=5,
            qualified_time_share=0.8,
            needs_review_share=0.1,
        ),
    ),
    templates=Templates(
        greeting={"es": "hola", "en": "hi"},
        fallback={"es": "perdona", "en": "sorry"},
        after_close={"es": "gracias", "en": "thanks"},
        nudges={"es": ["¿seguimos?"], "en": ["shall we?"]},
        reopen={"es": "reabierta", "en": "reopened"},
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


@pytest.mark.parametrize(
    ("fields", "overridden", "expected"),
    [
        ({"name": VALID_NAME, "license": NO_LICENSE}, ["no_license"], Ask("own_vehicle", 0)),
        (
            {"name": VALID_NAME, "license": NO_LICENSE, "own_vehicle": NO_VEHICLE},
            ["no_license"],
            Close(
                status=Status.REJECTION_PROPOSED,
                reason="no_own_vehicle",
                field="own_vehicle",
                within_hours=24,
            ),
        ),
        (
            {"name": VALID_NAME, "license": NO_LICENSE, "own_vehicle": NO_VEHICLE},
            ["no_license", "no_own_vehicle"],
            Ask("schedule", 0),
        ),
    ],
)
def test_next_action_ignores_overridden_knock_outs(fields, overridden, expected):
    state = CandidateState(consent=True, fields=fields, overridden_knock_outs=overridden)
    assert next_action(state, KNOCK_OUT_CONFIG) == expected


UNSURE_NAME = FieldState(unconfirmed=VALID_NAME)
CORRECTED_SCHEDULE = VALID_SCHEDULE.model_copy(
    update={"unconfirmed": FieldState(status="valid", value="morning")}
)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"name": UNSURE_NAME}, Confirm("name")),
        ({"name": VALID_NAME, "schedule": CORRECTED_SCHEDULE}, Confirm("schedule")),
        # A value waiting for confirmation comes before the next empty field...
        ({"schedule": CORRECTED_SCHEDULE}, Confirm("schedule")),
        # ...but after the knock-outs.
        ({"license": NO_LICENSE, "schedule": CORRECTED_SCHEDULE}, LICENSE_KNOCK_OUT),
    ],
)
def test_next_action_confirms_a_value_waiting_for_confirmation(fields, expected):
    state = CandidateState(consent=True, fields=fields)
    assert next_action(state, KNOCK_OUT_CONFIG) == expected


@pytest.mark.parametrize(
    ("recap_attempts", "expected"),
    [
        (0, RECAP),
        (1, AskCorrection(attempt=1)),
        (2, AskCorrection(attempt=2)),
        (3, Close(status=Status.QUALIFIED_TO_REVIEW)),
    ],
)
def test_a_recap_answered_no_asks_what_to_change_until_the_last_attempt(recap_attempts, expected):
    state = CandidateState(
        consent=True,
        fields={"name": VALID_NAME, "schedule": VALID_SCHEDULE},
        recap_attempts=recap_attempts,
    )
    assert next_action(state, CONFIG) == expected


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
        (Confirm("name"), "name"),
        (AskCorrection(attempt=1), "recap"),
        (RECAP, "recap"),
        (Close(status=Status.QUALIFIED), "closed"),
        (LICENSE_KNOCK_OUT, "closed"),
    ],
)
def test_stage_of(action, stage):
    assert stage_of(action) == stage


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (CandidateState(abuse_count=1), Greet()),
        (CandidateState(abuse_count=2), Close(status=None, reason="consent_declined")),
        (CandidateState(consent=True, abuse_count=1), Ask("name", attempt=0)),
        (
            CandidateState(consent=True, abuse_count=2),
            Close(status=Status.REJECTION_PROPOSED, reason="abuse", within_hours=24),
        ),
        (
            CandidateState(
                consent=True,
                abuse_count=2,
                fields={"name": VALID_NAME, "schedule": VALID_SCHEDULE},
                recap_confirmed=True,
            ),
            Close(status=Status.REJECTION_PROPOSED, reason="abuse", within_hours=24),
        ),
        (
            CandidateState(consent=True, opted_out=True, abuse_count=2),
            Close(status=Status.WITHDRAWN),
        ),
    ],
)
def test_a_second_abuse_proposes_a_rejection_or_declines_the_consent(state, expected):
    assert next_action(state, CONFIG) == expected


def test_abuse_is_proposed_before_a_failed_knock_out_and_has_no_field():
    state = CandidateState(consent=True, abuse_count=2, fields={"license": NO_LICENSE})

    action = next_action(state, KNOCK_OUT_CONFIG)

    assert action == Close(status=Status.REJECTION_PROPOSED, reason="abuse", within_hours=24)
    assert action.field is None
