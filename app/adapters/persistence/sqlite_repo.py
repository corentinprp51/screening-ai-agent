import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, col, create_engine, delete, func, select

from app.adapters.persistence.tables import (
    CandidateRow,
    ConsentDropOffRow,
    EventRow,
    MessageRow,
)
from app.domain.models import (
    Candidate,
    CandidateState,
    Event,
    Message,
    Score,
    Status,
    Summary,
)


def create_sqlite_engine(url: str) -> Engine:
    """Create the engine and the tables (no migrations: delete data/*.db on schema change).

    `sqlite://` is an in-memory database shared by every session, for tests.
    """
    if url == "sqlite://":
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


class SqliteCandidateRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, candidate_id: int) -> Candidate | None:
        with Session(self._engine) as session:
            row = session.get(CandidateRow, candidate_id)
            return _to_candidate(row) if row else None

    def list_candidates(self, client_id: str, status: Status | None = None) -> list[Candidate]:
        with Session(self._engine) as session:
            query = select(CandidateRow).where(CandidateRow.client_id == client_id)
            if status is not None:
                query = query.where(CandidateRow.status == status)
            rows = session.exec(
                query.order_by(
                    col(CandidateRow.score).desc(),
                    col(CandidateRow.updated_at).desc(),
                    col(CandidateRow.id).desc(),
                )
            ).all()
            return [_to_candidate(row) for row in rows]

    def get_by_handle(self, client_id: str, handle: str) -> Candidate | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(CandidateRow).where(
                    CandidateRow.client_id == client_id, CandidateRow.handle == handle
                )
            ).first()
            return _to_candidate(row) if row else None

    def save(self, candidate: Candidate) -> Candidate:
        with Session(self._engine) as session:
            row = session.get(CandidateRow, candidate.id) if candidate.id else CandidateRow()
            row.client_id = candidate.client_id
            row.handle = candidate.handle
            row.name = candidate.name
            row.city = candidate.city
            row.status = candidate.status
            row.stage = candidate.state.stage
            row.score = candidate.score.total
            row.state_json = candidate.state.model_dump_json()
            row.score_json = candidate.score.model_dump_json()
            row.summary_json = candidate.summary.model_dump_json() if candidate.summary else None
            row.created_at = candidate.created_at
            row.updated_at = candidate.updated_at
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_candidate(row)

    def delete(self, candidate_id: int) -> None:
        with Session(self._engine) as session:
            session.exec(delete(MessageRow).where(MessageRow.candidate_id == candidate_id))
            session.exec(delete(EventRow).where(EventRow.candidate_id == candidate_id))
            session.exec(delete(CandidateRow).where(CandidateRow.id == candidate_id))
            session.commit()

    def add_message(self, candidate_id: int, message: Message) -> None:
        with Session(self._engine) as session:
            session.add(MessageRow(candidate_id=candidate_id, **message.model_dump()))
            session.commit()

    def list_messages(self, candidate_id: int) -> list[Message]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(MessageRow)
                .where(MessageRow.candidate_id == candidate_id)
                .order_by(MessageRow.id)
            ).all()
            return [Message.model_validate(row, from_attributes=True) for row in rows]

    def add_event(self, candidate_id: int, event: Event) -> None:
        with Session(self._engine) as session:
            session.add(
                EventRow(
                    candidate_id=candidate_id,
                    type=event.type,
                    stage=event.stage,
                    payload_json=json.dumps(event.payload),
                    created_at=event.created_at,
                )
            )
            session.commit()

    def list_events(self, candidate_id: int) -> list[Event]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(EventRow).where(EventRow.candidate_id == candidate_id).order_by(EventRow.id)
            ).all()
            return [
                Event(
                    type=row.type,
                    stage=row.stage,
                    payload=json.loads(row.payload_json),
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def add_consent_drop_off(self, client_id: str, at: datetime) -> None:
        with Session(self._engine) as session:
            session.add(ConsentDropOffRow(client_id=client_id, created_at=at))
            session.commit()

    def count_consent_drop_offs(self, client_id: str) -> int:
        with Session(self._engine) as session:
            return session.exec(
                select(func.count())
                .select_from(ConsentDropOffRow)
                .where(ConsentDropOffRow.client_id == client_id)
            ).one()


def _to_candidate(row: CandidateRow) -> Candidate:
    return Candidate(
        id=row.id,
        client_id=row.client_id,
        handle=row.handle,
        name=row.name,
        city=row.city,
        status=row.status,
        state=CandidateState.model_validate_json(row.state_json),
        score=Score.model_validate_json(row.score_json),
        summary=Summary.model_validate_json(row.summary_json) if row.summary_json else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
