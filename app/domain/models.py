"""Domain models: candidate state, the extraction contract and client config."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

Language = Literal["es", "en"]


class Status(StrEnum):
    IN_PROGRESS = "in_progress"
    QUALIFIED = "qualified"


# --- Client config (loaded from YAML by the config adapter) ---


class Persona(BaseModel):
    agent_name: str
    client_name: str


class FieldConfig(BaseModel):
    type: Literal["name"]


class Templates(BaseModel):
    greeting: dict[Language, str]


class ClientConfig(BaseModel):
    client_id: str
    persona: Persona
    default_language: Language
    fields: list[FieldConfig] = Field(min_length=1)
    templates: Templates

    def greeting(self, language: Language) -> str:
        return self.templates.greeting[language].format(
            agent_name=self.persona.agent_name, client_name=self.persona.client_name
        )


# --- Candidate state (stored as state_json) ---


class FieldState(BaseModel):
    """What the screening knows about one field.

    `incomplete` means one follow-up is pending for the `missing` part.
    """

    status: Literal["empty", "incomplete", "valid"] = "empty"
    value: Any = None
    raw_answer: str | None = None
    confidence: float | None = None
    missing: str | None = None
    attempts: int = 0
    flags: list[str] = Field(default_factory=list)


class CandidateState(BaseModel):
    consent: bool | None = None
    fields: dict[str, FieldState] = Field(default_factory=dict)
    recap_confirmed: bool = False
    language: Language = "es"
    stage: str = "consent"

    def field(self, field_type: str) -> FieldState:
        return self.fields.get(field_type, FieldState())


class Candidate(BaseModel):
    id: int | None = None
    client_id: str
    handle: str
    name: str | None = None
    status: Status = Status.IN_PROGRESS
    state: CandidateState = Field(default_factory=CandidateState)
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    role: Literal["candidate", "agent"]
    content: str
    language: Language
    created_at: datetime


class Event(BaseModel):
    type: str
    stage: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- Extraction contract (the LLM output type) ---


class Extracted[T](BaseModel):
    value: T
    raw_answer: str
    confidence: float = Field(ge=0, le=1)


class Extraction(BaseModel):
    """Everything the LLM may understand from one candidate message."""

    language: Language = "es"
    intent: Literal["answer", "opt_out"] = "answer"
    yes_no: bool | None = None
    name: Extracted[str] | None = None
