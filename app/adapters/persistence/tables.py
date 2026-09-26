from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint


class CandidateRow(SQLModel, table=True):
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("client_id", "handle"),)

    id: int | None = Field(default=None, primary_key=True)
    client_id: str = Field(index=True)
    handle: str
    name: str | None = None
    city: str | None = None
    status: str = Field(index=True)
    stage: str
    score: int = Field(index=True)
    state_json: str
    score_json: str  # the breakdown: points per field
    summary_json: str | None = None  # the summary and the facts it was phrased from
    created_at: datetime
    updated_at: datetime


class MessageRow(SQLModel, table=True):
    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    candidate_id: int = Field(foreign_key="candidates.id", index=True)
    role: str
    content: str
    language: str
    created_at: datetime


class EventRow(SQLModel, table=True):
    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    candidate_id: int = Field(foreign_key="candidates.id", index=True)
    type: str = Field(index=True)
    stage: str
    payload_json: str
    created_at: datetime
