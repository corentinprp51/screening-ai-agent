"""Field types: one validator per type, and the generic rule that updates a field state."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app.domain.models import FieldState, FieldValue

# Unicode letter runs joined by a single space, hyphen or apostrophe.
NAME_PATTERN = re.compile(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*")


@dataclass(frozen=True)
class Verdict:
    status: Literal["valid", "incomplete", "invalid"]
    value: FieldValue | None = None
    missing: str | None = None
    flags: list[str] = field(default_factory=list)


INVALID = Verdict("invalid")


def validate_name(answer: str, current: FieldState) -> Verdict:
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


VALIDATORS: dict[str, Callable[[FieldValue, FieldState], Verdict]] = {
    "name": validate_name,
}


def update_field(
    current: FieldState,
    verdict: Verdict,
    raw_answer: str | None = None,
    confidence: float | None = None,
) -> FieldState:
    """Apply a verdict to a field.

    An incomplete answer gets one follow-up; if the follow-up does not complete it,
    the earlier value is accepted with a `<missing>_missing` flag.
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
    return FieldState(attempts=current.attempts + 1)
