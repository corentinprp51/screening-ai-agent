"""A deliberately dumb LLM for v0, tests and offline demos. No NLU, no keywords.

- extract: pops a scripted extraction if one is queued (at creation or with `queue`);
  otherwise echoes the raw message into the slot the pending action asks for and lets
  Pydantic coercion type it ("yes" → True). A failed coercion yields an empty
  extraction, i.e. an invalid answer.
- Every call returns a zero usage: no tokens are spent.
- reply / summarize: visible `[fake] …` placeholders; the recap lists every field and the
  summary names the status and the next action.
- fail_next(call): the next call to that method raises, to exercise the failure path.
- calls(call): the arguments of each call to that method, for tests to inspect.
"""

from datetime import date
from typing import Literal

from pydantic import JsonValue, ValidationError

from app.application.ports import LLMResult, LLMUsage
from app.domain.fields import format_value
from app.domain.flow import Action, Ask, AskCorrection, Close, Confirm, FollowUp, Greet, Recap
from app.domain.models import CandidateState, Extraction, Language, Message

LLMCall = Literal["extract", "reply", "summarize"]


class FakeLLM:
    def __init__(self, script: list[Extraction] | None = None) -> None:
        self._script = list(script or [])
        self._fail: LLMCall | None = None
        self._log: list[tuple[LLMCall, dict[str, object]]] = []

    def queue(self, *extractions: Extraction) -> None:
        self._script.extend(extractions)

    def fail_next(self, call: LLMCall = "extract") -> None:
        self._fail = call

    def calls(self, call: LLMCall) -> list[dict[str, object]]:
        return [args for logged, args in self._log if logged == call]

    def _maybe_fail(self, call: LLMCall) -> None:
        if self._fail == call:
            self._fail = None
            raise RuntimeError(f"FakeLLM: scripted {call} failure")

    def extract(
        self,
        message: str,
        action: Action,
        state: CandidateState,
        today: date,
        last_agent_message: str | None,
    ) -> LLMResult[Extraction]:
        self._log.append(
            (
                "extract",
                {
                    "message": message,
                    "action": action,
                    "state": state.model_copy(deep=True),
                    "today": today,
                    "last_agent_message": last_agent_message,
                },
            )
        )
        self._maybe_fail("extract")
        if self._script:
            return LLMResult(self._script.pop(0), LLMUsage())
        match action:
            case Greet() | Confirm() | Recap() | AskCorrection():
                data: dict[str, JsonValue] = {"yes_no": message.strip()}
            case Ask(field=field) | FollowUp(field=field):
                data = {field: {"value": message, "raw_answer": message, "confidence": 1.0}}
            case _:
                data = {}
        try:
            extraction = Extraction.model_validate({"language": "es", **data})
        except ValidationError:
            extraction = Extraction(language="es")
        return LLMResult(extraction, LLMUsage())

    def reply(
        self,
        action: Action,
        state: CandidateState,
        language: Language,
        transcript: list[Message],
    ) -> LLMResult[str]:
        self._log.append(
            (
                "reply",
                {
                    "action": action,
                    "state": state.model_copy(deep=True),
                    "language": language,
                    "transcript": transcript,
                },
            )
        )
        self._maybe_fail("reply")
        match action:
            case Greet():
                label = "greet"
            case Ask(field=field, attempt=attempt):
                label = f"ask:{field} (attempt {attempt})"
            case FollowUp(field=field, missing=missing):
                label = f"follow_up:{field} ({missing})"
            case Confirm(field=field):
                label = f"confirm:{field}={format_value(state.field(field).unconfirmed.value)}"
            case Recap(fields=fields):
                label = "recap: " + "; ".join(
                    f"{field}={_recap_value(state, field)}" for field in fields
                )
            case AskCorrection(attempt=attempt):
                label = f"ask_correction (attempt {attempt})"
            case Close(
                status=status, reason=reason, within_hours=within_hours, offer_contact=offer
            ):
                label = f"close:{reason or status}"
                if within_hours:
                    label += f" (reply within {within_hours} h)"
                if offer:
                    label += " (offer contact)"
        return LLMResult(f"[fake] {label}", LLMUsage())

    def summarize(self, facts: dict[str, JsonValue], language: Language) -> LLMResult[str]:
        self._log.append(("summarize", {"facts": facts, "language": language}))
        self._maybe_fail("summarize")
        text = f"[fake] summary: {facts['status']}; next: {facts['next_action']}"
        return LLMResult(text, LLMUsage())


def _recap_value(state: CandidateState, field: str) -> str:
    field_state = state.field(field)
    if field_state.status == "needs_review":
        return "needs review"
    return format_value(field_state.value)
