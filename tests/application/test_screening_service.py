from datetime import UTC, datetime

import pytest

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.screening_service import ScreeningService
from app.domain.models import Experience, Extracted, Extraction, License, OwnVehicle, Status

PHONE = "+34 600 111 222"
HANDLE = "34600111222"
RECAP = (
    "[fake] recap: name=Ana López; license=yes; own_vehicle=yes; availability=full_time; "
    "schedule=evening; experience=2 years; start_date=immediate"
)


def make_service(script: list[Extraction] | None = None, llm: FakeLLM | None = None):
    repo = SqliteCandidateRepository(create_sqlite_engine("sqlite://"))
    service = ScreeningService(
        config=load_client_config("grupo_sazon"),
        llm=llm or FakeLLM(script),
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
    assert service.handle_message(HANDLE, "Ana López") == "[fake] ask:license (attempt 0)"
    assert service.handle_message(HANDLE, "yes") == "[fake] ask:own_vehicle (attempt 0)"
    assert service.handle_message(HANDLE, "yes") == "[fake] ask:availability (attempt 0)"
    assert service.handle_message(HANDLE, "full_time") == "[fake] ask:schedule (attempt 0)"
    assert service.handle_message(HANDLE, "evening") == "[fake] ask:experience (attempt 0)"
    assert service.handle_message(HANDLE, "2") == "[fake] ask:start_date (attempt 0)"
    assert service.handle_message(HANDLE, "immediate") == RECAP
    assert service.handle_message(HANDLE, "yes") == "[fake] close:qualified"

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.QUALIFIED
    assert candidate.name == "Ana López"
    assert candidate.state.stage == "closed"
    assert len(service.transcript(HANDLE)) == 19
    assert [e.type for e in repo.list_events(candidate.id)] == [
        "application_received",
        "consent_given",
        *["field_captured"] * 7,
        "outcome",
    ]


def test_three_invalid_answers_mark_the_field_needs_review_and_end_qualified_to_review():
    service, repo = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes", "full_time")

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
    answer(service, "yes", "Ana López", "yes", "yes")

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
                license=Extracted(
                    value=License(has_license=True, type="car"),
                    raw_answer="carnet de coche",
                    confidence=1.0,
                ),
                own_vehicle=Extracted(
                    value=OwnVehicle(owns_vehicle=True), raw_answer="coche propio", confidence=1.0
                ),
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

    reply = service.handle_message(
        HANDLE, "Ana López, carnet de coche y coche propio, por la tarde, 3 años en Glovo"
    )

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
                license=Extracted(value=License(has_license=True), raw_answer="sí", confidence=1.0),
                own_vehicle=Extracted(
                    value=OwnVehicle(owns_vehicle=True), raw_answer="sí", confidence=1.0
                ),
                availability=Extracted(
                    value=["full_time", "part_time"], raw_answer="both", confidence=1.0
                ),
            ),
        ]
    )
    service.apply(PHONE)
    service.handle_message(HANDLE, "sí")

    assert service.handle_message(HANDLE, "Ana López, sí, sí, both") == (
        "[fake] ask:availability (attempt 0)"
    )


def test_a_start_date_beyond_90_days_is_kept_with_a_flag_that_does_not_change_the_outcome():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes", "full_time", "evening", "2")

    assert service.handle_message(HANDLE, "2027-01-15").startswith("[fake] recap")
    assert service.handle_message(HANDLE, "yes") == "[fake] close:qualified"

    candidate = service.candidate(HANDLE)
    start_date = candidate.state.fields["start_date"]
    assert (start_date.value, start_date.flags) == ("2027-01-15", ["start_date_beyond_90_days"])
    assert candidate.status == Status.QUALIFIED


def test_a_past_start_date_is_asked_again():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes", "full_time", "evening", "2")

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
    assert service.handle_message(HANDLE, "Ana López") == "[fake] ask:license (attempt 0)"


def test_first_name_only_gets_one_surname_follow_up():
    service, _ = make_service()
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")

    assert service.handle_message(HANDLE, "Ana") == "[fake] follow_up:name (surname)"
    assert service.handle_message(HANDLE, "López") == "[fake] ask:license (attempt 0)"
    assert service.candidate(HANDLE).name == "Ana López"


def test_missing_surname_after_the_follow_up_is_accepted_with_a_flag():
    service, _ = make_service()
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")
    service.handle_message(HANDLE, "Ana")

    assert service.handle_message(HANDLE, "prefiero no, 123") == "[fake] ask:license (attempt 0)"

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
    assert candidate.state.stage == "license"
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
        "[fake] ask:license (attempt 0)"
    )
    assert service.candidate(HANDLE).state.fields["name"].flags == ["surname_missing"]


def test_opting_out_after_consent_closes_as_withdrawn():
    service, repo = make_service(
        script=[
            Extraction(yes_no=True),
            Extraction(name=Extracted(value="Ana López", raw_answer="Ana", confidence=1.0)),
            Extraction(intent="opt_out"),
        ]
    )
    service.apply(PHONE)
    answer(service, "sí", "Ana López")

    assert service.handle_message(HANDLE, "ya no me interesa") == "[fake] close:withdrawn"

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.WITHDRAWN
    assert candidate.state.stage == "closed"
    events = repo.list_events(candidate.id)
    assert [(e.type, e.stage) for e in events[-2:]] == [
        ("opted_out", "license"),
        ("outcome", "closed"),
    ]


def test_a_message_after_an_outcome_gets_the_fixed_reply_and_a_flag():
    service, repo = make_service(script=[Extraction(yes_no=True), Extraction(intent="opt_out")])
    service.apply(PHONE)
    answer(service, "sí", "stop")

    reply = service.handle_message(HANDLE, "¿al final me llamáis?")

    assert reply == "Gracias por tu mensaje, un reclutador lo revisará."
    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.WITHDRAWN
    assert candidate.state.flags == ["message_after_close"]
    assert [m.content for m in service.transcript(HANDLE)[-2:]] == ["¿al final me llamáis?", reply]
    assert repo.list_events(candidate.id)[-1].type == "message_after_close"


def test_a_message_after_an_outcome_is_answered_in_the_candidate_language():
    service, _ = make_service(
        script=[
            Extraction(language="en", yes_no=True),
            Extraction(language="en", intent="opt_out"),
        ]
    )
    service.apply(PHONE)
    answer(service, "sure", "not interested anymore")

    assert service.handle_message(HANDLE, "hello?") == (
        "Thanks for your message, a recruiter will review it."
    )


def test_an_extract_failure_sends_the_fallback_and_asks_the_same_question_next():
    llm = FakeLLM()
    service, repo = make_service(llm=llm)
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")
    before = service.candidate(HANDLE).state

    llm.fail_next("extract")
    reply = service.handle_message(HANDLE, "Ana López")

    assert reply == "Perdona, he tenido un problema técnico. ¿Me lo puedes repetir?"
    candidate = service.candidate(HANDLE)
    assert candidate.state.flags == ["llm_failure"]
    assert candidate.state.model_copy(update={"flags": []}) == before
    assert [m.content for m in service.transcript(HANDLE)[-2:]] == ["Ana López", reply]
    assert repo.list_events(candidate.id)[-1].type == "llm_failure"
    assert service.handle_message(HANDLE, "Ana López") == "[fake] ask:license (attempt 0)"


def test_a_reply_failure_records_nothing_from_the_turn():
    llm = FakeLLM()
    service, repo = make_service(llm=llm)
    service.apply(PHONE)
    service.handle_message(HANDLE, "yes")

    llm.fail_next("reply")
    service.handle_message(HANDLE, "Ana López")

    candidate = service.candidate(HANDLE)
    assert "name" not in candidate.state.fields
    assert [e.type for e in repo.list_events(candidate.id)] == [
        "application_received",
        "consent_given",
        "llm_failure",
    ]


def test_no_license_stops_the_questions_and_proposes_a_rejection():
    service, repo = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López")

    assert service.handle_message(HANDLE, "no") == "[fake] close:no_license (reply within 24 h)"

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.REJECTION_PROPOSED
    assert candidate.state.stage == "closed"
    [event] = [e for e in repo.list_events(candidate.id) if e.type == "rejection_proposed"]
    assert event.payload == {"rule": "no_license", "answer": "no"}
    assert "outcome" not in [e.type for e in repo.list_events(candidate.id)]
    # No further question while the rejection is proposed.
    assert service.handle_message(HANDLE, "full_time") == (
        "Gracias por tu mensaje, un reclutador lo revisará."
    )


def test_no_own_vehicle_proposes_a_rejection():
    service, repo = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes")

    assert service.handle_message(HANDLE, "no") == (
        "[fake] close:no_own_vehicle (reply within 24 h)"
    )

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.REJECTION_PROPOSED
    events = repo.list_events(candidate.id)
    assert [e.payload for e in events if e.type == "rejection_proposed"] == [
        {"rule": "no_own_vehicle", "answer": "no"}
    ]


def test_a_volunteered_no_license_proposes_a_rejection_right_away():
    service, _ = make_service(
        script=[
            Extraction(yes_no=True),
            Extraction(
                name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0),
                license=Extracted(
                    value=License(has_license=False), raw_answer="sin carnet", confidence=1.0
                ),
            ),
        ]
    )
    service.apply(PHONE)
    service.handle_message(HANDLE, "sí")

    assert service.handle_message(HANDLE, "Ana López, sin carnet") == (
        "[fake] close:no_license (reply within 24 h)"
    )


def test_the_license_type_is_kept_when_given():
    service, _ = make_service(
        script=[
            Extraction(yes_no=True),
            Extraction(name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0)),
            Extraction(
                license=Extracted(
                    value=License(has_license=True, type="moped_motorcycle"),
                    raw_answer="sí, de moto",
                    confidence=1.0,
                )
            ),
        ]
    )
    service.apply(PHONE)
    answer(service, "sí", "Ana López")

    assert service.handle_message(HANDLE, "sí, de moto") == "[fake] ask:own_vehicle (attempt 0)"
    license = service.candidate(HANDLE).state.fields["license"]
    assert license.value == License(has_license=True, type="moped_motorcycle")


def test_an_unparseable_license_goes_to_needs_review_not_to_a_rejection():
    service, repo = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López")

    assert service.handle_message(HANDLE, "bueno...") == "[fake] ask:license (attempt 1)"
    assert service.handle_message(HANDLE, "depende") == "[fake] ask:license (attempt 2)"
    assert service.handle_message(HANDLE, "ni idea") == "[fake] ask:own_vehicle (attempt 0)"

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.IN_PROGRESS
    assert candidate.state.fields["license"].status == "needs_review"
    assert "rejection_proposed" not in [e.type for e in repo.list_events(candidate.id)]


def test_a_shared_vehicle_gets_one_follow_up_then_needs_review_and_the_screening_continues():
    service, repo = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes")

    assert service.handle_message(HANDLE, "shared") == "[fake] follow_up:own_vehicle (access)"
    assert service.handle_message(HANDLE, "shared") == "[fake] ask:availability (attempt 0)"
    answer(service, "full_time", "evening", "2", "immediate")
    assert service.handle_message(HANDLE, "yes") == "[fake] close:qualified_to_review"

    candidate = service.candidate(HANDLE)
    own_vehicle = candidate.state.fields["own_vehicle"]
    assert (own_vehicle.status, own_vehicle.value) == (
        "needs_review",
        OwnVehicle(owns_vehicle="shared"),
    )
    events = repo.list_events(candidate.id)
    assert [e.payload for e in events if e.type == "field_needs_review"] == [
        {"field": "own_vehicle"}
    ]


def test_a_clear_answer_to_the_shared_vehicle_follow_up_settles_the_field():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "shared")

    assert service.handle_message(HANDLE, "no") == (
        "[fake] close:no_own_vehicle (reply within 24 h)"
    )


def test_an_expired_license_gets_one_follow_up_then_proposes_a_rejection():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López")

    assert service.handle_message(HANDLE, "expired") == "[fake] follow_up:license (validity)"
    assert service.handle_message(HANDLE, "pending") == (
        "[fake] close:no_license (reply within 24 h)"
    )
    assert service.candidate(HANDLE).status == Status.REJECTION_PROPOSED


def test_an_unclear_answer_to_the_license_follow_up_is_treated_as_no():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "pending")

    assert service.handle_message(HANDLE, "no sé") == (
        "[fake] close:no_license (reply within 24 h)"
    )


def test_a_license_confirmed_valid_after_the_follow_up_continues():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "expired")

    assert service.handle_message(HANDLE, "yes") == "[fake] ask:own_vehicle (attempt 0)"


UNSURE_NAME = Extraction(name=Extracted(value="Ana López", raw_answer="ana lopes", confidence=0.5))


def test_a_value_below_the_confidence_threshold_is_confirmed_before_it_is_kept():
    service, _ = make_service(script=[Extraction(yes_no=True), UNSURE_NAME])
    service.apply(PHONE)
    service.handle_message(HANDLE, "sí")

    assert service.handle_message(HANDLE, "ana lopes") == "[fake] confirm:name=Ana López"
    assert service.candidate(HANDLE).state.fields["name"].status == "empty"
    assert service.handle_message(HANDLE, "yes") == "[fake] ask:license (attempt 0)"

    candidate = service.candidate(HANDLE)
    assert candidate.name == "Ana López"
    assert (candidate.state.fields["name"].status, candidate.state.fields["name"].unconfirmed) == (
        "valid",
        None,
    )


def test_an_unsure_value_the_candidate_denies_uses_an_attempt_and_is_asked_again():
    service, _ = make_service(script=[Extraction(yes_no=True), UNSURE_NAME])
    service.apply(PHONE)
    answer(service, "sí", "ana lopes")

    assert service.handle_message(HANDLE, "no") == "[fake] ask:name (attempt 1)"
    name = service.candidate(HANDLE).state.fields["name"]
    assert (name.status, name.value, name.attempts, name.unconfirmed) == ("empty", None, 1, None)


NO_LICENSE_AFTER_ALL = Extraction(
    license=Extracted(value=License(has_license=False), raw_answer="no tengo", confidence=1.0)
)


def test_a_confirmed_correction_overwrites_the_value_and_reruns_the_knock_outs():
    llm = FakeLLM()
    service, repo = make_service(llm=llm)
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes")
    llm.queue(NO_LICENSE_AFTER_ALL)

    assert service.handle_message(HANDLE, "perdón, no tengo carnet") == (
        "[fake] confirm:license=no"
    )
    assert service.handle_message(HANDLE, "yes") == "[fake] close:no_license (reply within 24 h)"

    candidate = service.candidate(HANDLE)
    assert candidate.status == Status.REJECTION_PROPOSED
    assert candidate.state.fields["license"].raw_answer == "no tengo"
    assert "field_corrected" in [e.type for e in repo.list_events(candidate.id)]


def test_a_denied_correction_keeps_the_previous_value_and_uses_no_attempt():
    llm = FakeLLM()
    service, _ = make_service(llm=llm)
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes")
    llm.queue(NO_LICENSE_AFTER_ALL)
    service.handle_message(HANDLE, "perdón, no tengo carnet")

    assert service.handle_message(HANDLE, "no") == "[fake] ask:availability (attempt 0)"
    license_ = service.candidate(HANDLE).state.fields["license"]
    assert (license_.value, license_.unconfirmed) == (License(has_license=True), None)


def test_the_same_value_again_is_not_a_correction():
    llm = FakeLLM()
    service, _ = make_service(llm=llm)
    service.apply(PHONE)
    answer(service, "yes", "Ana López")
    llm.queue(
        Extraction(
            name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0),
            license=Extracted(value=License(has_license=True), raw_answer="sí", confidence=1.0),
        )
    )

    assert service.handle_message(HANDLE, "Ana López, sí") == "[fake] ask:own_vehicle (attempt 0)"


def test_a_correction_at_the_recap_is_confirmed_then_a_new_recap_is_shown():
    llm = FakeLLM()
    service, _ = make_service(llm=llm)
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes", "full_time", "evening", "2", "immediate")
    llm.queue(Extraction(schedule=Extracted(value="morning", raw_answer="mañanas", confidence=1.0)))

    assert service.handle_message(HANDLE, "no, prefiero mañanas") == (
        "[fake] confirm:schedule=morning"
    )
    assert service.handle_message(HANDLE, "yes") == RECAP.replace("evening", "morning")
    assert service.handle_message(HANDLE, "yes") == "[fake] close:qualified"


def test_a_recap_no_without_a_correction_asks_what_to_change_then_ends_to_review():
    service, _ = make_service()
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes", "full_time", "evening", "2", "immediate")

    assert service.handle_message(HANDLE, "no") == "[fake] ask_correction (attempt 1)"
    assert service.handle_message(HANDLE, "no sé") == "[fake] ask_correction (attempt 2)"
    assert service.handle_message(HANDLE, "nada") == "[fake] close:qualified_to_review"
    assert service.candidate(HANDLE).status == Status.QUALIFIED_TO_REVIEW


def test_a_correction_after_asking_what_to_change_shows_a_new_recap():
    llm = FakeLLM()
    service, _ = make_service(llm=llm)
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes", "full_time", "evening", "2", "immediate")
    service.handle_message(HANDLE, "no")
    llm.queue(Extraction(schedule=Extracted(value="morning", raw_answer="mañanas", confidence=1.0)))

    assert service.handle_message(HANDLE, "el horario") == "[fake] confirm:schedule=morning"
    assert service.handle_message(HANDLE, "yes") == RECAP.replace("evening", "morning")


def test_a_new_value_given_instead_of_a_yes_replaces_the_unsure_one():
    llm = FakeLLM(script=[Extraction(yes_no=True), UNSURE_NAME])
    service, _ = make_service(llm=llm)
    service.apply(PHONE)
    answer(service, "sí", "ana lopes")
    llm.queue(
        Extraction(name=Extracted(value="Ana García", raw_answer="Ana García", confidence=1.0))
    )

    assert service.handle_message(HANDLE, "no, Ana García") == "[fake] ask:license (attempt 0)"
    assert service.candidate(HANDLE).name == "Ana García"


def test_an_unchanged_value_extracted_again_does_not_spare_the_attempt():
    llm = FakeLLM()
    service, _ = make_service(llm=llm)
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes")
    llm.queue(Extraction(name=Extracted(value="Ana López", raw_answer="Ana López", confidence=1.0)))

    assert service.handle_message(HANDLE, "Ana López, ni idea") == (
        "[fake] ask:availability (attempt 1)"
    )


def test_restating_the_previous_value_drops_the_correction():
    llm = FakeLLM()
    service, _ = make_service(llm=llm)
    service.apply(PHONE)
    answer(service, "yes", "Ana López", "yes", "yes")
    llm.queue(
        NO_LICENSE_AFTER_ALL,
        Extraction(
            license=Extracted(value=License(has_license=True), raw_answer="sí", confidence=1.0)
        ),
    )
    service.handle_message(HANDLE, "perdón, no tengo carnet")

    assert service.handle_message(HANDLE, "no, sí tengo") == "[fake] ask:availability (attempt 0)"
    assert service.candidate(HANDLE).state.fields["license"].value == License(has_license=True)
