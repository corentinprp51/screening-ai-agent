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
from app.domain.models import Candidate, ClientConfig, Event, Extraction, Message, Status


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
        """Run one turn and return the agent's reply. Never raises, except for an
        unknown candidate."""
        candidate = self._get(handle)
        if candidate.status != Status.IN_PROGRESS:
            return self._after_close(candidate, text)

        # Both LLM calls happen before any write, so a failure leaves the state unchanged.
        pending = next_action(candidate.state, self._config)
        events: list[Event] = []
        try:
            extraction = self._llm.extract(text, pending, candidate.state)
            candidate.state.language = extraction.language
            self._apply_extraction(candidate, pending, extraction, text, events)
            action = next_action(candidate.state, self._config)
            reply = self._llm.reply(action, candidate.state, candidate.state.language)
        except Exception as error:
            return self._llm_failure(self._get(handle), text, error)

        if isinstance(action, Close) and action.status is None:
            # Consent declined: nothing about the candidate is kept, not even this reply.
            self._repo.delete(candidate.id)
            return reply

        self._store(candidate, "candidate", text)
        for event in events:
            self._repo.add_event(candidate.id, event)
        candidate.state.stage = stage_of(action)
        if isinstance(action, Close) and action.status and candidate.status != action.status:
            candidate.status = action.status
            if action.status == Status.REJECTION_PROPOSED:
                answer = candidate.state.field(action.field).raw_answer
                self._record(candidate, "rejection_proposed", rule=action.reason, answer=answer)
            else:
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

    def _after_close(self, candidate: Candidate, text: str) -> str:
        """The screening has stopped: store the message, flag it for the recruiter and
        answer with a fixed template. The screening is not reopened."""
        reply = self._config.templates.after_close[candidate.state.language]
        self._store(candidate, "candidate", text)
        candidate.state.add_flag("message_after_close")
        self._record(candidate, "message_after_close")
        candidate.updated_at = self._clock.now()
        self._repo.save(candidate)
        self._send(candidate, reply)
        return reply

    def _llm_failure(self, candidate: Candidate, text: str, error: Exception) -> str:
        """An LLM call failed: keep the message, flag the candidate and send the fallback.
        The state is unchanged, so the next message gets the same question."""
        reply = self._config.templates.fallback[candidate.state.language]
        self._store(candidate, "candidate", text)
        candidate.state.add_flag("llm_failure")
        self._record(candidate, "llm_failure", error=repr(error))
        candidate.updated_at = self._clock.now()
        self._repo.save(candidate)
        self._send(candidate, reply)
        return reply

    def _apply_extraction(
        self,
        candidate: Candidate,
        pending: Action,
        extraction: Extraction,
        text: str,
        events: list[Event],
    ) -> None:
        """Validate what was extracted for the pending action and update the state.
        Events are collected in `events`, written only once the turn succeeds."""
        state = candidate.state
        if extraction.intent == "opt_out" and not isinstance(pending, Greet):
            state.opted_out = True
            events.append(self._event(candidate, "opted_out"))
            return
        match pending:
            case Greet():
                if extraction.yes_no is not None:
                    state.consent = extraction.yes_no
                if state.consent:
                    events.append(self._event(candidate, "consent_given"))
            case Ask() | FollowUp():
                for field_config in self._config.fields:
                    self._apply_field(
                        candidate, field_config.type, extraction, pending, text, events
                    )
            case Recap():
                state.recap_confirmed = extraction.yes_no is True

    def _apply_field(
        self,
        candidate: Candidate,
        field_type: str,
        extraction: Extraction,
        pending: Action,
        text: str,
        events: list[Event],
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
            events.append(self._event(candidate, "field_captured", field=field_type))
        elif updated.status == "needs_review":
            events.append(self._event(candidate, "field_needs_review", field=field_type))

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
        self._repo.add_event(candidate.id, self._event(candidate, event_type, **payload))

    def _event(self, candidate: Candidate, event_type: str, **payload: JsonValue) -> Event:
        return Event(
            type=event_type,
            stage=candidate.state.stage,
            payload=payload,
            created_at=self._clock.now(),
        )
