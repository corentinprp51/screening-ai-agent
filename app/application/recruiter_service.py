"""The recruiter side: the candidate queue and a candidate's detail (read-only)."""

from datetime import datetime

from pydantic import BaseModel

from app.application.ports import CandidateRepository
from app.application.screening_service import UnknownCandidate
from app.domain.fields import format_value
from app.domain.models import ClientConfig, Event, FieldStatus, FieldValue, Message, Status


class QueueRow(BaseModel):
    id: int
    handle: str
    name: str | None
    city: str | None = None  # filled by the service-area ticket
    status: Status
    stage: str
    score: int | None = None  # filled by the priority-score ticket
    flags: list[str]
    last_activity: datetime


class FieldView(BaseModel):
    field: str
    value: FieldValue | None
    display: str
    raw_answer: str | None
    confidence: float | None
    verdict: FieldStatus
    needs_review: bool
    flags: list[str]


class CandidateDetail(BaseModel):
    id: int
    handle: str
    name: str | None
    status: Status
    stage: str
    fields: list[FieldView]
    flags: list[str]
    messages: list[Message]
    events: list[Event]


class RecruiterService:
    def __init__(self, config: ClientConfig, repo: CandidateRepository) -> None:
        self._config = config
        self._repo = repo

    def queue(self, status: Status | None = None) -> list[QueueRow]:
        """Every candidate of the client, most recent activity first."""
        return [
            QueueRow(
                id=candidate.id,
                handle=candidate.handle,
                name=candidate.name,
                status=candidate.status,
                stage=candidate.state.stage,
                flags=candidate.state.all_flags(),
                last_activity=candidate.updated_at,
            )
            for candidate in self._repo.list_candidates(self._config.client_id, status)
        ]

    def detail(self, candidate_id: int) -> CandidateDetail:
        candidate = self._repo.get(candidate_id)
        if candidate is None or candidate.client_id != self._config.client_id:
            raise UnknownCandidate(candidate_id)
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
            fields=fields,
            flags=candidate.state.all_flags(),
            messages=self._repo.list_messages(candidate_id),
            events=self._repo.list_events(candidate_id),
        )
