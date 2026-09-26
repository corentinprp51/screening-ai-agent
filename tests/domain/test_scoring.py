from datetime import date

import pytest

from app.domain.models import (
    CandidateState,
    Experience,
    FieldState,
    OpenShifts,
    ScoreWeights,
    Scoring,
)
from app.domain.scoring import priority_score

TODAY = date(2026, 9, 26)
SCORING = Scoring(
    weights=ScoreWeights(availability=30, schedule=20, start_date=30, experience=20),
    open_shifts=OpenShifts(availability=["full_time", "weekends"], schedule=["evening"]),
)


def valid(value):
    return FieldState(status="valid", value=value)


def points(field: str, state: FieldState) -> int:
    return priority_score(CandidateState(fields={field: state}), SCORING, TODAY).points[field]


@pytest.mark.parametrize(
    ("field", "state", "expected"),
    [
        # Shift match: availability overlaps the open shifts (30), schedule in them (20).
        ("availability", valid(["full_time"]), 30),
        ("availability", valid(["part_time", "weekends"]), 30),
        ("availability", valid(["part_time"]), 0),
        ("schedule", valid("evening"), 20),
        ("schedule", valid("flexible"), 20),
        ("schedule", valid("morning"), 0),
        # Start date: 30 up to 7 days, then linear to 0 at 90 days.
        ("start_date", valid("immediate"), 30),
        ("start_date", valid("2026-10-03"), 30),  # 7 days
        ("start_date", valid("2026-11-14"), 15),  # 49 days, half way from 7 to 90
        ("start_date", valid("2026-12-25"), 0),  # 90 days
        ("start_date", valid("2027-03-01"), 0),  # beyond 90 days
        # Experience: min(years, 5) × 4.
        ("experience", valid(Experience(years=0)), 0),
        ("experience", valid(Experience(years=2)), 8),
        ("experience", valid(Experience(years=5)), 20),
        ("experience", valid(Experience(years=12)), 20),
        # A missing or needs-review field scores 0.
        ("availability", FieldState(), 0),
        ("schedule", FieldState(status="needs_review", attempts=3), 0),
        ("experience", FieldState(status="incomplete", value=Experience(years=5)), 0),
        # Only a confirmed value counts: one waiting for confirmation does not.
        ("availability", FieldState(unconfirmed=valid(["full_time"])), 0),
        (
            "schedule",
            FieldState(status="valid", value="morning", unconfirmed=valid("evening")),
            0,
        ),
    ],
)
def test_each_component_scores_by_rule(field, state, expected):
    assert points(field, state) == expected


def test_the_total_is_the_sum_of_the_components():
    state = CandidateState(
        fields={
            "availability": valid(["full_time"]),
            "schedule": valid("morning"),
            "start_date": valid("immediate"),
            "experience": valid(Experience(years=2)),
        }
    )

    score = priority_score(state, SCORING, TODAY)

    assert score.points == {"availability": 30, "schedule": 0, "start_date": 30, "experience": 8}
    assert score.total == 68


def test_a_new_candidate_scores_0():
    assert priority_score(CandidateState(), SCORING, TODAY).total == 0


def test_the_weights_add_up_to_100():
    with pytest.raises(ValueError, match="100"):
        ScoreWeights(availability=30, schedule=20, start_date=30, experience=30)
