from datetime import date

import pytest

from app.domain.fields import (
    validate_availability,
    validate_experience,
    validate_name,
    validate_schedule,
    validate_start_date,
)
from app.domain.models import Experience, FieldState

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
