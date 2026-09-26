"""A deliberately dumb LLM for v0, tests and offline demos. No NLU, no keywords.

- extract: pops a scripted extraction if one is queued; otherwise echoes the raw message
  into the slot the pending action asks for and lets Pydantic coercion type it
  ("yes" → True). A failed coercion yields an empty extraction, i.e. an invalid answer.
- reply / summarize: visible `[fake] …` placeholders; the recap lists every field.
"""

from pydantic import JsonValue, ValidationError

from app.domain.fields import format_value
from app.domain.flow import Action, Ask, Close, FollowUp, Greet, Recap
from app.domain.models import CandidateState, Extraction, Language


class FakeLLM:
    def __init__(self, script: list[Extraction] | None = None) -> None:
        self._script = list(script or [])

    def extract(self, message: str, action: Action, state: CandidateState) -> Extraction:
        if self._script:
            return self._script.pop(0)
        match action:
            case Greet() | Recap():
                data: dict[str, JsonValue] = {"yes_no": message.strip()}
            case Ask(field=field) | FollowUp(field=field):
                data = {field: {"value": message, "raw_answer": message, "confidence": 1.0}}
            case _:
                data = {}
        try:
            return Extraction.model_validate({"language": "es", **data})
        except ValidationError:
            return Extraction(language="es")

    def reply(self, action: Action, state: CandidateState, language: Language) -> str:
        match action:
            case Greet():
                label = "greet"
            case Ask(field=field, attempt=attempt):
                label = f"ask:{field} (attempt {attempt})"
            case FollowUp(field=field, missing=missing):
                label = f"follow_up:{field} ({missing})"
            case Recap(fields=fields):
                label = "recap: " + "; ".join(
                    f"{field}={_recap_value(state, field)}" for field in fields
                )
            case Close(status=status, reason=reason):
                label = f"close:{reason or status}"
        return f"[fake] {label}"

    def summarize(self, facts: dict[str, JsonValue], language: Language) -> str:
        return "[fake] summary"


def _recap_value(state: CandidateState, field: str) -> str:
    field_state = state.field(field)
    if field_state.status == "needs_review":
        return "needs review"
    return format_value(field_state.value)
