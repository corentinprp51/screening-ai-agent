"""The candidate side of a screening: applying and one turn per candidate message.

Turn: extract (LLM) → validate (code) → update state → next_action() (code)
→ reply for that action (LLM) → persist.
"""

import re
from typing import Literal

from pydantic import JsonValue

from app.application.ports import CandidateRepository, Clock, LLMPort
from app.domain.fields import INVALID, VALIDATORS, update_field
from app.domain.flow import Action, Ask, Close, FollowUp, Greet, Recap, next_action, stage_of
from app.domain.models import Candidate, ClientConfig, Event, Extraction, Message


class UnknownCandidate(LookupError):
    pass


def normalize_handle(phone: str) -> str:
    handle = re.sub(r"\D", "", phone)
    if not handle:
        raise ValueError("A phone number needs digits")
    return handle


class ScreeningService:
    def __init__(
        self, config: ClientConfig, llm: LLMPort, repo: CandidateRepository, clock: Clock
    ) -> None:
        self._config = config
        self._llm = llm
        self._repo = repo
        self._clock = clock

    def apply(self, phone: str, name: str | None = None) -> Candidate:
        """Create the candidate and send the greeting, or resume an existing screening."""
        handle = normalize_handle(phone)
        existing = self._repo.get_by_handle(self._config.client_id, handle)
        if existing:
            return existing

        now = self._clock.now()
        candidate = Candidate(
            client_id=self._config.client_id,
            handle=handle,
            name=name or None,
            created_at=now,
            updated_at=now,
        )
        candidate.state.language = self._config.default_language
        candidate = self._repo.save(candidate)
        self._record(candidate, "application_received")
        self._send(candidate, self._config.greeting(candidate.state.language))
        return candidate

    def handle_message(self, handle: str, text: str) -> str:
        """Run one turn and return the agent's reply."""
        candidate = self._get(handle)
        pending = next_action(candidate.state, self._config)
        extraction = self._llm.extract(text, pending, candidate.state)
        candidate.state.language = extraction.language
        self._store(candidate, "candidate", text)

        self._apply_extraction(candidate, pending, extraction, text)
        action = next_action(candidate.state, self._config)
        reply = self._llm.reply(action, candidate.state, candidate.state.language)

        if isinstance(action, Close) and action.status is None:
            # Consent declined: nothing about the candidate is kept, not even this reply.
            self._repo.delete(candidate.id)
            return reply

        candidate.state.stage = stage_of(action)
        if isinstance(action, Close) and action.status and candidate.status != action.status:
            candidate.status = action.status
            self._record(candidate, "outcome", status=action.status)
        candidate.updated_at = self._clock.now()
        self._repo.save(candidate)
        self._send(candidate, reply)
        return reply

    def candidate(self, handle: str) -> Candidate | None:
        return self._repo.get_by_handle(self._config.client_id, handle)

    def transcript(self, handle: str) -> list[Message]:
        candidate = self.candidate(handle)
        return self._repo.list_messages(candidate.id) if candidate else []

    def _apply_extraction(
        self, candidate: Candidate, pending: Action, extraction: Extraction, text: str
    ) -> None:
        """Validate what was extracted for the pending action and update the state."""
        state = candidate.state
        match pending:
            case Greet():
                if extraction.yes_no is not None:
                    state.consent = extraction.yes_no
                if state.consent:
                    self._record(candidate, "consent_given")
            case Ask() | FollowUp():
                for field_config in self._config.fields:
                    self._apply_field(candidate, field_config.type, extraction, pending, text)
            case Recap():
                state.recap_confirmed = extraction.yes_no is True

    def _apply_field(
        self,
        candidate: Candidate,
        field_type: str,
        extraction: Extraction,
        pending: Action,
        text: str,
    ) -> None:
        """Validate one field. Attempts are only used by the question asked: an invalid
        answer volunteered for another field is ignored."""
        extracted = getattr(extraction, field_type)
        current = candidate.state.field(field_type)
        asked = isinstance(pending, Ask | FollowUp) and pending.field == field_type
        if extracted is not None:
            verdict = VALIDATORS[field_type](extracted.value, current, self._clock.now().date())
            if verdict.status == "invalid" and not asked:
                return
            updated = update_field(current, verdict, extracted.raw_answer, extracted.confidence)
        elif asked:
            # Nothing usable for the question asked (e.g. a failed coercion): an invalid
            # answer, and an unanswered follow-up still uses up the one follow-up.
            updated = update_field(current, INVALID, raw_answer=text)
        else:
            return
        candidate.state.fields[field_type] = updated
        if updated.status == "valid":
            if field_type == "name":
                candidate.name = updated.value
            self._record(candidate, "field_captured", field=field_type)
        elif updated.status == "needs_review":
            self._record(candidate, "field_needs_review", field=field_type)

    def _get(self, handle: str) -> Candidate:
        candidate = self.candidate(handle)
        if candidate is None:
            raise UnknownCandidate(handle)
        return candidate

    def _send(self, candidate: Candidate, content: str) -> None:
        self._store(candidate, "agent", content)

    def _store(
        self, candidate: Candidate, role: Literal["candidate", "agent"], content: str
    ) -> None:
        self._repo.add_message(
            candidate.id,
            Message(
                role=role,
                content=content,
                language=candidate.state.language,
                created_at=self._clock.now(),
            ),
        )

    def _record(self, candidate: Candidate, event_type: str, **payload: JsonValue) -> None:
        self._repo.add_event(
            candidate.id,
            Event(
                type=event_type,
                stage=candidate.state.stage,
                payload=payload,
                created_at=self._clock.now(),
            ),
        )
