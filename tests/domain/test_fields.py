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
    validate_start_date,
)
from app.domain.models import Experience, FieldState, License, OwnVehicle

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


def test_the_license_type_given_with_an_expired_answer_is_kept_after_the_follow_up():
    current = FieldState(
        status="incomplete",
        value=License(has_license="expired", type="moped_motorcycle"),
        missing="validity",
    )
    verdict = validate_license(License(has_license="expired"), current, TODAY)
    assert verdict.value == License(has_license="expired", type="moped_motorcycle")


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
