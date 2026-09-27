"""The code checks of an eval run: outcome, fields, flags, events and message rules."""

from datetime import UTC, datetime

from app.domain.flow import Ask, Close, Recap
from app.domain.models import Candidate, CandidateState, Event, FieldState, License, Status
from evals.checks import Expectation, Reply, Run, check_run

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
GOOD = Reply(Ask("schedule", attempt=0), "Genial. ¿Qué turno prefieres: mañana, tarde o noche?")


def run(
    status: Status = Status.QUALIFIED,
    state: CandidateState | None = None,
    events: list[Event] | None = None,
    replies: list[Reply] | None = None,
) -> Run:
    candidate = Candidate(
        client_id="grupo_sazon",
        handle="600000001",
        status=status,
        state=state or CandidateState(consent=True),
        created_at=NOW,
        updated_at=NOW,
    )
    return Run(candidate=candidate, events=events or [], messages=[], replies=replies or [GOOD])


def event(event_type: str, **payload) -> Event:
    return Event(type=event_type, stage="license", payload=payload, created_at=NOW)


def test_a_run_meeting_every_expectation_has_no_failure():
    state = CandidateState(
        consent=True,
        language="en",
        fields={"license": FieldState(status="valid", value=License(has_license=False))},
        flags=["question_for_recruiter"],
    )
    result = run(
        Status.REJECTION_PROPOSED,
        state,
        [event("question_forwarded"), event("rejection_proposed", rule="no_license")],
    )
    expect = Expectation(
        status=Status.REJECTION_PROPOSED,
        rule="no_license",
        fields={"license": "valid"},
        values={"license": "no"},
        flags=("question_for_recruiter",),
        events=("question_forwarded",),
        language="en",
    )

    assert check_run(expect, result) == []


def test_each_missed_expectation_is_a_failure():
    expect = Expectation(
        status=Status.REJECTION_PROPOSED,
        rule="no_license",
        fields={"own_vehicle": "needs_review"},
        values={"schedule": "morning"},
        flags=("wants_human",),
        events=("nudge_sent",),
        language="en",
    )

    assert check_run(expect, run()) == [
        "status: expected rejection_proposed, got qualified",
        "rule: expected no_license, got None",
        "own_vehicle: expected needs_review, got empty",
        "schedule: expected morning, got —",
        "flag missing: wants_human",
        "event missing: nudge_sent",
        "language: expected en, got es",
    ]


def test_an_erased_candidate_is_a_failure():
    result = Run(candidate=None, events=[], messages=[], replies=[])

    assert check_run(Expectation(status=Status.QUALIFIED), result) == ["the candidate was erased"]


def test_an_llm_failure_is_a_failure():
    result = run(state=CandidateState(consent=True, flags=["llm_failure"]))

    assert check_run(Expectation(status=Status.QUALIFIED), result) == [
        "an LLM call failed: the candidate got the fallback"
    ]


def test_the_message_rules_are_checked_on_every_reply():
    replies = [
        Reply(Ask("schedule", attempt=0), "Vale. " + "a" * 300 + " ¿Qué turno?"),
        Reply(Ask("schedule", attempt=0), "Genial. Vamos bien. ¿Qué turno prefieres?"),
        Reply(Ask("schedule", attempt=0), "¿Qué turno prefieres? ¿Y qué días?"),
        Reply(Ask("schedule", attempt=0), "¿Qué turno prefieres? 🚀"),
        Reply(Recap(fields=("name",)), "Resumen:\n- Nombre: " + "a" * 400 + "\n¿Todo bien?"),
        Reply(Close(status=Status.QUALIFIED), "¡Listo, Ana! Te llamamos pronto 🚀"),
    ]

    assert check_run(Expectation(status=Status.QUALIFIED), run(replies=replies)) == [
        "reply 1: 318 characters, at most 300",
        "reply 2: 3 sentences, at most 2",
        "reply 3: 2 questions, at most 1",
        "reply 4: an emoji outside a closing message",
    ]
