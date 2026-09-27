"""Adaptive replies: the LLM reads the sentiment, code picks the cue and the flags. A
frustrated candidate is offered a call; accepting it, or asking for one unprompted, asks the
recruiter for a call while the screening goes on."""

from app.domain.models import Extracted, Extraction, Status
from app.domain.summary import recruiter_action
from tests.application.test_resume import reply_cues, write_at
from tests.application.test_tick import consented

NAME = Extracted[str](value="Ana López", raw_answer="Ana López", confidence=1.0)


def test_a_confused_candidate_gets_the_confused_cue_and_the_same_question():
    screening = consented()
    screening.llm.queue(Extraction(sentiment="confused"))

    reply = screening.service.handle_message("34600111222", "¿qué nombre?")

    assert reply == "[fake] ask:name (attempt 1) +confused"
    assert "frustrated" not in screening.candidate().state.flags


def test_a_frustrated_candidate_is_flagged_and_gets_the_frustrated_cue():
    screening = consented()
    screening.llm.queue(Extraction(sentiment="frustrated", name=NAME))

    reply = screening.service.handle_message("34600111222", "Ana López, qué pesado")

    assert reply == "[fake] ask:license (attempt 0) +frustrated"
    assert "frustrated" in screening.candidate().state.flags


def test_a_neutral_message_has_no_cue():
    screening = consented()

    screening.answer("Ana López")

    assert reply_cues(screening)[-1] == frozenset()


def test_accepting_the_call_asks_the_recruiter_for_a_call_and_the_screening_goes_on():
    screening = consented()
    screening.llm.queue(
        Extraction(sentiment="frustrated", name=NAME),
        Extraction(call_requested=True),
    )
    screening.answer("Ana López, qué pesado", "sí, prefiero que me llaméis")

    candidate = screening.candidate()
    assert "wants_human" in candidate.state.flags
    assert candidate.status == Status.IN_PROGRESS
    # Accepting the call is not an answer to the question: it uses no attempt.
    assert screening.agent_messages()[-1] == "[fake] ask:license (attempt 0) +call_requested"
    action = recruiter_action(candidate.status, candidate.state, screening.config)
    assert action == "Call: the candidate asked for it"
    [event] = screening.events("call_requested")
    assert event.stage == "license"


def test_a_call_request_without_an_offer_asks_the_recruiter_for_a_call():
    screening = consented()
    screening.llm.queue(Extraction(call_requested=True))

    screening.answer("que me llame una persona, por favor")

    candidate = screening.candidate()
    assert "wants_human" in candidate.state.flags
    assert candidate.status == Status.IN_PROGRESS
    # Asking for a call is not an answer to the question: it uses no attempt.
    assert screening.agent_messages()[-1] == "[fake] ask:name (attempt 0) +call_requested"
    action = recruiter_action(candidate.status, candidate.state, screening.config)
    assert action == "Call: the candidate asked for it"
    [event] = screening.events("call_requested")
    assert event.stage == "name"


def test_a_call_request_given_with_an_answer_keeps_the_answer():
    screening = consented()
    screening.llm.queue(Extraction(call_requested=True, name=NAME))

    screening.answer("Ana López, llamadme")

    candidate = screening.candidate()
    assert "wants_human" in candidate.state.flags
    assert candidate.state.field("name").value == "Ana López"


def test_a_second_call_request_is_not_recorded_or_acknowledged_again():
    screening = consented()
    screening.llm.queue(
        Extraction(call_requested=True),
        Extraction(call_requested=True, name=NAME),
    )

    screening.answer("llamadme", "Ana López, que me llaméis")

    assert len(screening.events("call_requested")) == 1
    assert reply_cues(screening)[-1] == frozenset()


def test_once_a_call_is_requested_a_frustrated_candidate_is_not_offered_it_again():
    screening = consented()
    screening.llm.queue(
        Extraction(sentiment="frustrated", name=NAME),
        Extraction(call_requested=True),
        Extraction(sentiment="frustrated"),
    )

    screening.answer("Ana López, qué pesado", "sí, llamadme", "otra vez?")

    assert reply_cues(screening)[-1] == frozenset()


def test_frustration_replaces_the_resuming_cue():
    screening = consented()
    screening.tick_at(1)
    screening.llm.queue(Extraction(sentiment="frustrated", name=NAME))

    reply = write_at(screening, 2, "Ana López, ya os lo dije")

    assert reply == "[fake] ask:license (attempt 0) +frustrated"


def test_a_closing_message_gets_no_sentiment_cue():
    screening = consented()
    screening.llm.queue(Extraction(sentiment="frustrated", intent="opt_out"))

    screening.answer("dejadme en paz")

    assert reply_cues(screening)[-1] == frozenset()
    assert screening.candidate().status == Status.WITHDRAWN
