"""Field types: one validator per type, and the generic rule that updates a field state."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, get_args

from app.domain.models import (
    AvailabilityOption,
    Experience,
    FieldState,
    FieldValue,
    License,
    OwnVehicle,
    ScheduleOption,
)

# The 1st and 2nd invalid answers lead to a re-ask; the 3rd marks the field needs review.
MAX_ATTEMPTS = 3
MAX_EXPERIENCE_YEARS = 40
START_DATE_HORIZON_DAYS = 90
# A missing part that decides a knock-out: left unresolved after its follow-up, the
# field goes to a recruiter instead of being accepted with a flag.
DECIDES_KNOCK_OUT = {"access"}

# Unicode letter runs joined by a single space, hyphen or apostrophe.
NAME_PATTERN = re.compile(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*")


@dataclass(frozen=True)
class Verdict:
    status: Literal["valid", "incomplete", "invalid"]
    value: FieldValue | None = None
    missing: str | None = None
    flags: list[str] = field(default_factory=list)


INVALID = Verdict("invalid")


def validate_name(answer: str, current: FieldState, today: date) -> Verdict:
    """First name + surname. After the surname follow-up, a new answer that does not
    repeat the first name is taken as the surname(s)."""
    name = " ".join(answer.split())
    if current.status == "incomplete" and name:
        first_name = current.value.split()[0]
        if name.split()[0].casefold() != first_name.casefold():
            name = f"{current.value} {name}"
    if not NAME_PATTERN.fullmatch(name):
        return INVALID
    if len(name.split()) < 2:
        return Verdict("incomplete", value=name, missing="surname")
    return Verdict("valid", value=name)


def validate_license(answer: License, current: FieldState, today: date) -> Verdict:
    """An explicit yes or no; a type given earlier is kept, never re-asked."""
    return Verdict("valid", value=_keep_type(answer, current))


def validate_own_vehicle(answer: OwnVehicle, current: FieldState, today: date) -> Verdict:
    """An explicit yes or no; a shared or borrowed vehicle gets one follow-up on access."""
    answer = _keep_type(answer, current)
    if answer.owns_vehicle == "shared":
        return Verdict("incomplete", value=answer, missing="access")
    return Verdict("valid", value=answer)


def _keep_type[T: (License, OwnVehicle)](answer: T, current: FieldState) -> T:
    if answer.type is None and isinstance(current.value, License | OwnVehicle):
        return answer.model_copy(update={"type": current.value.type})
    return answer


def validate_availability(answer: list[str], current: FieldState, today: date) -> Verdict:
    """At least one option; full_time and part_time are mutually exclusive."""
    options = list(dict.fromkeys(answer))
    if not options or not set(options) <= set(get_args(AvailabilityOption)):
        return INVALID
    if {"full_time", "part_time"} <= set(options):
        return INVALID
    return Verdict("valid", value=options)


def validate_schedule(answer: str, current: FieldState, today: date) -> Verdict:
    """Exactly one option."""
    if answer not in get_args(ScheduleOption):
        return INVALID
    return Verdict("valid", value=answer)


def validate_experience(answer: Experience, current: FieldState, today: date) -> Verdict:
    """Years from 0 to 40; the platforms are kept as given."""
    if not 0 <= answer.years <= MAX_EXPERIENCE_YEARS:
        return INVALID
    return Verdict("valid", value=answer)


def validate_start_date(answer: str | date, current: FieldState, today: date) -> Verdict:
    """`immediate` or a date from today on; beyond 90 days it is kept with a flag."""
    if answer == "immediate":
        return Verdict("valid", value="immediate")
    if not isinstance(answer, date) or answer < today:
        return INVALID
    flags = ["start_date_beyond_90_days"] if (answer - today).days > START_DATE_HORIZON_DAYS else []
    return Verdict("valid", value=answer.isoformat(), flags=flags)


# Each validator receives the typed value of its part of the Extraction.
VALIDATORS: dict[str, Callable[[FieldValue | date, FieldState, date], Verdict]] = {
    "name": validate_name,
    "license": validate_license,
    "own_vehicle": validate_own_vehicle,
    "availability": validate_availability,
    "schedule": validate_schedule,
    "experience": validate_experience,
    "start_date": validate_start_date,
}


def update_field(
    current: FieldState,
    verdict: Verdict,
    raw_answer: str | None = None,
    confidence: float | None = None,
) -> FieldState:
    """Apply a verdict to a field.

    An incomplete answer gets one follow-up; if the follow-up does not complete it,
    the earlier value is accepted with a `<missing>_missing` flag, or marked needs review
    when the missing part decides a knock-out. An invalid answer
    uses one attempt and clears the value; the last attempt marks the field needs review.
    """
    if verdict.status == "valid":
        return FieldState(
            status="valid",
            value=verdict.value,
            raw_answer=raw_answer,
            confidence=confidence,
            attempts=current.attempts,
            flags=verdict.flags,
        )
    if current.status == "incomplete":
        if current.missing in DECIDES_KNOCK_OUT:
            return current.model_copy(update={"status": "needs_review", "missing": None})
        return current.model_copy(
            update={
                "status": "valid",
                "missing": None,
                "flags": [*current.flags, f"{current.missing}_missing"],
            }
        )
    if verdict.status == "incomplete":
        return FieldState(
            status="incomplete",
            value=verdict.value,
            raw_answer=raw_answer,
            confidence=confidence,
            missing=verdict.missing,
            attempts=current.attempts,
        )
    attempts = current.attempts + 1
    return FieldState(
        status="needs_review" if attempts >= MAX_ATTEMPTS else "empty",
        raw_answer=raw_answer,
        attempts=attempts,
    )


@dataclass(frozen=True)
class KnockOut:
    rule: str
    fails: Callable[[FieldValue], bool]


# Applied to a valid field whose config has `knock_out: true`: only an explicit "no" fails,
# an unclear answer is needs review and left to a recruiter.
KNOCK_OUTS: dict[str, KnockOut] = {
    "license": KnockOut("no_license", lambda value: value.has_license is False),
    "own_vehicle": KnockOut("no_own_vehicle", lambda value: value.owns_vehicle is False),
}


def format_value(value: FieldValue | None) -> str:
    """A field value as shown in the recap and on the dashboard."""
    match value:
        case None:
            return "—"
        case list():
            return ", ".join(value)
        case Experience(years=years, platforms=platforms):
            return f"{years} years" + (f" ({', '.join(platforms)})" if platforms else "")
        case (
            License(has_license=answer, type=vehicle_type)
            | OwnVehicle(owns_vehicle=answer, type=vehicle_type)
        ):
            label = answer if answer == "shared" else ("yes" if answer else "no")
            return label + (f" ({vehicle_type})" if vehicle_type else "")
        case _:
            return value
