from datetime import UTC, datetime

import pytest

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.screening_service import ScreeningService
from app.domain.models import Extracted, Extraction, Status

PHONE = "+34 600 111 222"
HANDLE = "34600111222"


def make_service(script: list[Extraction] | None = None):
    repo = SqliteCandidateRepository(create_sqlite_engine("sqlite://"))
    service = ScreeningService(
        config=load_client_config("grupo_sazon"),
        llm=FakeLLM(script),
        repo=repo,
        clock=FixedClock(datetime(2026, 9, 26, 10, 0, tzinfo=UTC)),
    )
    return service, repo


def test_applying_creates_the_candidate_and_sends_the_greeting():
    service, _ = make_service()

    candidate = service.apply(PHONE, name="Ana")

    assert candidate.handle == HANDLE
    assert candidate.status == Status.IN_PROGRESS
    [greeting] = service.transcript(HANDLE)
    assert greeting.role == "agent"
    assert "Lucía" in greeting.content and "Grupo Sazón" in greeting.content


def test_happy_path_ends_qualified():
    service, repo = make_service()
    service.apply(PHONE)

    assert service.handle_message(HANDLE, "yes") == "[fake] ask:name (attempt 0)"
    assert service.handle_message(HANDLE, "Ana López") == "[fake] recap"
    assert service.handle_message(HANDLE, "yes") == "[fake] close:qualified"

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.QUALIFIED
    assert candidate.name == "Ana López"
    assert candidate.state.stage == "closed"
    assert len(service.transcript(HANDLE)) == 7
    assert [e.type for e in repo.list_events(candidate.id)] == [
        "application_received",
        "consent_given",
        "field_captured",
        "outcome",
    ]


def test_declining_consent_deletes_the_candidate():
    service, repo = make_service()
    candidate = service.apply(PHONE)

    reply = service.handle_message(HANDLE, "no")

    assert reply == "[fake] close:consent_declined"
    assert service.candidate(HANDLE) is None
    assert service.transcript(HANDLE) == []
    assert repo.list_events(candidate.id) == []


def test_applying_again_with_the_same_handle_resumes_the_screening():
    service, _ = make_service()
    first = service.apply(PHONE)
    service.handle_message(HANDLE, "yes")

    again = service.apply("34-600-111-222")

    assert again.id == first.id
    assert len(service.transcript(HANDLE)) == 3
    assert service.handle_message(HANDLE, "Ana López") == "[fake] recap"


def test_first_name_only_gets_one_surname_follow_up():
    service, _ = make_service()
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")

    assert service.handle_message(HANDLE, "Ana") == "[fake] follow_up:name (surname)"
    assert service.handle_message(HANDLE, "López") == "[fake] recap"
    assert service.candidate(HANDLE).name == "Ana López"


def test_missing_surname_after_the_follow_up_is_accepted_with_a_flag():
    service, _ = make_service()
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")
    service.handle_message(HANDLE, "Ana")

    assert service.handle_message(HANDLE, "prefiero no, 123") == "[fake] recap"

    name = service.candidate(HANDLE).state.fields["name"]
    assert name.value == "Ana"
    assert name.flags == ["surname_missing"]


def test_an_invalid_name_is_asked_again():
    service, _ = make_service()
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")

    assert service.handle_message(HANDLE, "12345") == "[fake] ask:name (attempt 1)"


def test_extraction_fills_the_field_and_the_reply_follows_its_language():
    service, _ = make_service(
        script=[
            Extraction(language="en", yes_no=True),
            Extraction(
                language="en",
                name=Extracted(value="Ana López", raw_answer="I'm Ana López", confidence=0.9),
            ),
        ]
    )
    service.apply(PHONE)
    service.handle_message(HANDLE, "sure")
    service.handle_message(HANDLE, "I'm Ana López")

    candidate = service.candidate(HANDLE)
    assert candidate.state.fields["name"].raw_answer == "I'm Ana López"
    assert candidate.state.stage == "recap"
    assert service.transcript(HANDLE)[-1].language == "en"


def test_a_handle_needs_digits():
    service, _ = make_service()

    with pytest.raises(ValueError):
        service.apply("no phone")


def test_a_follow_up_answer_with_no_name_accepts_the_first_name_with_a_flag():
    service, _ = make_service(
        script=[
            Extraction(yes_no=True),
            Extraction(name=Extracted(value="Ana", raw_answer="Ana", confidence=1.0)),
            Extraction(),
        ]
    )
    service.apply(PHONE)
    service.handle_message(HANDLE, "sí")
    service.handle_message(HANDLE, "Ana")

    assert service.handle_message(HANDLE, "prefiero no decirlo") == "[fake] recap"
    assert service.candidate(HANDLE).state.fields["name"].flags == ["surname_missing"]
