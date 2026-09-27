"""Resume: a candidate writing back after a Nudge or after Abandoned continues at the stage
the screening stopped at, and the reply gets the `resuming` cue."""

from datetime import timedelta

from app.domain.models import Status
from tests.application.test_tick import HANDLE, START, Screening, consented


def write_at(screening: Screening, hours: float, text: str) -> str:
    screening.clock.set(START + timedelta(hours=hours))
    return screening.service.handle_message(HANDLE, text)


def reply_cues(screening: Screening) -> list[frozenset[str]]:
    return [call["cues"] for call in screening.llm.calls("reply")]


def test_an_answer_without_a_nudge_has_no_cue():
    screening = consented()

    assert write_at(screening, 0.5, "Ana López") == "[fake] ask:license (attempt 0)"
    assert reply_cues(screening)[-1] == frozenset()


def test_an_answer_after_a_nudge_resumes_with_the_cue_once():
    screening = consented()
    screening.tick_at(1)

    assert write_at(screening, 2, "Ana López") == "[fake] ask:license (attempt 0) +resuming"
    assert write_at(screening, 2.5, "yes") == "[fake] ask:own_vehicle (attempt 0)"
    assert reply_cues(screening)[-2:] == [frozenset({"resuming"}), frozenset()]


def test_an_abandoned_candidate_resumes_at_the_stage_it_stopped_at():
    screening = consented()
    screening.tick_at(72)
    assert screening.candidate().status == Status.ABANDONED

    reply = write_at(screening, 80, "Ana López")

    assert reply == "[fake] ask:license (attempt 0) +resuming"
    candidate = screening.candidate()
    assert candidate.status == Status.IN_PROGRESS
    assert candidate.name == "Ana López"
    [resumed] = screening.events("resumed")
    assert resumed.stage == "name"


def test_an_abandoned_candidate_stays_abandoned_when_the_turn_fails():
    screening = consented()
    screening.tick_at(72)
    screening.llm.fail_next("reply")

    write_at(screening, 80, "Ana López")

    assert screening.candidate().status == Status.ABANDONED
    assert screening.events("resumed") == []


def test_other_outcomes_keep_the_after_close_reply():
    screening = consented()
    screening.answer("Ana López", "no")
    assert screening.candidate().status == Status.REJECTION_PROPOSED

    reply = write_at(screening, 80, "hola?")

    assert reply == screening.config.templates.after_close["es"]
    assert screening.candidate().status == Status.REJECTION_PROPOSED
    assert screening.events("resumed") == []
