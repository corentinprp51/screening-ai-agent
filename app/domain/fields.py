"""Field types: one validator per type, and the generic rule that updates a field state."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, get_args

from app.domain.areas import AreaMatch, Matches, match_cities, match_zones
from app.domain.models import (
    AvailabilityOption,
    Experience,
    FieldState,
    FieldValue,
    License,
    Location,
    OwnVehicle,
    Place,
    ScheduleOption,
    ServiceAreas,
)

# The 1st and 2nd invalid answers lead to a re-ask; the 3rd marks the field needs review.
MAX_ATTEMPTS = 3
MAX_EXPERIENCE_YEARS = 40
START_DATE_HORIZON_DAYS = 90
# A missing part that decides a knock-out: left unresolved after its follow-up, the
# field goes to a recruiter instead of being accepted with a flag.
DECIDES_KNOCK_OUT = {"access", "city"}

# Unicode letter runs joined by a single space, hyphen or apostrophe.
NAME_PATTERN = re.compile(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*")


@dataclass(frozen=True)
class Verdict:
    """`unsure` asks the candidate to confirm the value, whatever the confidence."""

    status: Literal["valid", "incomplete", "invalid"]
    value: FieldValue | None = None
    missing: str | None = None
    flags: list[str] = field(default_factory=list)
    unsure: bool = False


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
    """An explicit yes or no; a type given earlier is kept, never re-asked. An expired or
    pending license gets one follow-up on validity; still not valid, it is kept as is
    and fails the knock-out."""
    answer = _keep_type(answer, current)
    awaiting_validity = current.status == "incomplete" and current.missing == "validity"
    if answer.has_license in ("expired", "pending") and not awaiting_validity:
        return Verdict("incomplete", value=answer, missing="validity")
    return Verdict("valid", value=answer)


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


def validate_service_area(answer: Place, current: FieldState, areas: ServiceAreas) -> Verdict:
    """Match the place against the service areas; the city is what the knock-out checks.

    An exact city or zone is in area. A close match, or a name found in several cities,
    is confirmed first. A listed city with an unknown zone is in area with a flag. A city
    not on the list is outside. No city and an unknown zone gets one follow-up asking for
    the city; the zone given before it is kept.
    """
    if not (answer.city or answer.zone):
        return INVALID
    zone = answer.zone
    if zone is None and current.status == "incomplete" and isinstance(current.value, Location):
        zone = current.value.zone
    if answer.city:
        cities = match_cities(answer.city, areas)
        if cities.found:
            return _in_area(cities, areas, zone)
        # A town or district given as the city ("Getafe").
        zones = match_zones(answer.city, areas)
        if zones.found:
            return _in_area(zones, areas)
        return Verdict("valid", Location(city=answer.city, zone=zone, in_service_area=False))
    zones = match_zones(zone, areas)
    if zones.found:
        return _in_area(zones, areas)
    return Verdict("incomplete", Location(zone=zone, in_service_area=False), missing="city")


def _in_area(matches: Matches, areas: ServiceAreas, zone: str | None = None) -> Verdict:
    """The best match, in area. `zone` is looked up in the matched city."""
    match = matches.found[0]
    unsure = not matches.exact or len(matches.found) > 1
    flags = []
    if zone is not None:
        zones = match_zones(zone, areas, city=match)
        if zones.found:
            match = zones.found[0]
            unsure = unsure or not zones.exact
        else:
            match = AreaMatch(match.country, match.city, zone)
            flags = ["zone_unknown"]
    location = Location(
        country=match.country, city=match.city, zone=match.zone, in_service_area=True
    )
    return Verdict("valid", location, flags=flags, unsure=unsure)


# Each validator receives the typed value of its part of the Extraction. The service area
# is not here: its validator also needs the client's service areas.
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
    """`offers_contact`: the rejection message offers to get back to the candidate if a
    nearby location opens."""

    rule: str
    fails: Callable[[FieldValue], bool]
    offers_contact: bool = False


# Applied to a valid field whose config has `knock_out: true`: only an explicit "no" or a
# city outside the service areas fails (an expired or pending license counts as a no), an
# unclear answer is needs review and left to a recruiter.
KNOCK_OUTS: dict[str, KnockOut] = {
    "license": KnockOut("no_license", lambda value: value.has_license is not True),
    "own_vehicle": KnockOut("no_own_vehicle", lambda value: value.owns_vehicle is False),
    "service_area": KnockOut(
        "outside_service_area", lambda value: not value.in_service_area, offers_contact=True
    ),
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
            label = answer if isinstance(answer, str) else ("yes" if answer else "no")
            return label + (f" ({vehicle_type})" if vehicle_type else "")
        case Location(country=country, city=city, zone=zone):
            place = ", ".join(part for part in (zone, city) if part)
            return place + (f" ({country})" if country else "")
        case _:
            return value
