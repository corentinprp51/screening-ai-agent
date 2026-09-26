from datetime import UTC, datetime

import pytest

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.screening_service import ScreeningService
from app.domain.models import Experience, Extracted, Extraction, Status

PHONE = "+34 600 111 222"
HANDLE = "34600111222"
RECAP = (
    "[fake] recap: name=Ana López; availability=full_time; schedule=evening; "
    "experience=2 years; start_date=immediate"
)


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


def answer(service, *texts):
    """Send each message in turn; return the last reply."""
    for text in texts:
        reply = service.handle_message(HANDLE, text)
    return reply


def test_happy_path_asks_every_field_in_order_and_ends_qualified():
    service, repo = make_service()
    service.apply(PHONE)

    assert service.handle_message(HANDLE, "yes") == "[fake] ask:name (attempt 0)"
    assert service.handle_message(HANDLE, "Ana López") == "[fake] ask:availability (attempt 0)"
    assert service.handle_message(HANDLE, "full_time") == "[fake] ask:schedule (attempt 0)"
    assert service.handle_message(HANDLE, "evening") == "[fake] ask:experience (attempt 0)"
    assert service.handle_message(HANDLE, "2") == "[fake] ask:start_date (attempt 0)"
    assert service.handle_message(HANDLE, "immediate") == RECAP
    assert service.handle_message(HANDLE, "yes") == "[fake] close:qualified"

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.QUALIFIED
    assert candidate.name == "Ana López"
    assert candidate.state.stage == "closed"
    assert len(service.transcript(HANDLE)) == 15
    assert [e.type for e in repo.list_events(candidate.id)] == [
        "application_received",
        "consent_given",
        *["field_captured"] * 5,
        "outcome",
    ]


def test_three_invalid_answers_mark_the_field_needs_review_and_end_qualified_to_review():
    service, repo = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "full_time")

    assert service.handle_message(HANDLE, "de noche") == "[fake] ask:schedule (attempt 1)"
    assert service.handle_message(HANDLE, "cuando sea") == "[fake] ask:schedule (attempt 2)"
    assert service.handle_message(HANDLE, "no sé") == "[fake] ask:experience (attempt 0)"
    answer(service, "2", "immediate")
    assert service.handle_message(HANDLE, "yes") == "[fake] close:qualified_to_review"

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.QUALIFIED_TO_REVIEW
    schedule = candidate.state.fields["schedule"]
    assert (schedule.status, schedule.value, schedule.attempts) == ("needs_review", None, 3)
    assert schedule.raw_answer == "no sé"
    events = repo.list_events(candidate.id)
    assert [e.payload for e in events if e.type == "field_needs_review"] == [{"field": "schedule"}]


def test_an_invalid_answer_clears_the_value_and_uses_an_attempt():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López")

    assert service.handle_message(HANDLE, "full_time part_time") == (
        "[fake] ask:availability (attempt 1)"
    )
    availability = service.candidate(HANDLE).state.fields["availability"]
    assert (availability.status, availability.value, availability.attempts) == ("empty", None, 1)


def test_volunteered_answers_are_kept_and_not_asked_again():
    service, _ = make_service(
        script=[
            Extraction(yes_no=True),
            Extraction(
                name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0),
                schedule=Extracted(value="evening", raw_answer="por la tarde", confidence=1.0),
                experience=Extracted(
                    value=Experience(years=3, platforms=["Glovo"]),
                    raw_answer="3 años en Glovo",
                    confidence=1.0,
                ),
            ),
        ]
    )
    service.apply(PHONE)
    service.handle_message(HANDLE, "sí")

    reply = service.handle_message(HANDLE, "Ana López, por la tarde, 3 años en Glovo")

    assert reply == "[fake] ask:availability (attempt 0)"
    assert answer(service, "weekends") == "[fake] ask:start_date (attempt 0)"
    experience = service.candidate(HANDLE).state.fields["experience"]
    assert experience.value == Experience(years=3, platforms=["Glovo"])


def test_an_invalid_volunteered_answer_is_ignored():
    service, _ = make_service(
        script=[
            Extraction(yes_no=True),
            Extraction(
                name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0),
                availability=Extracted(
                    value=["full_time", "part_time"], raw_answer="both", confidence=1.0
                ),
            ),
        ]
    )
    service.apply(PHONE)
    service.handle_message(HANDLE, "sí")

    assert service.handle_message(HANDLE, "Ana López, both") == (
        "[fake] ask:availability (attempt 0)"
    )


def test_a_start_date_beyond_90_days_is_kept_with_a_flag():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "full_time", "evening", "2")

    assert service.handle_message(HANDLE, "2027-01-15").startswith("[fake] recap")

    start_date = service.candidate(HANDLE).state.fields["start_date"]
    assert (start_date.value, start_date.flags) == ("2027-01-15", ["start_date_beyond_90_days"])


def test_a_past_start_date_is_asked_again():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "full_time", "evening", "2")

    assert service.handle_message(HANDLE, "2026-09-25") == "[fake] ask:start_date (attempt 1)"


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
    assert service.handle_message(HANDLE, "Ana López") == "[fake] ask:availability (attempt 0)"


def test_first_name_only_gets_one_surname_follow_up():
    service, _ = make_service()
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")

    assert service.handle_message(HANDLE, "Ana") == "[fake] follow_up:name (surname)"
    assert service.handle_message(HANDLE, "López") == "[fake] ask:availability (attempt 0)"
    assert service.candidate(HANDLE).name == "Ana López"


def test_missing_surname_after_the_follow_up_is_accepted_with_a_flag():
    service, _ = make_service()
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")
    service.handle_message(HANDLE, "Ana")

    assert service.handle_message(HANDLE, "prefiero no, 123") == (
        "[fake] ask:availability (attempt 0)"
    )

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
    assert candidate.state.stage == "availability"
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

    assert service.handle_message(HANDLE, "prefiero no decirlo") == (
        "[fake] ask:availability (attempt 0)"
    )
    assert service.candidate(HANDLE).state.fields["name"].flags == ["surname_missing"]
