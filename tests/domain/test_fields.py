import pytest

from app.domain.fields import validate_name
from app.domain.models import FieldState

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
    verdict = validate_name(answer, EMPTY)
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
    verdict = validate_name(answer, AWAITING_SURNAME)
    assert (verdict.status, verdict.value) == expected
