"""The recruiter side: the candidate queue, a candidate's detail, confirming or
overriding a proposed rejection, and the Impact view."""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, JsonValue

from app.application.ports import (
    RECENT_MESSAGES,
    CandidateRepository,
    Clock,
    LLMPort,
    LLMUsage,
)
from app.application.screening_service import UnknownCandidate, write_summary
from app.domain.fields import KNOCK_OUTS, format_value
from app.domain.flow import Action, Close, next_action, stage_of
from app.domain.models import (
    Candidate,
    ClientConfig,
    Event,
    FieldStatus,
    FieldValue,
    Message,
    Status,
)
from app.domain.summary import recruiter_action


class NotRejectionProposed(ValueError):
    """Confirm and Override only apply to a candidate in Rejection proposed."""


class LLMUnavailable(RuntimeError):
    """The message for a recruiter action could not be written; nothing was changed."""


class QueueRow(BaseModel):
    id: int
    handle: str
    name: str | None
    city: str | None = None
    status: Status
    stage: str
    score: int
    flags: list[str]
    last_activity: datetime
    rule: str | None = None  # the failed knock-out, in Rejection proposed
    answer: str | None = None  # the candidate's answer to it
    summary: str | None = None
    next_action: str | None = None


class FieldView(BaseModel):
    field: str
    value: FieldValue | None
    display: str
    raw_answer: str | None
    confidence: float | None
    verdict: FieldStatus
    needs_review: bool
    flags: list[str]


class ScoreLine(BaseModel):
    """One field's part of the priority score: why the candidate ranks where they do."""

    field: str
    display: str
    points: int
    weight: int


class CandidateDetail(BaseModel):
    id: int
    handle: str
    name: str | None
    status: Status
    stage: str
    score: int
    breakdown: list[ScoreLine]
    fields: list[FieldView]
    flags: list[str]
    rule: str | None = None
    answer: str | None = None
    summary: str | None = None
    next_action: str | None = None
    messages: list[Message]
    events: list[Event]


# The Impact view covers the pilot period.
IMPACT_DAYS = 30
# A completed screening: the questions stopped with the candidate still engaged.
COMPLETED = (
    Status.QUALIFIED,
    Status.QUALIFIED_TO_REVIEW,
    Status.REJECTION_PROPOSED,
    Status.REJECTED,
)


class Metric(BaseModel):
    """One section 1 figure, next to today's phone baseline and the pilot target. `value`
    is None when there is nothing to count yet."""

    name: str
    label: str
    unit: Literal["share", "minutes", "hours", "money"]
    value: float | None
    baseline: float | None = None
    target: float | None = None
    note: str


class Impact(BaseModel):
    days: int
    since: datetime
    currency: str
    candidates: int  # applied since, still kept (a declined consent is erased)
    consent_drop_offs: int  # greetings left unanswered, erased and only counted
    contacted: int  # both
    consented: int
    completed: int
    metrics: list[Metric]


class RecruiterService:
    def __init__(
        self, config: ClientConfig, llm: LLMPort, repo: CandidateRepository, clock: Clock
    ) -> None:
        self._config = config
        self._llm = llm
        self._repo = repo
        self._clock = clock

    def queue(self, status: Status | None = None) -> list[QueueRow]:
        """Every candidate of the client, highest priority score first, then most recent
        activity."""
        return [
            QueueRow(
                id=candidate.id,
                handle=candidate.handle,
                name=candidate.name,
                city=candidate.city,
                status=candidate.status,
                stage=candidate.state.stage,
                score=candidate.score.total,
                flags=candidate.state.all_flags(),
                last_activity=candidate.updated_at,
                **self._failed_knock_out(candidate),
                **self._handoff(candidate),
            )
            for candidate in self._repo.list_candidates(self._config.client_id, status)
        ]

    def detail(self, candidate_id: int) -> CandidateDetail:
        candidate = self._get(candidate_id)
        fields = []
        for field_config in self._config.fields:
            field = candidate.state.field(field_config.type)
            fields.append(
                FieldView(
                    field=field_config.type,
                    value=field.value,
                    display=format_value(field.value),
                    raw_answer=field.raw_answer,
                    confidence=field.confidence,
                    verdict=field.status,
                    needs_review=field.status == "needs_review",
                    flags=field.flags,
                )
            )
        return CandidateDetail(
            id=candidate_id,
            handle=candidate.handle,
            name=candidate.name,
            status=candidate.status,
            stage=candidate.state.stage,
            score=candidate.score.total,
            breakdown=[
                ScoreLine(
                    field=field,
                    display=format_value(candidate.state.field(field).value),
                    points=points,
                    weight=getattr(self._config.scoring.weights, field),
                )
                for field, points in candidate.score.points.items()
            ],
            fields=fields,
            flags=candidate.state.all_flags(),
            **self._failed_knock_out(candidate),
            **self._handoff(candidate),
            messages=self._repo.list_messages(candidate_id),
            events=self._repo.list_events(candidate_id),
        )

    def confirm_rejection(self, candidate_id: int) -> None:
        """Reject the candidate and send the rejection message for the failed rule."""
        candidate, proposal = self._proposal(candidate_id)
        rejection = Close(
            status=Status.REJECTED,
            reason=proposal.reason,
            field=proposal.field,
            offer_contact=KNOCK_OUTS[proposal.field].offers_contact,
        )
        reply = self._reply(candidate, rejection)
        candidate.status = Status.REJECTED
        self._record(candidate, "outcome", status=Status.REJECTED, rule=proposal.reason)
        self._send(candidate, reply)

    def override_rejection(self, candidate_id: int) -> None:
        """Set the failed knock-out aside for good and send the next question right away."""
        candidate, proposal = self._proposal(candidate_id)
        candidate.state.overridden_knock_outs.append(proposal.reason)
        action = next_action(candidate.state, self._config)
        reply = self._reply(candidate, action)
        self._record(candidate, "knock_out_overridden", rule=proposal.reason)
        candidate.state.stage = stage_of(action)
        if isinstance(action, Close) and action.status == Status.REJECTION_PROPOSED:
            # Another knock-out had already failed (a volunteered answer): propose it now.
            answer = candidate.state.field(action.field).raw_answer
            self._record(candidate, "rejection_proposed", rule=action.reason, answer=answer)
            try:
                usage = write_summary(candidate, self._config, self._llm)
            except Exception as error:
                self._record(candidate, "llm_failure", error=repr(error))
            else:
                self._record_llm_call(candidate, "summarize", usage)
        else:
            candidate.status = Status.IN_PROGRESS
            candidate.summary = None  # a new one is written when the questions stop again
        self._send(candidate, reply)

    def impact(self) -> Impact:
        """The section 1 metrics over the last 30 days, from the candidates who applied
        since, their messages and events, and the consent drop-offs."""
        baselines = self._config.impact
        since = self._clock.now() - timedelta(days=IMPACT_DAYS)
        candidates = [
            candidate
            for candidate in self._repo.list_candidates(self._config.client_id)
            if candidate.created_at >= since
        ]
        # A drop-off is counted at the deadline, that long after its application.
        drop_offs = self._repo.count_consent_drop_offs(
            self._config.client_id, since + timedelta(hours=self._config.deadline_hours)
        )
        consented = [c for c in candidates if c.state.consent]
        completed = [c for c in candidates if c.status in COMPLETED]
        qualified = [
            c for c in completed if c.status in (Status.QUALIFIED, Status.QUALIFIED_TO_REVIEW)
        ]
        needs_review = [
            c
            for c in completed
            if any(field.status == "needs_review" for field in c.state.fields.values())
        ]

        first_message_delays = [
            (first.created_at - c.created_at).total_seconds() / 60
            for c in candidates
            if (first := next(iter(self._repo.list_messages(c.id)), None))
        ]
        call_hours = baselines.call_minutes / 60
        # Every contacted candidate would have been called: a completed screening saves
        # its call, and each unanswered attempt saves a call slot.
        contacted = len(candidates) + drop_offs
        saved_calls = len(completed) + contacted * baselines.unanswered_calls_per_candidate
        calls_per_week = (
            baselines.recruiters
            * baselines.calls_per_recruiter_per_day
            * baselines.working_days_per_week
        )
        llm_cost = sum(self._llm_cost(c) for c in candidates)

        return Impact(
            days=IMPACT_DAYS,
            since=since,
            currency=baselines.currency,
            candidates=len(candidates),
            consent_drop_offs=drop_offs,
            contacted=contacted,
            consented=len(consented),
            completed=len(completed),
            metrics=[
                Metric(
                    name="completion_rate",
                    label="Completion rate",
                    unit="share",
                    value=_ratio(len(completed), len(consented)),
                    baseline=1 - baselines.no_answer_rate,
                    target=baselines.targets.completion_rate,
                    note="Completed screenings out of the candidates who gave consent.",
                ),
                Metric(
                    name="first_message_minutes",
                    label="Time from application to first message",
                    unit="minutes",
                    value=_ratio(sum(first_message_delays), len(first_message_delays)),
                    target=baselines.targets.first_message_minutes,
                    note="Average delay of the greeting after the application.",
                ),
                Metric(
                    name="hours_saved_per_week",
                    label="Recruiter hours saved per week",
                    unit="hours",
                    value=saved_calls * call_hours * 7 / IMPACT_DAYS,
                    baseline=calls_per_week * call_hours,
                    note=(
                        "Completed screenings plus the unanswered call attempts avoided, "
                        "times the average call; the baseline is the recruiters' weekly "
                        "call time."
                    ),
                ),
                Metric(
                    name="qualified_time_share",
                    label="Recruiter time on qualified candidates (estimate)",
                    unit="share",
                    value=_ratio(len(qualified), len(completed)),
                    baseline=1 - baselines.unqualified_time_share,
                    target=baselines.targets.qualified_time_share,
                    note=(
                        "Qualified and Qualified to review out of the completed screenings, "
                        "assuming a recruiter spends as long on each."
                    ),
                ),
                Metric(
                    name="needs_review_share",
                    label="Screenings with a field to review",
                    unit="share",
                    value=_ratio(len(needs_review), len(completed)),
                    target=baselines.targets.needs_review_share,
                    note="Completed screenings with at least one needs-review field.",
                ),
                Metric(
                    name="llm_cost_per_candidate",
                    label="LLM cost per candidate",
                    unit="money",
                    value=_ratio(llm_cost, len(candidates)),
                    baseline=call_hours * baselines.recruiter_hourly_cost,
                    note=(
                        "Recorded token usage priced with the config; the baseline is one "
                        "recruiter call."
                    ),
                ),
            ],
        )

    def _llm_cost(self, candidate: Candidate) -> float:
        """The candidate's recorded `llm_call` tokens, priced with the config."""
        prices = self._config.impact.llm_price_per_million_tokens
        usages = [
            LLMUsage.model_validate(event.payload)
            for event in self._repo.list_events(candidate.id)
            if event.type == "llm_call"
        ]
        return sum(
            (usage.input_tokens * prices.input + usage.output_tokens * prices.output) / 1_000_000
            for usage in usages
        )

    def _get(self, candidate_id: int) -> Candidate:
        candidate = self._repo.get(candidate_id)
        if candidate is None or candidate.client_id != self._config.client_id:
            raise UnknownCandidate(candidate_id)
        return candidate

    def _proposal(self, candidate_id: int) -> tuple[Candidate, Close]:
        """The candidate and the proposed rejection, recomputed from the state."""
        candidate = self._get(candidate_id)
        proposal = next_action(candidate.state, self._config)
        if candidate.status != Status.REJECTION_PROPOSED or not isinstance(proposal, Close):
            raise NotRejectionProposed(candidate_id)
        return candidate, proposal

    def _failed_knock_out(self, candidate: Candidate) -> dict[str, str | None]:
        """The rule and the candidate's answer, for a candidate in Rejection proposed."""
        if candidate.status != Status.REJECTION_PROPOSED:
            return {}
        proposal = next_action(candidate.state, self._config)
        if not isinstance(proposal, Close) or proposal.field is None:
            return {}
        return {
            "rule": proposal.reason,
            "answer": candidate.state.field(proposal.field).raw_answer,
        }

    def _handoff(self, candidate: Candidate) -> dict[str, str | None]:
        """The summary text and the next action, computed from the status."""
        return {
            "summary": candidate.summary.text if candidate.summary else None,
            "next_action": recruiter_action(candidate.status, candidate.state, self._config),
        }

    def _reply(self, candidate: Candidate, action: Action) -> str:
        """The LLM message for the action. On failure the candidate is flagged, nothing
        else changes and the recruiter can try again."""
        recent = self._repo.list_messages(candidate.id)[-RECENT_MESSAGES:]
        try:
            reply, usage = self._llm.reply(
                action, candidate.state, candidate.state.language, recent
            )
        except Exception as error:
            stored = self._get(candidate.id)  # drop the in-memory changes
            stored.state.add_flag("llm_failure")
            self._record(stored, "llm_failure", error=repr(error))
            self._repo.save(stored)
            raise LLMUnavailable(candidate.id) from error
        self._record_llm_call(candidate, "reply", usage)
        return reply

    def _send(self, candidate: Candidate, content: str) -> None:
        """Save the candidate and store the agent message: the chat picks it up by polling."""
        now = self._clock.now()
        candidate.updated_at = now
        self._repo.save(candidate)
        self._repo.add_message(
            candidate.id,
            Message(
                role="agent", content=content, language=candidate.state.language, created_at=now
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

    def _record_llm_call(self, candidate: Candidate, call: str, usage: LLMUsage) -> None:
        self._record(candidate, "llm_call", call=call, **usage.model_dump())


def _ratio(part: float, whole: float) -> float | None:
    return part / whole if whole else None
