from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.config.yaml_loader import load_client_config
from app.domain.models import CandidateState, FieldState
from app.domain.reengagement import Deadline, Nudge, due_step, questions_left

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
