"""The recruiter side: the candidate queue, a candidate's detail, and confirming or
overriding a proposed rejection."""

from datetime import datetime

from pydantic import BaseModel, JsonValue

from app.application.ports import CandidateRepository, Clock, LLMPort
from app.application.screening_service import UnknownCandidate
from app.domain.fields import format_value
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


class NotRejectionProposed(ValueError):
    """Confirm and Override only apply to a candidate in Rejection proposed."""


class LLMUnavailable(RuntimeError):
    """The message for a recruiter action could not be written; nothing was changed."""


class QueueRow(BaseModel):
    id: int
    handle: str
    name: str | None
    city: str | None = None  # filled by the service-area ticket
    status: Status
    stage: str
    score: int
    flags: list[str]
    last_activity: datetime
    rule: str | None = None  # the failed knock-out, in Rejection proposed
    answer: str | None = None  # the candidate's answer to it


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
    messages: list[Message]
    events: list[Event]


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
                status=candidate.status,
                stage=candidate.state.stage,
                score=candidate.score.total,
                flags=candidate.state.all_flags(),
                last_activity=candidate.updated_at,
                **self._failed_knock_out(candidate),
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
            messages=self._repo.list_messages(candidate_id),
            events=self._repo.list_events(candidate_id),
        )

    def confirm_rejection(self, candidate_id: int) -> None:
        """Reject the candidate and send the rejection message for the failed rule."""
        candidate, proposal = self._proposal(candidate_id)
        rejection = Close(status=Status.REJECTED, reason=proposal.reason, field=proposal.field)
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
        else:
            candidate.status = Status.IN_PROGRESS
        self._send(candidate, reply)

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

    def _reply(self, candidate: Candidate, action: Action) -> str:
        """The LLM message for the action. On failure the candidate is flagged, nothing
        else changes and the recruiter can try again."""
        try:
            return self._llm.reply(action, candidate.state, candidate.state.language)
        except Exception as error:
            stored = self._get(candidate.id)  # drop the in-memory changes
            stored.state.add_flag("llm_failure")
            self._record(stored, "llm_failure", error=repr(error))
            self._repo.save(stored)
            raise LLMUnavailable(candidate.id) from error

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
