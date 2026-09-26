from datetime import date

import pytest

from app.domain.fields import (
    KNOCK_OUTS,
    format_value,
    update_field,
    validate_availability,
    validate_experience,
    validate_license,
    validate_name,
    validate_own_vehicle,
    validate_schedule,
    validate_service_area,
    validate_start_date,
)
from app.domain.models import Experience, FieldState, License, Location, OwnVehicle, Place

TODAY = date(2026, 9, 26)
EMPTY = FieldState()
AWAITING_SURNAME = FieldState(status="incomplete", value="Ana", missing="surname")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Ana López", ("valid", "Ana López", None)),
        ("  ana   lópez  ", ("valid", "ana lópez", None)),
        ("María José Núñez-García", ("valid", "María José Núñez-García", None)),
        ("Seán O'Brien", ("valid", "Seán O'Brien", None)),
        ("Ana", ("incomplete", "Ana", "surname")),
        ("Ana 2", ("invalid", None, None)),
        ("ana@mail.com", ("invalid", None, None)),
        ("", ("invalid", None, None)),
    ],
)
def test_validate_name_first_ask(answer, expected):
    verdict = validate_name(answer, EMPTY, TODAY)
    assert (verdict.status, verdict.value, verdict.missing) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("López", ("valid", "Ana López")),
        ("López García", ("valid", "Ana López García")),
        ("Ana López", ("valid", "Ana López")),
        ("Ana", ("incomplete", "Ana")),
        ("???", ("invalid", None)),
    ],
)
def test_validate_name_after_surname_follow_up(answer, expected):
    verdict = validate_name(answer, AWAITING_SURNAME, TODAY)
    assert (verdict.status, verdict.value) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (["full_time"], ("valid", ["full_time"])),
        (["part_time", "weekends"], ("valid", ["part_time", "weekends"])),
        (["weekends"], ("valid", ["weekends"])),
        (["weekends", "weekends"], ("valid", ["weekends"])),
        (["full_time", "part_time"], ("invalid", None)),
        ([], ("invalid", None)),
        (["nights"], ("invalid", None)),
    ],
)
def test_validate_availability(answer, expected):
    verdict = validate_availability(answer, EMPTY, TODAY)
    assert (verdict.status, verdict.value) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("morning", ("valid", "morning")),
        ("afternoon", ("valid", "afternoon")),
        ("evening", ("valid", "evening")),
        ("flexible", ("valid", "flexible")),
        ("night", ("invalid", None)),
    ],
)
def test_validate_schedule(answer, expected):
    verdict = validate_schedule(answer, EMPTY, TODAY)
    assert (verdict.status, verdict.value) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (Experience(years=0), ("valid", Experience(years=0))),
        (Experience(years=40), ("valid", Experience(years=40))),
        (
            Experience(years=3, platforms=["Glovo", "Uber Eats"]),
            ("valid", Experience(years=3, platforms=["Glovo", "Uber Eats"])),
        ),
        (Experience(years=-1), ("invalid", None)),
        (Experience(years=41), ("invalid", None)),
    ],
)
def test_validate_experience(answer, expected):
    verdict = validate_experience(answer, EMPTY, TODAY)
    assert (verdict.status, verdict.value) == expected


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("immediate", ("valid", "immediate", [])),
        (date(2026, 9, 26), ("valid", "2026-09-26", [])),
        (date(2026, 12, 25), ("valid", "2026-12-25", [])),
        (date(2026, 12, 26), ("valid", "2026-12-26", ["start_date_beyond_90_days"])),
        (date(2026, 9, 25), ("invalid", None, [])),
        ("tomorrow", ("invalid", None, [])),
    ],
)
def test_validate_start_date(answer, expected):
    verdict = validate_start_date(answer, EMPTY, TODAY)
    assert (verdict.status, verdict.value, verdict.flags) == expected


@pytest.mark.parametrize(
    ("answer", "current", "expected"),
    [
        (License(has_license=True), EMPTY, License(has_license=True)),
        (License(has_license=False), EMPTY, License(has_license=False)),
        (
            License(has_license=True, type="moped_motorcycle"),
            EMPTY,
            License(has_license=True, type="moped_motorcycle"),
        ),
        (
            License(has_license=True),
            FieldState(status="valid", value=License(has_license=True, type="car")),
            License(has_license=True, type="car"),
        ),
    ],
)
def test_validate_license_keeps_the_type_once_given(answer, current, expected):
    verdict = validate_license(answer, current, TODAY)
    assert (verdict.status, verdict.value) == ("valid", expected)


AWAITING_VALIDITY = FieldState(
    status="incomplete", value=License(has_license="expired"), missing="validity"
)


@pytest.mark.parametrize(
    ("answer", "current", "expected"),
    [
        (License(has_license="expired"), EMPTY, ("incomplete", "expired", "validity")),
        (License(has_license="pending"), EMPTY, ("incomplete", "pending", "validity")),
        (License(has_license=True), AWAITING_VALIDITY, ("valid", True, None)),
        (License(has_license="pending"), AWAITING_VALIDITY, ("valid", "pending", None)),
    ],
)
def test_an_expired_or_pending_license_gets_one_follow_up(answer, current, expected):
    verdict = validate_license(answer, current, TODAY)
    assert (verdict.status, verdict.value.has_license, verdict.missing) == expected


def test_a_license_still_not_valid_after_the_follow_up_fails_the_knock_out():
    assert KNOCK_OUTS["license"].fails(License(has_license="expired"))
    assert KNOCK_OUTS["license"].fails(License(has_license=False))
    assert not KNOCK_OUTS["license"].fails(License(has_license=True))


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (OwnVehicle(owns_vehicle=True, type="car"), ("valid", None)),
        (OwnVehicle(owns_vehicle=False), ("valid", None)),
        (OwnVehicle(owns_vehicle="shared"), ("incomplete", "access")),
    ],
)
def test_validate_own_vehicle(answer, expected):
    verdict = validate_own_vehicle(answer, EMPTY, TODAY)
    assert (verdict.status, verdict.missing) == expected
    assert verdict.value == answer


def test_an_own_vehicle_answer_after_the_follow_up_keeps_the_type():
    shared = FieldState(
        status="incomplete",
        value=OwnVehicle(owns_vehicle="shared", type="moped_motorcycle"),
        missing="access",
    )
    verdict = validate_own_vehicle(OwnVehicle(owns_vehicle=True), shared, TODAY)
    assert verdict.value == OwnVehicle(owns_vehicle=True, type="moped_motorcycle")


def test_an_unresolved_vehicle_access_follow_up_marks_the_field_needs_review():
    shared = FieldState(
        status="incomplete",
        value=OwnVehicle(owns_vehicle="shared"),
        raw_answer="la moto de mi hermano",
        missing="access",
    )
    verdict = validate_own_vehicle(OwnVehicle(owns_vehicle="shared"), shared, TODAY)

    updated = update_field(shared, verdict, "a veces", 1.0)

    assert (updated.status, updated.value, updated.flags) == (
        "needs_review",
        OwnVehicle(owns_vehicle="shared"),
        [],
    )


@pytest.mark.parametrize(
    ("value", "display"),
    [
        (License(has_license=True, type="car"), "yes (car)"),
        (License(has_license=False), "no"),
        (License(has_license="expired"), "expired"),
        (OwnVehicle(owns_vehicle="shared", type="moped_motorcycle"), "shared (moped_motorcycle)"),
    ],
)
def test_format_value_of_a_license_or_vehicle(value, display):
    assert format_value(value) == display


AREAS = {
    "ES": {"Madrid": ["Centro", "Getafe", "Móstoles"], "Barcelona": ["L'Hospitalet"]},
    "MX": {"Ciudad de México": ["Centro", "Coyoacán"], "Guadalajara": []},
}


def in_area(country, city, zone=None):
    return Location(country=country, city=city, zone=zone, in_service_area=True)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        # Exact city or zone, after normalization (accents, case, punctuation).
        (Place(city="Madrid"), ("valid", in_area("ES", "Madrid"), False, [])),
        (Place(city="  MÓSTOLES "), ("valid", in_area("ES", "Madrid", "Móstoles"), False, [])),
        (
            Place(city="coyoacan"),
            ("valid", in_area("MX", "Ciudad de México", "Coyoacán"), False, []),
        ),
        (
            Place(city="l hospitalet"),
            ("valid", in_area("ES", "Barcelona", "L'Hospitalet"), False, []),
        ),
        (
            Place(city="Madrid", zone="getafe"),
            ("valid", in_area("ES", "Madrid", "Getafe"), False, []),
        ),
        # A zone shared by two cities is found in the city given.
        (
            Place(city="Ciudad de Mexico", zone="Centro"),
            ("valid", in_area("MX", "Ciudad de México", "Centro"), False, []),
        ),
        # Close match, or a zone shared by two cities: confirmed first.
        (Place(city="Getaffe"), ("valid", in_area("ES", "Madrid", "Getafe"), True, [])),
        (Place(city="Barcelna"), ("valid", in_area("ES", "Barcelona"), True, [])),
        (Place(zone="Centro"), ("valid", in_area("ES", "Madrid", "Centro"), True, [])),
        # A listed city with an unknown zone: in area, with a flag.
        (
            Place(city="Madrid", zone="Vallecas"),
            ("valid", in_area("ES", "Madrid", "Vallecas"), False, ["zone_unknown"]),
        ),
        # A city not on the list: outside.
        (
            Place(city="Bilbao"),
            ("valid", Location(city="Bilbao", in_service_area=False), False, []),
        ),
        # No city and an unknown zone: ask for the city.
        (
            Place(zone="cerca del parque"),
            ("incomplete", Location(zone="cerca del parque", in_service_area=False), False, []),
        ),
        (Place(), ("invalid", None, False, [])),
    ],
)
def test_validate_service_area(answer, expected):
    verdict = validate_service_area(answer, EMPTY, AREAS)
    assert (verdict.status, verdict.value, verdict.unsure, verdict.flags) == expected


AWAITING_CITY = FieldState(
    status="incomplete", value=Location(zone="Vallecas", in_service_area=False), missing="city"
)


def test_the_zone_given_before_the_city_follow_up_is_kept():
    verdict = validate_service_area(Place(city="Madrid"), AWAITING_CITY, AREAS)
    assert (verdict.status, verdict.value, verdict.flags) == (
        "valid",
        in_area("ES", "Madrid", "Vallecas"),
        ["zone_unknown"],
    )


def test_an_unknown_place_after_the_city_follow_up_marks_the_field_needs_review():
    verdict = validate_service_area(Place(zone="por ahí"), AWAITING_CITY, AREAS)

    updated = update_field(AWAITING_CITY, verdict, "por ahí", 1.0)

    assert (updated.status, updated.flags) == ("needs_review", [])


def test_a_city_outside_the_service_areas_fails_the_knock_out_and_offers_contact():
    knock_out = KNOCK_OUTS["service_area"]
    assert knock_out.fails(Location(city="Bilbao", in_service_area=False))
    assert not knock_out.fails(in_area("ES", "Madrid"))
    assert knock_out.offers_contact


@pytest.mark.parametrize(
    ("value", "display"),
    [
        (in_area("ES", "Madrid", "Getafe"), "Getafe, Madrid (ES)"),
        (Location(city="Bilbao", in_service_area=False), "Bilbao"),
    ],
)
def test_format_value_of_a_location(value, display):
    assert format_value(value) == display
