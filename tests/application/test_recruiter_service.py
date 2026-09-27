from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.ports import RECENT_MESSAGES
from app.application.recruiter_service import (
    LLMUnavailable,
    NotAbandoned,
    NotRejectionProposed,
    RecruiterService,
)
from app.application.screening_service import ScreeningService, UnknownCandidate
from app.domain.models import Extracted, Extraction, License, OwnVehicle, Status

START = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def services(llm):
    config = load_client_config("grupo_sazon")
    repo = SqliteCandidateRepository(create_sqlite_engine("sqlite://"))
    clock = FixedClock(START)
    screening = ScreeningService(config=config, llm=llm, repo=repo, clock=clock)
    recruiter = RecruiterService(config=config, llm=llm, repo=repo, clock=clock)
    return screening, recruiter, clock


def qualify(screening: ScreeningService, phone: str, name: str) -> None:
    screening.apply(phone)
    for text in [
        "yes",
        name,
        "yes",
        "yes",
        "Madrid",
        "full_time",
        "evening",
        "2",
        "immediate",
        "yes",
    ]:
        screening.handle_message(phone, text)


def test_the_queue_ranks_by_score_then_most_recent_activity(services):
    screening, recruiter, clock = services
    qualify(screening, "600000003", "Ana López")
    clock.set(START + timedelta(minutes=5))
    screening.apply("600000001")
    clock.set(START + timedelta(minutes=10))
    screening.apply("600000002")

    rows = recruiter.queue()

    assert [row.handle for row in rows] == ["600000003", "600000002", "600000001"]
    ana = rows[0]
    assert (ana.name, ana.city, ana.status, ana.stage, ana.score) == (
        "Ana López",
        "Madrid",
        Status.QUALIFIED,
        "closed",
        88,
    )
    assert ana.last_activity == START


def test_the_detail_shows_the_score_breakdown(services):
    screening, recruiter, _ = services
    qualify(screening, "600000002", "Ana López")
    [row] = recruiter.queue()

    detail = recruiter.detail(row.id)

    assert detail.score == 88
    assert [(line.field, line.display, line.points, line.weight) for line in detail.breakdown] == [
        ("availability", "full_time", 30, 30),
        ("schedule", "evening", 20, 20),
        ("start_date", "immediate", 30, 30),
        ("experience", "2 years", 8, 20),
    ]


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
    assert [m.role for m in detail.messages] == ["agent"] + ["candidate", "agent"] * 10
    name, *others = detail.fields
    assert [(field.field, field.display) for field in others] == [
        ("license", "yes"),
        ("own_vehicle", "yes"),
        ("service_area", "Madrid (ES)"),
        ("availability", "full_time"),
        ("schedule", "evening"),
        ("experience", "2 years"),
        ("start_date", "immediate"),
    ]
    assert (name.field, name.value, name.raw_answer, name.confidence, name.verdict) == (
        "name",
        "Ana López",
        "Ana López",
        1.0,
        "valid",
    )
    assert name.needs_review is False
    assert detail.flags == []
    assert [e.type for e in detail.events if e.type != "llm_call"] == [
        "application_received",
        "consent_given",
        *["field_captured"] * 8,
        "outcome",
    ]


def test_the_detail_lists_fields_not_yet_answered(services):
    screening, recruiter, _ = services
    screening.apply("600000001")
    [row] = recruiter.queue()

    name = recruiter.detail(row.id).fields[0]

    assert (name.field, name.value, name.verdict) == ("name", None, "empty")


def test_the_detail_of_an_unknown_candidate_raises(services):
    _, recruiter, _ = services

    with pytest.raises(UnknownCandidate):
        recruiter.detail(999)


def propose_rejection(screening: ScreeningService, phone: str) -> int:
    """A candidate with no license, in Rejection proposed; returns their id."""
    screening.apply(phone)
    for text in ["yes", "Ana López", "no"]:
        screening.handle_message(phone, text)
    return screening.candidate(phone).id


def test_the_to_confirm_tab_shows_the_rule_and_the_answer(services):
    screening, recruiter, _ = services
    screening.apply("600000001")
    propose_rejection(screening, "600000002")

    [row] = recruiter.queue(Status.REJECTION_PROPOSED)

    assert (row.handle, row.rule, row.answer) == ("600000002", "no_license", "no")
    assert (recruiter.detail(row.id).rule, recruiter.detail(row.id).answer) == ("no_license", "no")


def test_confirming_rejects_the_candidate_and_sends_the_rejection_message(services):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")

    recruiter.confirm_rejection(candidate_id)

    detail = recruiter.detail(candidate_id)
    assert detail.status == Status.REJECTED
    assert detail.messages[-1].content == "[fake] close:no_license"
    assert (detail.events[-1].type, detail.events[-1].payload) == (
        "outcome",
        {"status": "rejected", "rule": "no_license"},
    )
    assert (detail.rule, detail.answer) == (None, None)


def test_confirming_an_outside_service_area_rejection_offers_contact(services):
    screening, recruiter, _ = services
    screening.apply("600000001")
    for text in ["yes", "Ana López", "yes", "yes", "Bilbao"]:
        screening.handle_message("600000001", text)
    [row] = recruiter.queue(Status.REJECTION_PROPOSED)
    assert (row.city, row.rule, row.answer) == ("Bilbao", "outside_service_area", "Bilbao")

    recruiter.confirm_rejection(row.id)

    assert recruiter.detail(row.id).messages[-1].content == (
        "[fake] close:outside_service_area (offer contact)"
    )


def test_overriding_resumes_the_screening_with_the_next_question(services):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")

    recruiter.override_rejection(candidate_id)

    detail = recruiter.detail(candidate_id)
    assert (detail.status, detail.stage) == (Status.IN_PROGRESS, "own_vehicle")
    assert detail.messages[-1].content == "[fake] ask:own_vehicle (attempt 0)"
    assert (detail.events[-1].type, detail.events[-1].payload) == (
        "knock_out_overridden",
        {"rule": "no_license"},
    )
    assert screening.handle_message("600000001", "yes") == "[fake] ask:service_area (attempt 0)"


def test_a_different_knock_out_after_an_override_proposes_a_rejection_again(services):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")
    recruiter.override_rejection(candidate_id)

    screening.handle_message("600000001", "no")

    [row] = recruiter.queue(Status.REJECTION_PROPOSED)
    assert (row.id, row.rule, row.answer) == (candidate_id, "no_own_vehicle", "no")


def test_confirm_and_override_are_refused_outside_rejection_proposed(services):
    screening, recruiter, _ = services
    qualify(screening, "600000001", "Ana López")
    candidate_id = screening.candidate("600000001").id

    with pytest.raises(NotRejectionProposed):
        recruiter.confirm_rejection(candidate_id)
    with pytest.raises(NotRejectionProposed):
        recruiter.override_rejection(candidate_id)


def test_an_llm_failure_on_override_changes_nothing_but_a_flag(services, llm):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")
    llm.fail_next("reply")

    with pytest.raises(LLMUnavailable):
        recruiter.override_rejection(candidate_id)

    candidate = screening.candidate("600000001")
    assert candidate.status == Status.REJECTION_PROPOSED
    assert candidate.state.overridden_knock_outs == []
    assert candidate.state.flags == ["llm_failure"]


def test_the_queue_and_the_detail_show_the_summary_and_the_next_action(services):
    screening, recruiter, _ = services
    qualify(screening, "600000001", "Ana López")

    [row] = recruiter.queue()
    detail = recruiter.detail(row.id)

    for view in (row, detail):
        assert (view.summary, view.next_action) == (
            "[fake] summary: qualified; next: Call within 48 h",
            "Call within 48 h",
        )


def test_confirming_a_rejection_writes_no_new_summary(services):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")
    proposed = recruiter.detail(candidate_id).summary

    recruiter.confirm_rejection(candidate_id)

    detail = recruiter.detail(candidate_id)
    assert (detail.summary, detail.next_action) == (proposed, None)


def test_a_screening_resumed_by_an_override_is_summarized_again_when_it_stops(services):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")

    recruiter.override_rejection(candidate_id)
    assert recruiter.detail(candidate_id).summary is None

    for text in ["yes", "Madrid", "full_time", "evening", "2", "immediate", "yes"]:
        screening.handle_message("600000001", text)
    assert recruiter.detail(candidate_id).summary == (
        "[fake] summary: qualified; next: Call within 48 h"
    )


def test_an_override_onto_another_failed_knock_out_writes_a_new_summary(services, llm):
    screening, recruiter, _ = services
    screening.apply("600000001")
    llm.queue(
        Extraction(yes_no=True),
        Extraction(
            name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0),
            license=Extracted(value=License(has_license=False), raw_answer="no", confidence=1.0),
            own_vehicle=Extracted(
                value=OwnVehicle(owns_vehicle=False), raw_answer="no", confidence=1.0
            ),
        ),
    )
    for text in ["sí", "Ana López, sin carnet ni coche"]:
        screening.handle_message("600000001", text)
    candidate_id = screening.candidate("600000001").id

    recruiter.override_rejection(candidate_id)

    candidate = screening.candidate("600000001")
    assert candidate.status == Status.REJECTION_PROPOSED
    assert candidate.summary.facts["rule"] == "no_own_vehicle"


def recent_transcript(recruiter: RecruiterService, candidate_id: int) -> list[tuple[str, str]]:
    messages = recruiter.detail(candidate_id).messages
    assert len(messages) > RECENT_MESSAGES  # the window really cuts the transcript
    return [(m.role, m.content) for m in messages[-RECENT_MESSAGES:]]


def test_confirming_gives_the_recent_transcript_to_the_reply(services, llm):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")
    expected = recent_transcript(recruiter, candidate_id)

    recruiter.confirm_rejection(candidate_id)

    transcript = llm.calls("reply")[-1]["transcript"]
    assert [(m.role, m.content) for m in transcript] == expected


def test_overriding_gives_the_recent_transcript_to_the_reply(services, llm):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")
    expected = recent_transcript(recruiter, candidate_id)

    recruiter.override_rejection(candidate_id)

    transcript = llm.calls("reply")[-1]["transcript"]
    assert [(m.role, m.content) for m in transcript] == expected


def llm_calls(detail) -> list[str]:
    return [e.payload["call"] for e in detail.events if e.type == "llm_call"]


def test_confirming_records_the_reply_call(services):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")
    before = llm_calls(recruiter.detail(candidate_id))

    recruiter.confirm_rejection(candidate_id)

    detail = recruiter.detail(candidate_id)
    assert llm_calls(detail)[len(before) :] == ["reply"]
    assert [e.payload for e in detail.events if e.type == "llm_call"][-1] == {
        "call": "reply",
        "input_tokens": 0,
        "output_tokens": 0,
    }


def test_an_override_onto_another_failed_knock_out_records_the_reply_and_the_summary(services, llm):
    screening, recruiter, _ = services
    screening.apply("600000001")
    llm.queue(
        Extraction(yes_no=True),
        Extraction(
            name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0),
            license=Extracted(value=License(has_license=False), raw_answer="no", confidence=1.0),
            own_vehicle=Extracted(
                value=OwnVehicle(owns_vehicle=False), raw_answer="no", confidence=1.0
            ),
        ),
    )
    for text in ["sí", "Ana López, sin carnet ni coche"]:
        screening.handle_message("600000001", text)
    candidate_id = screening.candidate("600000001").id
    before = llm_calls(recruiter.detail(candidate_id))

    recruiter.override_rejection(candidate_id)

    assert llm_calls(recruiter.detail(candidate_id))[len(before) :] == ["reply", "summarize"]


def abandon(screening: ScreeningService, clock, phone: str) -> int:
    """A candidate silent at the license question until the deadline."""
    screening.apply(phone)
    for text in ["yes", "Ana López"]:
        screening.handle_message(phone, text)
    clock.set(START + timedelta(hours=72))
    screening.tick()
    return screening.candidate(phone).id


def test_reopening_restarts_an_abandoned_screening_at_its_stage(services):
    screening, recruiter, clock = services
    candidate_id = abandon(screening, clock, "600000001")
    clock.set(START + timedelta(hours=80))

    recruiter.reopen(candidate_id)

    detail = recruiter.detail(candidate_id)
    assert (detail.status, detail.stage) == (Status.IN_PROGRESS, "license")
    last = detail.messages[-1]
    assert (last.role, last.language, last.created_at) == ("agent", "es", clock.now())
    assert last.content == (
        "¡Hola de nuevo! Hemos reabierto tu candidatura: "
        "respóndeme cuando quieras y seguimos donde lo dejamos."
    )
    [reopened] = [e for e in detail.events if e.type == "reopened"]
    assert (reopened.stage, reopened.created_at) == ("license", clock.now())


def test_the_reopen_message_is_in_the_candidate_language(services, llm):
    screening, recruiter, clock = services
    llm.queue(
        Extraction(yes_no=True, language="en"),
        Extraction(
            name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0),
            language="en",
        ),
    )
    candidate_id = abandon(screening, clock, "600000001")

    recruiter.reopen(candidate_id)

    assert recruiter.detail(candidate_id).messages[-1].content == (
        "Hi again! We have reopened your application: "
        "reply whenever you like and we will pick up where we left off."
    )


def test_reopening_makes_no_llm_call(services, llm):
    screening, recruiter, clock = services
    candidate_id = abandon(screening, clock, "600000001")
    before = len(llm.calls("reply"))

    recruiter.reopen(candidate_id)

    assert len(llm.calls("reply")) == before


def test_a_candidate_that_is_not_abandoned_cannot_be_reopened(services):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")

    with pytest.raises(NotAbandoned):
        recruiter.reopen(candidate_id)
    assert recruiter.detail(candidate_id).status == Status.REJECTION_PROPOSED


def test_reopening_an_unknown_candidate_fails(services):
    _, recruiter, _ = services

    with pytest.raises(UnknownCandidate):
        recruiter.reopen(999)


STAGES = [
    "consent",
    "name",
    "license",
    "own_vehicle",
    "service_area",
    "availability",
    "schedule",
    "experience",
    "start_date",
    "recap",
    "closed",
]


def dots(recruiter: RecruiterService) -> list[tuple[str, str]]:
    [row] = recruiter.queue()
    return [(dot.stage, dot.state) for dot in row.dots]


def test_the_stage_dots_follow_the_client_fields(services):
    screening, recruiter, _ = services
    screening.apply("600000001")
    for text in ["yes", "Ana López"]:
        screening.handle_message("600000001", text)

    assert dots(recruiter) == [
        ("consent", "done"),
        ("name", "done"),
        ("license", "current"),
        *[(stage, "todo") for stage in STAGES[3:]],
    ]


def test_a_qualified_candidate_has_every_dot_done(services):
    screening, recruiter, _ = services
    qualify(screening, "600000001", "Ana López")

    assert dots(recruiter) == [(stage, "done") for stage in STAGES]


def test_a_knock_out_marks_the_failing_field(services):
    screening, recruiter, _ = services
    candidate_id = propose_rejection(screening, "600000001")
    expected = [
        ("consent", "done"),
        ("name", "done"),
        ("license", "failed"),
        *[(stage, "todo") for stage in STAGES[3:]],
    ]
    assert dots(recruiter) == expected

    recruiter.confirm_rejection(candidate_id)

    assert dots(recruiter) == expected


def test_an_abandoned_screening_greys_out_the_remaining_dots(services):
    screening, recruiter, clock = services
    abandon(screening, clock, "600000001")

    assert dots(recruiter) == [
        ("consent", "done"),
        ("name", "done"),
        *[(stage, "stopped") for stage in STAGES[2:]],
    ]


def test_a_withdrawn_screening_greys_out_the_dots_from_where_it_stopped(services, llm):
    screening, recruiter, _ = services
    screening.apply("600000001")
    for text in ["yes", "Ana López", "yes"]:
        screening.handle_message("600000001", text)
    llm.queue(Extraction(intent="opt_out"))
    screening.handle_message("600000001", "stop")

    assert dots(recruiter) == [
        ("consent", "done"),
        ("name", "done"),
        ("license", "done"),
        *[(stage, "stopped") for stage in STAGES[3:]],
    ]


def test_an_abuse_rejection_marks_the_close_not_a_field(services, llm):
    screening, recruiter, _ = services
    screening.apply("600000001")
    screening.handle_message("600000001", "yes")
    llm.queue(Extraction(intent="abuse"), Extraction(intent="abuse"))
    screening.handle_message("600000001", "eres un bot inútil")
    screening.handle_message("600000001", "eres un bot inútil")

    assert dots(recruiter) == [
        ("consent", "done"),
        *[(stage, "todo") for stage in STAGES[1:-1]],
        ("closed", "failed"),
    ]


def test_a_knock_out_volunteered_early_leaves_the_skipped_fields_empty(services, llm):
    screening, recruiter, _ = services
    screening.apply("600000001")
    screening.handle_message("600000001", "yes")
    llm.queue(
        Extraction(
            name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0),
            own_vehicle=Extracted(
                value=OwnVehicle(owns_vehicle=False), raw_answer="no car", confidence=1.0
            ),
        )
    )
    screening.handle_message("600000001", "Ana López, no car")

    assert dots(recruiter) == [
        ("consent", "done"),
        ("name", "done"),
        ("license", "todo"),
        ("own_vehicle", "failed"),
        *[(stage, "todo") for stage in STAGES[4:]],
    ]


def test_qualified_to_review_leaves_an_unconfirmed_recap_empty(services):
    screening, recruiter, clock = services
    screening.apply("600000001")
    for text in ["yes", "Ana López", "yes", "yes", "Madrid", "full_time", "evening", "2"]:
        screening.handle_message("600000001", text)
    screening.handle_message("600000001", "immediate")
    clock.set(START + timedelta(hours=72))
    screening.tick()

    assert recruiter.queue()[0].status == Status.QUALIFIED_TO_REVIEW
    assert dots(recruiter) == [
        *[(stage, "done") for stage in STAGES[:-2]],
        ("recap", "todo"),
        ("closed", "done"),
    ]


def test_the_score_is_partial_while_in_progress(services):
    screening, recruiter, _ = services
    qualify(screening, "600000001", "Ana López")
    screening.apply("600000002")

    assert {row.handle: row.partial_score for row in recruiter.queue()} == {
        "600000001": False,
        "600000002": True,
    }
