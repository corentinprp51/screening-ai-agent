"""The candidate side of a screening: applying and one turn per candidate message.

Turn: extract (LLM) → validate (code) → update state → next_action() and priority score
(code) → reply for that action (LLM) → summary when the questions stop (LLM) → persist.
The sweep (`tick`) nudges silent candidates and ends the screenings silent past the deadline.
"""

import re
from datetime import date, datetime
from typing import Literal

from pydantic import JsonValue

from app.application.ports import (
    RECENT_MESSAGES,
    CandidateRepository,
    Clock,
    LLMPort,
    LLMUsage,
)
from app.domain.fields import (
    INVALID,
    VALIDATORS,
    Verdict,
    update_field,
    validate_service_area,
)
from app.domain.flow import (
    Action,
    Ask,
    AskCorrection,
    Close,
    Confirm,
    FollowUp,
    Greet,
    Recap,
    next_action,
    stage_of,
)
from app.domain.models import (
    Candidate,
    ClientConfig,
    Cue,
    Event,
    Extraction,
    FieldState,
    FieldValue,
    Message,
    Persona,
    Place,
    Status,
    Summary,
)
from app.domain.reengagement import (
    Deadline,
    Nudge,
    due_step,
    questions_left,
    silence,
    silent_outcome,
)
from app.domain.scoring import priority_score
from app.domain.summary import SUMMARIZED_STATUSES, summary_facts


class UnknownCandidate(LookupError):
    pass


def normalize_handle(phone: str) -> str:
    handle = re.sub(r"\D", "", phone)
    if not handle:
        raise ValueError("A phone number needs digits")
    return handle


def write_summary(candidate: Candidate, config: ClientConfig, llm: LLMPort) -> LLMUsage:
    """Phrase the summary from facts computed by code, store both on the candidate and
    return the usage. On failure the text stays empty, the candidate is flagged and the
    error is raised."""
    facts = summary_facts(candidate.status, candidate.state, config)
    try:
        text, usage = llm.summarize(facts, config.default_language)
    except Exception:
        candidate.summary = Summary(facts=facts)
        candidate.state.add_flag("llm_failure")
        raise
    candidate.summary = Summary(text=text, facts=facts)
    return usage


class ScreeningService:
    def __init__(
        self, config: ClientConfig, llm: LLMPort, repo: CandidateRepository, clock: Clock
    ) -> None:
        self._config = config
        self._llm = llm
        self._repo = repo
        self._clock = clock

    @property
    def persona(self) -> Persona:
        return self._config.persona

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
        candidate.score = priority_score(candidate.state, self._config.scoring, now.date())
        candidate = self._repo.save(candidate)
        self._record(candidate, "application_received")
        self._send(candidate, self._config.greeting(candidate.state.language))
        return candidate

    def handle_message(self, handle: str, text: str) -> str:
        """Run one turn and return the agent's reply. Never raises, except for an
        unknown candidate."""
        candidate = self._get(handle)
        events: list[Event] = []
        resumed = candidate.status == Status.ABANDONED
        if resumed:
            # Resume at the stage it stopped at. On an LLM failure the candidate is reloaded,
            # so this is kept only if the turn succeeds.
            candidate.status = Status.IN_PROGRESS
            events.append(self._event(candidate, "resumed"))
        elif candidate.status != Status.IN_PROGRESS:
            return self._after_close(candidate, text)

        # Both LLM calls happen before any write except their `llm_call` events, so a failure
        # leaves the state unchanged.
        pending = next_action(candidate.state, self._config)
        messages = self._repo.list_messages(candidate.id)
        last_agent_message = next(
            (m.content for m in reversed(messages) if m.role == "agent"), None
        )
        resuming = resumed or self._nudged_or_reopened_since_last_message(candidate, messages)
        try:
            extraction, usage = self._llm.extract(
                text, pending, candidate.state, self._clock.now().date(), last_agent_message
            )
            self._record_llm_call(candidate, "extract", usage)
            candidate.state.language = extraction.language
            self._apply_extraction(candidate, pending, extraction, text, events)
            action = next_action(candidate.state, self._config)
            # The candidate message is stored after the LLM calls, but the reply follows it.
            recent = [*messages, self._message(candidate, "candidate", text)][-RECENT_MESSAGES:]
            # A closing message is not a welcome back, nor does it go back to a question.
            cues = (
                frozenset()
                if isinstance(action, Close)
                else self._cues(candidate, extraction, resuming)
            )
            reply, usage = self._llm.reply(
                action, candidate.state, candidate.state.language, recent, cues
            )
            self._record_llm_call(candidate, "reply", usage)
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
        candidate.score = priority_score(
            candidate.state, self._config.scoring, self._clock.now().date()
        )
        if isinstance(action, Close) and action.status and candidate.status != action.status:
            candidate.status = action.status
            if action.status == Status.REJECTION_PROPOSED:
                # Abuse has no field: its answer is the message that proposed it.
                answer = candidate.state.field(action.field).raw_answer if action.field else text
                self._record(candidate, "rejection_proposed", rule=action.reason, answer=answer)
            else:
                self._record(candidate, "outcome", status=action.status)
            if action.status in SUMMARIZED_STATUSES:
                self._summarize(candidate)
        candidate.updated_at = self._clock.now()
        self._repo.save(candidate)
        self._send(candidate, reply)
        return reply

    def _cues(self, candidate: Candidate, extraction: Extraction, resuming: bool) -> frozenset[Cue]:
        """How the reply is phrased. Frustration replaces the welcome back and the forwarded
        question: they all open the reply (the question is still forwarded). A candidate who
        already asked for a call is not offered it again. An abusive message only gets the
        refocus: no welcome back, no call offer."""
        if extraction.intent == "abuse":
            return frozenset({"refocus"})
        if extraction.sentiment == "frustrated" and "wants_human" not in candidate.state.flags:
            return frozenset({"frustrated"})
        cues: set[Cue] = set()
        if resuming:
            cues.add("resuming")
        if extraction.intent == "question":
            cues.add("question_forwarded")
        if extraction.sentiment == "confused":
            cues.add("confused")
        return frozenset(cues)

    def _nudged_or_reopened_since_last_message(
        self, candidate: Candidate, messages: list[Message]
    ) -> bool:
        events = self._repo.list_events(candidate.id)
        since_last_message = silence(messages, events)
        if since_last_message is None:
            return False
        # A Reopen starts the silence, so its message is the question the silence counts from.
        reopened = any(
            event.type == "reopened" and event.created_at >= since_last_message.asked_at
            for event in events
        )
        return since_last_message.last_nudge > 0 or reopened

    def tick(self) -> None:
        """The sweep, run on a schedule: recompute the priority score of every open
        candidate, nudge the silent ones who gave consent, and end the screenings silent
        past the deadline."""
        now = self._clock.now()
        for status in (Status.IN_PROGRESS, Status.REJECTION_PROPOSED):
            for candidate in self._repo.list_candidates(self._config.client_id, status):
                candidate.score = priority_score(candidate.state, self._config.scoring, now.date())
                match self._due_step(candidate, now) if status == Status.IN_PROGRESS else None:
                    case Deadline() if candidate.state.consent is None:
                        # No answer to the greeting: erased, only counted as a drop-off.
                        self._repo.delete(candidate.id)
                        self._repo.add_consent_drop_off(self._config.client_id, now)
                        continue
                    case Deadline():
                        self._end_silent_screening(candidate)
                        candidate.updated_at = now
                    case Nudge(number=number) if candidate.state.consent:
                        self._send_nudge(candidate, number)
                        candidate.updated_at = now
                self._repo.save(candidate)

    def candidate(self, handle: str) -> Candidate | None:
        return self._repo.get_by_handle(self._config.client_id, handle)

    def transcript(self, handle: str, after: int = 0) -> list[Message]:
        """The messages, or only those stored after message id `after`."""
        candidate = self.candidate(handle)
        messages = self._repo.list_messages(candidate.id) if candidate else []
        return [m for m in messages if m.id is not None and m.id > after]

    def _due_step(self, candidate: Candidate, now: datetime) -> Nudge | Deadline | None:
        messages = self._repo.list_messages(candidate.id)
        silent = silence(messages, self._repo.list_events(candidate.id))
        return due_step(silent.asked_at, silent.last_nudge, now, self._config) if silent else None

    def _send_nudge(self, candidate: Candidate, number: int) -> None:
        first_name = candidate.name.split()[0] if candidate.name else None
        left = questions_left(candidate.state, self._config)
        self._record(candidate, "nudge_sent", number=number)
        self._send(
            candidate, self._config.nudge(candidate.state.language, number, first_name, left)
        )

    def _end_silent_screening(self, candidate: Candidate) -> None:
        """Qualified to review, with its summary, or Abandoned at the stage the candidate
        dropped off at."""
        candidate.status = silent_outcome(candidate.state, self._config)
        if candidate.status == Status.QUALIFIED_TO_REVIEW:
            candidate.state.stage = "closed"
            self._record(candidate, "outcome", status=candidate.status)
            self._summarize(candidate)
        else:
            self._record(candidate, "abandoned", stage=candidate.state.stage)

    def _summarize(self, candidate: Candidate) -> None:
        """A summary failure does not stop the turn: the reply is still sent."""
        try:
            usage = write_summary(candidate, self._config, self._llm)
        except Exception as error:
            self._record(candidate, "llm_failure", error=repr(error))
            return
        self._record_llm_call(candidate, "summarize", usage)

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
        if extraction.intent == "abuse":
            # Not read as an answer: it only counts towards the abuse limit (ADR 0003).
            state.abuse_count += 1
            events.append(self._event(candidate, "abuse"))
            return
        if extraction.intent == "opt_out" and not isinstance(pending, Greet):
            state.opted_out = True
            events.append(self._event(candidate, "opted_out"))
            return
        # A question is forwarded to a recruiter; a message that is only a question uses no
        # attempt, while any answer given with it is still applied.
        asking = extraction.intent == "question"
        only_a_question = asking and extraction.yes_no is None
        if asking:
            state.add_flag("question_for_recruiter")
            question = extraction.question or text
            events.append(self._event(candidate, "question_forwarded", question=question))
        call_accepted = self._apply_sentiment(candidate, extraction, events)
        match pending:
            case Greet():
                if extraction.yes_no is not None:
                    state.consent = extraction.yes_no
                if state.consent:
                    events.append(self._event(candidate, "consent_given"))
            case Ask(field=asked) | FollowUp(field=asked):
                others = [field.type for field in self._config.fields if field.type != asked]
                changed_others = self._apply_fields(
                    candidate, others, extraction, pending, text, events
                )
                # A message only about other fields (e.g. a correction) or accepting the call
                # uses no attempt.
                self._apply_field(
                    candidate,
                    asked,
                    extraction,
                    pending,
                    text,
                    events,
                    uses_attempt=not (changed_others or asking or call_accepted),
                )
            case Confirm(field=confirmed):
                # A new value instead of a yes is validated like any other answer.
                new_value = (
                    extraction.yes_no is not True and getattr(extraction, confirmed) is not None
                )
                if not new_value and not only_a_question:
                    self._apply_confirmation(
                        candidate, confirmed, extraction.yes_no is True, events
                    )
                fields = [
                    field.type
                    for field in self._config.fields
                    if new_value or field.type != confirmed
                ]
                self._apply_fields(candidate, fields, extraction, pending, text, events)
            case Recap() | AskCorrection():
                all_fields = [field.type for field in self._config.fields]
                if self._apply_fields(candidate, all_fields, extraction, pending, text, events):
                    state.recap_attempts = 0  # a correction: a new recap follows
                elif extraction.yes_no is True:
                    state.recap_confirmed = True
                elif not only_a_question:
                    state.recap_attempts += 1

    def _apply_sentiment(
        self, candidate: Candidate, extraction: Extraction, events: list[Event]
    ) -> bool:
        """Flag a frustrated candidate, whose reply offers a call. A yes to that offer asks the
        recruiter for a call and the screening goes on; True when it is accepted now."""
        state = candidate.state
        offered = "frustrated" in state.flags and "wants_human" not in state.flags
        call_accepted = offered and extraction.call_requested is True
        if call_accepted:
            state.add_flag("wants_human")
            events.append(self._event(candidate, "call_requested"))
        if extraction.sentiment == "frustrated":
            state.add_flag("frustrated")
        return call_accepted

    def _apply_fields(
        self,
        candidate: Candidate,
        field_types: list[str],
        extraction: Extraction,
        pending: Action,
        text: str,
        events: list[Event],
    ) -> bool:
        """Apply the extraction to these fields; True when any of them changed."""
        before = dict(candidate.state.fields)
        for field_type in field_types:
            self._apply_field(candidate, field_type, extraction, pending, text, events)
        return candidate.state.fields != before

    def _apply_field(
        self,
        candidate: Candidate,
        field_type: str,
        extraction: Extraction,
        pending: Action,
        text: str,
        events: list[Event],
        uses_attempt: bool = True,
    ) -> None:
        """Validate one field. Attempts are only used by the question asked: an invalid
        answer volunteered for another field is ignored. A value understood below the
        confidence threshold, or a new value for a valid field (a correction), waits for
        the candidate's confirmation instead of being kept, as does a value the validator is
        unsure of (a close service-area match)."""
        extracted = getattr(extraction, field_type)
        # A new answer replaces a value still waiting for confirmation.
        current = candidate.state.field(field_type).model_copy(update={"unconfirmed": None})
        asked = isinstance(pending, Ask | FollowUp) and pending.field == field_type
        if extracted is not None:
            verdict = self._validate(field_type, extracted.value, current)
            if verdict.status == "invalid" and not asked:
                return
            updated = update_field(current, verdict, extracted.raw_answer, extracted.confidence)
            if verdict.status != "invalid" and (
                current.status == "valid"
                or verdict.unsure
                or extracted.confidence < self._config.confidence_threshold
            ):
                if current.status == "valid" and updated.value == current.value:
                    # The same value again: nothing to correct (it drops a correction).
                    candidate.state.fields[field_type] = current
                    return
                candidate.state.fields[field_type] = current.model_copy(
                    update={"unconfirmed": updated}
                )
                return
        elif asked and uses_attempt:
            # Nothing usable for the question asked (e.g. a failed coercion): an invalid
            # answer, and an unanswered follow-up still uses up the one follow-up.
            updated = update_field(current, INVALID, raw_answer=text)
        else:
            return
        self._set_field(candidate, field_type, updated, events)

    def _validate(
        self, field_type: str, value: FieldValue | Place | date, current: FieldState
    ) -> Verdict:
        if field_type == "service_area":
            return validate_service_area(value, current, self._config.service_areas)
        return VALIDATORS[field_type](value, current, self._clock.now().date())

    def _apply_confirmation(
        self, candidate: Candidate, field_type: str, confirmed: bool, events: list[Event]
    ) -> None:
        """Yes keeps the unconfirmed value. No drops it: a correction keeps the previous value,
        an unsure first answer uses one attempt and is asked again."""
        field = candidate.state.field(field_type)
        previous = field.model_copy(update={"unconfirmed": None})
        if confirmed:
            self._set_field(
                candidate, field_type, field.unconfirmed, events, corrected=field.status == "valid"
            )
        elif field.status == "valid":
            candidate.state.fields[field_type] = previous
        else:
            updated = update_field(previous, INVALID, raw_answer=field.unconfirmed.raw_answer)
            self._set_field(candidate, field_type, updated, events)

    def _set_field(
        self,
        candidate: Candidate,
        field_type: str,
        updated: FieldState,
        events: list[Event],
        corrected: bool = False,
    ) -> None:
        candidate.state.fields[field_type] = updated
        if updated.status == "valid":
            if field_type == "name":
                candidate.name = updated.value
            if field_type == "service_area":
                candidate.city = updated.value.city
            event_type = "field_corrected" if corrected else "field_captured"
            events.append(self._event(candidate, event_type, field=field_type))
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
        self._repo.add_message(candidate.id, self._message(candidate, role, content))

    def _message(
        self, candidate: Candidate, role: Literal["candidate", "agent"], content: str
    ) -> Message:
        return Message(
            role=role,
            content=content,
            language=candidate.state.language,
            created_at=self._clock.now(),
        )

    def _record(self, candidate: Candidate, event_type: str, **payload: JsonValue) -> None:
        self._repo.add_event(candidate.id, self._event(candidate, event_type, **payload))

    def _record_llm_call(self, candidate: Candidate, call: str, usage: LLMUsage) -> None:
        self._record(candidate, "llm_call", call=call, **usage.model_dump())

    def _event(self, candidate: Candidate, event_type: str, **payload: JsonValue) -> Event:
        return Event(
            type=event_type,
            stage=candidate.state.stage,
            payload=payload,
            created_at=self._clock.now(),
        )
