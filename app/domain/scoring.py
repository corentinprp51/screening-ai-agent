"""The priority score: 0 to 100, computed by rules from the valid fields, never by the LLM."""

from datetime import date

from app.domain.fields import START_DATE_HORIZON_DAYS
from app.domain.models import CandidateState, Experience, Score, Scoring

# A start within this many days gets every start-date point.
START_DATE_FULL_POINTS_DAYS = 7
# Experience points grow with the years up to this cap.
EXPERIENCE_CAP_YEARS = 5


def priority_score(state: CandidateState, scoring: Scoring, today: date) -> Score:
    """Points per field, out of its weight. A field that is not valid (missing, incomplete
    or needs review) scores 0."""
    weights = scoring.weights
    open_shifts = scoring.open_shifts
    points = dict.fromkeys(weights.model_dump(), 0)

    availability = state.field("availability")
    if availability.status == "valid" and set(availability.value) & set(open_shifts.availability):
        points["availability"] = weights.availability

    schedule = state.field("schedule")
    if schedule.status == "valid" and (
        schedule.value == "flexible" or schedule.value in open_shifts.schedule
    ):
        points["schedule"] = weights.schedule

    start_date = state.field("start_date")
    if start_date.status == "valid":
        days = (
            0
            if start_date.value == "immediate"
            else (date.fromisoformat(start_date.value) - today).days
        )
        points["start_date"] = round(weights.start_date * _start_date_ratio(days))

    experience = state.field("experience")
    if experience.status == "valid" and isinstance(experience.value, Experience):
        years = min(experience.value.years, EXPERIENCE_CAP_YEARS)
        points["experience"] = round(weights.experience * years / EXPERIENCE_CAP_YEARS)

    return Score(points=points)


def _start_date_ratio(days: int) -> float:
    """1 up to 7 days, then linear down to 0 at 90 days."""
    if days <= START_DATE_FULL_POINTS_DAYS:
        return 1.0
    remaining = START_DATE_HORIZON_DAYS - days
    return max(remaining, 0) / (START_DATE_HORIZON_DAYS - START_DATE_FULL_POINTS_DAYS)
