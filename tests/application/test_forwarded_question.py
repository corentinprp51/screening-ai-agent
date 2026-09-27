"""Forwarded question: a candidate's question is passed on to a recruiter and kept on their
profile, and the screening goes back to the pending question without using an attempt."""

from app.domain.models import Extracted, Extraction, Place
from tests.application.test_tick import ALL_ANSWERS, HANDLE, consented

QUESTION = "¿cuánto se paga?"


def asks(question: str | None = QUESTION, **fields) -> Extraction:
    return Extraction(intent="question", question=question, **fields)


def said[T](value: T) -> Extracted[T]:
    return Extracted(value=value, raw_answer=str(value), confidence=1.0)


def test_a_question_is_forwarded_and_the_pending_question_asked_again():
    screening = consented()
    screening.llm.queue(asks())

    reply = screening.service.handle_message(HANDLE, QUESTION)

    assert reply == "[fake] ask:name (attempt 0) +question_forwarded"
    candidate = screening.candidate()
    assert "question_for_recruiter" in candidate.state.flags
    [forwarded] = screening.events("question_forwarded")
    assert forwarded.payload == {"question": QUESTION}
    assert forwarded.stage == "name"
    assert candidate.state.field("name").attempts == 0


def test_the_question_falls_back_to_the_message_when_its_text_is_missing():
    screening = consented()
    screening.llm.queue(asks(question=None))

    screening.service.handle_message(HANDLE, "y cuánto pagáis")

    [forwarded] = screening.events("question_forwarded")
    assert forwarded.payload == {"question": "y cuánto pagáis"}


def test_an_answer_given_with_the_question_is_kept():
    screening = consented()
    screening.llm.queue(asks(name=said("Ana López")))

    reply = screening.service.handle_message(HANDLE, f"{QUESTION} Soy Ana López")

    assert reply == "[fake] ask:license (attempt 0) +question_forwarded"
    assert screening.candidate().name == "Ana López"


def test_an_answer_to_another_field_is_kept_and_uses_no_attempt():
    screening = consented()
    screening.llm.queue(asks(service_area=said(Place(city="Madrid"))))

    screening.service.handle_message(HANDLE, f"{QUESTION} vivo en Madrid")

    state = screening.candidate().state
    assert state.field("service_area").status == "valid"
    assert state.field("name").attempts == 0


def test_a_question_on_a_re_ask_uses_no_further_attempt():
    screening = consented()
    screening.answer("")  # an invalid name: attempt 1
    screening.llm.queue(asks())

    reply = screening.service.handle_message(HANDLE, QUESTION)

    assert reply == "[fake] ask:name (attempt 1) +question_forwarded"
    assert screening.candidate().state.field("name").attempts == 1


def test_a_question_at_the_recap_uses_no_recap_attempt():
    screening = consented()
    screening.answer(*ALL_ANSWERS[1:])
    screening.llm.queue(asks())

    reply = screening.service.handle_message(HANDLE, QUESTION)

    assert reply.startswith("[fake] recap:") and reply.endswith("+question_forwarded")
    assert screening.candidate().state.recap_attempts == 0


def test_every_question_is_forwarded_and_the_flag_is_kept_once():
    screening = consented()
    screening.llm.queue(asks(), asks(question="¿hay que llevar mochila?"))

    screening.answer(QUESTION, "¿hay que llevar mochila?")

    assert [e.payload["question"] for e in screening.events("question_forwarded")] == [
        QUESTION,
        "¿hay que llevar mochila?",
    ]
    assert screening.candidate().state.flags.count("question_for_recruiter") == 1


def test_a_question_at_a_confirmation_keeps_the_value_to_confirm():
    screening = consented()
    unsure = Extracted(value="Ana López", raw_answer="Ana López", confidence=0.5)
    screening.llm.queue(Extraction(name=unsure), asks())

    screening.answer("Ana López")
    reply = screening.service.handle_message(HANDLE, QUESTION)

    assert reply == "[fake] confirm:name=Ana López +question_forwarded"
    name = screening.candidate().state.field("name")
    assert name.unconfirmed.value == "Ana López" and name.attempts == 0
