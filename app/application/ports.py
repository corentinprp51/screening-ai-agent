"""The three ports. Adapters implement them; the services depend only on them."""

from datetime import date, datetime
from typing import Protocol

from pydantic import JsonValue

from app.domain.flow import Action
from app.domain.models import (
    Candidate,
    CandidateState,
    Event,
    Extraction,
    Language,
    Message,
    Status,
)

# How many of the latest messages `reply` gets to phrase in the flow of the conversation.
RECENT_MESSAGES = 6


class LLMPort(Protocol):
    def extract(
        self,
        message: str,
        action: Action,
        state: CandidateState,
        today: date,
        last_agent_message: str | None,
    ) -> Extraction:
        """Understand a candidate message answering `action`, asked as `last_agent_message`."""
        ...

    def reply(
        self,
        action: Action,
        state: CandidateState,
        language: Language,
        transcript: list[Message],
    ) -> str:
        """Write the message for an action chosen by code, following the recent `transcript`."""
        ...

    def summarize(self, facts: dict[str, JsonValue], language: Language) -> str:
        """Phrase a recruiter summary from facts computed by code."""
        ...


class CandidateRepository(Protocol):
    def get(self, candidate_id: int) -> Candidate | None: ...

    def get_by_handle(self, client_id: str, handle: str) -> Candidate | None: ...

    def list_candidates(self, client_id: str, status: Status | None = None) -> list[Candidate]:
        """Highest priority score first, then most recent activity."""
        ...

    def save(self, candidate: Candidate) -> Candidate:
        """Insert or update; returns the candidate with its id."""
        ...

    def delete(self, candidate_id: int) -> None:
        """Delete the candidate, its messages and its events."""
        ...

    def add_message(self, candidate_id: int, message: Message) -> None: ...

    def list_messages(self, candidate_id: int) -> list[Message]: ...

    def add_event(self, candidate_id: int, event: Event) -> None: ...

    def list_events(self, candidate_id: int) -> list[Event]: ...

    def add_consent_drop_off(self, client_id: str, at: datetime) -> None:
        """Count a greeting left unanswered, keeping nothing about the candidate."""
        ...

    def count_consent_drop_offs(self, client_id: str) -> int: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
