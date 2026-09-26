from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.recruiter_service import RecruiterService
from app.application.screening_service import ScreeningService, UnknownCandidate
from app.domain.models import Status

START = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


@pytest.fixture
def services():
    config = load_client_config("grupo_sazon")
    repo = SqliteCandidateRepository(create_sqlite_engine("sqlite://"))
    clock = FixedClock(START)
    screening = ScreeningService(config=config, llm=FakeLLM(), repo=repo, clock=clock)
    return screening, RecruiterService(config=config, repo=repo), clock


def qualify(screening: ScreeningService, phone: str, name: str) -> None:
    screening.apply(phone)
    for text in ["yes", name, "yes"]:
        screening.handle_message(phone, text)


def test_the_queue_lists_every_candidate_most_recent_activity_first(services):
    screening, recruiter, clock = services
    screening.apply("600000001")
    clock.set(START + timedelta(minutes=5))
    qualify(screening, "600000002", "Ana López")

    rows = recruiter.queue()

    assert [row.handle for row in rows] == ["600000002", "600000001"]
    ana = rows[0]
    assert (ana.name, ana.status, ana.stage) == ("Ana López", Status.QUALIFIED, "closed")
    assert ana.last_activity == START + timedelta(minutes=5)


def test_the_queue_filters_by_status(services):
    screening, recruiter, _ = services
    screening.apply("600000001")
    qualify(screening, "600000002", "Ana López")

    assert [row.handle for row in recruiter.queue(Status.IN_PROGRESS)] == ["600000001"]
    assert [row.handle for row in recruiter.queue(Status.QUALIFIED)] == ["600000002"]
    assert recruiter.queue(Status.REJECTION_PROPOSED) == []


def test_queue_rows_show_the_candidate_flags(services):
    screening, recruiter, _ = services
    screening.apply("600000001")
    for text in ["yes", "Ana", "123"]:
        screening.handle_message("600000001", text)

    [row] = recruiter.queue()

    assert row.flags == ["surname_missing"]


def test_the_detail_shows_transcript_fields_flags_and_events(services):
    screening, recruiter, _ = services
    qualify(screening, "600000002", "Ana López")
    [row] = recruiter.queue()

    detail = recruiter.detail(row.id)

    assert detail.handle == "600000002"
    assert detail.status == Status.QUALIFIED
    assert [m.role for m in detail.messages] == ["agent"] + ["candidate", "agent"] * 3
    [name] = detail.fields
    assert (name.field, name.value, name.raw_answer, name.confidence, name.verdict) == (
        "name",
        "Ana López",
        "Ana López",
        1.0,
        "valid",
    )
    assert name.needs_review is False
    assert detail.flags == []
    assert [e.type for e in detail.events] == [
        "application_received",
        "consent_given",
        "field_captured",
        "outcome",
    ]


def test_the_detail_lists_fields_not_yet_answered(services):
    screening, recruiter, _ = services
    screening.apply("600000001")
    [row] = recruiter.queue()

    [name] = recruiter.detail(row.id).fields

    assert (name.field, name.value, name.verdict) == ("name", None, "empty")


def test_the_detail_of_an_unknown_candidate_raises(services):
    _, recruiter, _ = services

    with pytest.raises(UnknownCandidate):
        recruiter.detail(999)
