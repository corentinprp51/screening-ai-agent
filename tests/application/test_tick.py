"""The sweep: Nudges to silent candidates and the outcome at the deadline, with the time
moved forward on a FixedClock."""

from datetime import UTC, datetime, timedelta

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.recruiter_service import RecruiterService
from app.application.screening_service import ScreeningService
from app.domain.models import Status

PHONE = "+34 600 111 222"
HANDLE = "34600111222"
START = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
ALL_ANSWERS = ["yes", "Ana López", "yes", "yes", "Madrid", "full_time", "evening", "2", "immediate"]


class Screening:
    def __init__(self) -> None:
        self.clock = FixedClock(START)
        self.repo = SqliteCandidateRepository(create_sqlite_engine("sqlite://"))
        self.service = ScreeningService(
            config=load_client_config("grupo_sazon"),
            llm=FakeLLM(),
            repo=self.repo,
            clock=self.clock,
        )

    def tick_at(self, hours: float) -> None:
        """Run the sweep this many hours after the start."""
        self.clock.set(START + timedelta(hours=hours))
        self.service.tick()

    def answer(self, *texts: str) -> None:
        for text in texts:
            self.service.handle_message(HANDLE, text)

    def candidate(self):
        return self.service.candidate(HANDLE)

    def events(self, event_type: str):
        return [e for e in self.repo.list_events(self.candidate().id) if e.type == event_type]

    def agent_messages(self) -> list[str]:
        return [m.content for m in self.service.transcript(HANDLE) if m.role == "agent"]


def consented(name: str | None = "Ana López") -> Screening:
    screening = Screening()
    screening.service.apply(PHONE, name)
    screening.answer("yes")  # asks the name at START
    return screening


def test_a_silent_candidate_gets_three_nudges_at_1_20_and_48_hours():
    screening = consented()

    for hours in [0.5, 1, 2, 20, 21, 48, 60]:
        screening.tick_at(hours)

    assert [e.payload["number"] for e in screening.events("nudge_sent")] == [1, 2, 3]
    assert [e.created_at for e in screening.events("nudge_sent")] == [
        START + timedelta(hours=hours) for hours in [1, 20, 48]
    ]
    assert screening.agent_messages()[-3:] == [
        "¿Seguimos, Ana? Ya casi está, preguntas pendientes: 9.",
        "¿Retomamos, Ana? Tu candidatura sigue abierta, preguntas pendientes: 9.",
        "Último recordatorio, Ana: ¿terminamos tu candidatura? Preguntas pendientes: 9.",
    ]
    assert screening.candidate().status == Status.IN_PROGRESS


def test_a_nudge_is_in_the_candidate_language_and_counts_the_questions_left():
    screening = Screening()
    screening.service.apply(PHONE)
    screening.answer("yes", "Ana López", "yes")
    candidate = screening.candidate()
    candidate.state.language = "en"
    screening.repo.save(candidate)

    screening.tick_at(1)

    assert screening.agent_messages()[-1] == (
        "Shall we carry on, Ana? Almost done, questions left: 7."
    )


def test_the_delays_count_from_the_last_unanswered_question():
    screening = consented()
    screening.tick_at(1)
    screening.clock.set(START + timedelta(hours=10))
    screening.answer("Ana López")  # a new question at 10 h

    screening.tick_at(10.5)
    assert len(screening.events("nudge_sent")) == 1
    screening.tick_at(11)
    assert [e.payload["number"] for e in screening.events("nudge_sent")] == [1, 1]


def test_no_nudge_before_consent_in_rejection_proposed_or_in_an_outcome():
    no_consent = Screening()
    no_consent.service.apply(PHONE)
    no_consent.tick_at(1)
    assert no_consent.events("nudge_sent") == []

    rejected = consented()
    rejected.answer("Ana López", "no")
    assert rejected.candidate().status == Status.REJECTION_PROPOSED
    rejected.tick_at(1)
    rejected.tick_at(72)
    assert rejected.events("nudge_sent") == []
    assert rejected.candidate().status == Status.REJECTION_PROPOSED

    qualified = consented()
    qualified.answer(*ALL_ANSWERS[1:], "yes")
    assert qualified.candidate().status == Status.QUALIFIED
    qualified.tick_at(1)
    assert qualified.events("nudge_sent") == []


def test_at_the_deadline_a_screening_with_questions_left_is_abandoned_at_its_stage():
    screening = consented()
    screening.answer("Ana López")  # now at the license question

    screening.tick_at(71)
    assert screening.candidate().status == Status.IN_PROGRESS
    screening.tick_at(72)

    candidate = screening.candidate()
    assert candidate.status == Status.ABANDONED
    assert candidate.state.stage == "license"
    assert candidate.summary is None
    [abandoned] = screening.events("abandoned")
    assert abandoned.payload == {"stage": "license"}


def test_at_the_deadline_an_unconfirmed_recap_is_qualified_to_review_with_a_summary():
    screening = consented()
    screening.answer(*ALL_ANSWERS[1:])  # the recap is sent

    screening.tick_at(72)

    candidate = screening.candidate()
    assert candidate.status == Status.QUALIFIED_TO_REVIEW
    assert candidate.summary.text == (
        "[fake] summary: qualified_to_review; next: Check the answers: the recap was not confirmed"
    )
    assert screening.events("abandoned") == []
    [outcome] = screening.events("outcome")
    assert outcome.payload == {"status": "qualified_to_review"}


def test_at_the_deadline_an_unanswered_greeting_is_erased_and_counted_anonymously():
    screening = Screening()
    candidate_id = screening.service.apply(PHONE, "Ana").id

    screening.tick_at(71)
    assert screening.candidate() is not None
    screening.tick_at(72)

    assert screening.candidate() is None
    assert screening.repo.list_messages(candidate_id) == []
    assert screening.repo.list_events(candidate_id) == []
    assert screening.repo.count_consent_drop_offs("grupo_sazon") == 1


def test_a_reply_to_the_greeting_that_is_not_a_consent_is_still_erased_at_the_deadline():
    screening = Screening()
    screening.service.apply(PHONE)
    screening.answer("maybe")  # neither yes nor no: the consent is asked again at START

    screening.tick_at(1)
    assert screening.events("nudge_sent") == []
    screening.tick_at(72)

    assert screening.candidate() is None
    assert screening.repo.count_consent_drop_offs("grupo_sazon") == 1


def test_nothing_is_sent_once_the_screening_has_ended():
    screening = consented()
    screening.tick_at(72)
    messages = screening.agent_messages()

    screening.tick_at(96)
    screening.tick_at(200)

    assert screening.candidate().status == Status.ABANDONED
    assert screening.agent_messages() == messages
    assert len(screening.events("abandoned")) == 1


def test_after_a_recruiter_override_the_delays_count_from_the_new_question():
    screening = consented()
    screening.answer("Ana López", "no")  # rejection proposed at START
    screening.clock.set(START + timedelta(hours=30))
    recruiter = RecruiterService(
        config=load_client_config("grupo_sazon"),
        llm=FakeLLM(),
        repo=screening.repo,
        clock=screening.clock,
    )
    recruiter.override_rejection(screening.candidate().id)  # asks the next field at 30 h

    screening.tick_at(30.5)
    assert screening.events("nudge_sent") == []
    screening.tick_at(31)
    assert [e.payload["number"] for e in screening.events("nudge_sent")] == [1]


def test_the_priority_score_of_an_open_candidate_is_recomputed_on_each_tick():
    screening = consented()
    screening.answer(*ALL_ANSWERS[1:-1], "2026-11-20")  # a start in 55 days
    score = screening.candidate().score.points["start_date"]

    screening.tick_at(71)  # 3 days later, before the deadline

    assert screening.candidate().score.points["start_date"] > score
