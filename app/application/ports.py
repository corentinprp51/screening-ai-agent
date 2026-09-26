"""The three ports. Adapters implement them; the services depend only on them."""

from datetime import datetime
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


class LLMPort(Protocol):
    def extract(self, message: str, action: Action, state: CandidateState) -> Extraction:
        """Understand a candidate message answering `action`."""
        ...

    def reply(self, action: Action, state: CandidateState, language: Language) -> str:
        """Write the message for an action chosen by code."""
        ...

    def summarize(self, facts: dict[str, JsonValue], language: Language) -> str:
        """Phrase a recruiter summary from facts computed by code."""
        ...


class CandidateRepository(Protocol):
    def get(self, candidate_id: int) -> Candidate | None: ...

    def get_by_handle(self, client_id: str, handle: str) -> Candidate | None: ...

    def list_candidates(self, client_id: str, status: Status | None = None) -> list[Candidate]:
        """Most recent activity first."""
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


class Clock(Protocol):
    def now(self) -> datetime: ...
