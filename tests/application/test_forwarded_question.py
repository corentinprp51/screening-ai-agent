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


def test_a_no_to_the_recap_sent_with_a_question_still_asks_what_to_change():
    screening = consented()
    screening.answer(*ALL_ANSWERS[1:])
    screening.llm.queue(asks(yes_no=False))

    reply = screening.service.handle_message(HANDLE, f"no, está mal. {QUESTION}")

    assert reply == "[fake] ask_correction (attempt 1) +question_forwarded"


def test_a_question_at_a_follow_up_keeps_the_follow_up_pending():
    screening = consented()
    screening.answer("Ana López", "expired")
    screening.llm.queue(asks())

    reply = screening.service.handle_message(HANDLE, QUESTION)

    assert reply == "[fake] follow_up:license (validity) +question_forwarded"
    assert screening.candidate().state.field("license").status == "incomplete"


def test_a_question_before_consent_asks_for_the_consent_again():
    screening = consented()
    screening.service.apply("+34 600 999 888")
    screening.llm.queue(asks())

    reply = screening.service.handle_message("34600999888", QUESTION)

    assert reply == "[fake] greet +question_forwarded"
    assert screening.repo.get_by_handle("grupo_sazon", "34600999888").state.consent is None


def test_a_frustrated_question_gets_the_call_offer_and_is_still_forwarded():
    screening = consented()
    screening.llm.queue(asks(sentiment="frustrated"))

    reply = screening.service.handle_message(HANDLE, f"qué pesado, {QUESTION}")

    assert reply == "[fake] ask:name (attempt 0) +frustrated"
    assert len(screening.events("question_forwarded")) == 1
