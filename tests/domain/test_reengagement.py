from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.config.yaml_loader import load_client_config
from app.domain.models import CandidateState, Event, FieldState, Message, Status
from app.domain.reengagement import (
    Deadline,
    Nudge,
    Silence,
    due_step,
    questions_left,
    silence,
    silent_outcome,
)

CONFIG = load_client_config("grupo_sazon")
ASKED_AT = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def step(hours: float, last_nudge: int = 0):
    return due_step(ASKED_AT, last_nudge, ASKED_AT + timedelta(hours=hours), CONFIG)


@pytest.mark.parametrize(
    ("hours", "last_nudge", "expected"),
    [
        (0.5, 0, None),
        (1, 0, Nudge(1)),
        (2, 1, None),
        (20, 1, Nudge(2)),
        (48, 2, Nudge(3)),
        (60, 3, None),
        (72, 3, Deadline()),
    ],
)
def test_each_nudge_is_due_at_its_delay_after_the_question(hours, last_nudge, expected):
    assert step(hours, last_nudge) == expected


def test_a_nudge_already_sent_is_never_due_again():
    assert step(19, last_nudge=1) is None


def test_after_a_long_silence_only_the_latest_nudge_is_due():
    assert step(30, last_nudge=0) == Nudge(2)


def test_the_deadline_wins_over_a_missed_nudge():
    assert step(80, last_nudge=1) == Deadline()


def test_questions_left_counts_the_open_fields_and_the_recap():
    state = CandidateState(
        consent=True,
        fields={
            "name": FieldState(status="valid", value="Ana"),
            "license": FieldState(status="needs_review"),
            "own_vehicle": FieldState(status="incomplete", missing="type"),
        },
    )

    # own_vehicle and the 5 empty fields, plus the recap
    assert questions_left(state, CONFIG) == 7


def test_only_the_recap_left_is_one_question():
    state = CandidateState(
        consent=True,
        fields={field.type: FieldState(status="valid") for field in CONFIG.fields},
    )

    assert questions_left(state, CONFIG) == 1


def message(role: str, hours: float) -> Message:
    return Message(
        role=role, content="…", language="es", created_at=ASKED_AT + timedelta(hours=hours)
    )


def nudge_event(number: int, hours: float) -> Event:
    return Event(
        type="nudge_sent",
        stage="name",
        payload={"number": number},
        created_at=ASKED_AT + timedelta(hours=hours),
    )


def test_the_silence_starts_at_the_question_before_the_nudges():
    messages = [message("agent", -1), message("candidate", 0), message("agent", 0)]
    messages += [message("agent", 1), message("agent", 20)]

    assert silence(messages, [nudge_event(1, 1), nudge_event(2, 20)]) == Silence(ASKED_AT, 2)


def test_nudges_before_the_last_answer_belong_to_an_earlier_silence():
    messages = [message("agent", -5), message("agent", -4), message("candidate", 0)]
    messages += [message("agent", 0)]

    assert silence(messages, [nudge_event(1, -4)]) == Silence(ASKED_AT, 0)


def test_after_an_override_the_silence_starts_at_the_new_question():
    # the close message, then the recruiter's override sends the next question
    messages = [message("candidate", -3), message("agent", -3), message("agent", 0)]

    assert silence(messages, []) == Silence(ASKED_AT, 0)


def test_no_silence_when_the_candidate_wrote_last():
    assert silence([message("agent", -1), message("candidate", 0)], []) is None


def test_a_silent_screening_with_every_field_settled_is_qualified_to_review():
    fields = {field.type: FieldState(status="valid", value="x") for field in CONFIG.fields}
    fields["schedule"] = FieldState(status="needs_review")
    # a correction of a valid field left unconfirmed: the valid value stands
    fields["name"] = FieldState(status="valid", value="Ana", unconfirmed=FieldState(value="Eva"))

    assert silent_outcome(CandidateState(consent=True, fields=fields), CONFIG) == (
        Status.QUALIFIED_TO_REVIEW
    )


def test_a_silent_screening_with_a_field_left_is_abandoned():
    fields = {field.type: FieldState(status="valid", value="x") for field in CONFIG.fields}
    fields["start_date"] = FieldState(unconfirmed=FieldState(status="valid", value="immediate"))

    assert silent_outcome(CandidateState(consent=True, fields=fields), CONFIG) == Status.ABANDONED
