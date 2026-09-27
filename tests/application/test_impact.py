"""The Impact view, on a seeded repository: known candidates, messages and events."""

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.recruiter_service import RecruiterService
from app.domain.models import Candidate, Event, FieldState, Message, Status

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


class Seed:
    def __init__(self) -> None:
        self.config = load_client_config("grupo_sazon")
        self.repo = SqliteCandidateRepository(create_sqlite_engine("sqlite://"))
        self.service = RecruiterService(
            config=self.config, llm=FakeLLM(), repo=self.repo, clock=FixedClock(NOW)
        )

    def candidate(
        self,
        handle: str,
        status: Status,
        *,
        days_ago: float = 1,
        consent: bool | None = True,
        greeted_after_minutes: float = 0,
        needs_review: bool = False,
        tokens: tuple[int, int] | None = None,
    ) -> None:
        created_at = NOW - timedelta(days=days_ago)
        candidate = Candidate(
            client_id="grupo_sazon",
            handle=handle,
            status=status,
            created_at=created_at,
            updated_at=created_at,
        )
        candidate.state.consent = consent
        if needs_review:
            candidate.state.fields["license"] = FieldState(status="needs_review")
        candidate = self.repo.save(candidate)
        greeted_at = created_at + timedelta(minutes=greeted_after_minutes)
        self.repo.add_message(
            candidate.id,
            Message(role="agent", content="Hola", language="es", created_at=greeted_at),
        )
        if tokens:
            self.repo.add_event(
                candidate.id,
                Event(
                    type="llm_call",
                    stage="consent",
                    payload={
                        "call": "reply",
                        "input_tokens": tokens[0],
                        "output_tokens": tokens[1],
                    },
                    created_at=greeted_at,
                ),
            )


@pytest.fixture
def seed():
    seed = Seed()
    seed.candidate("1", Status.QUALIFIED, greeted_after_minutes=2, tokens=(1_000_000, 500_000))
    seed.candidate("2", Status.QUALIFIED_TO_REVIEW, needs_review=True)
    seed.candidate("3", Status.REJECTION_PROPOSED)
    seed.candidate("4", Status.ABANDONED)
    seed.candidate("5", Status.IN_PROGRESS, consent=None)  # the greeting is not answered yet
    # Before the 30 days: left out.
    seed.candidate("6", Status.QUALIFIED, days_ago=31, tokens=(9_000_000, 9_000_000))
    seed.repo.add_consent_drop_off("grupo_sazon", NOW - timedelta(days=2))
    seed.repo.add_consent_drop_off("grupo_sazon", NOW - timedelta(days=40))
    return seed


def metrics(seed: Seed) -> dict[str, tuple[float | None, float | None, float | None]]:
    return {m.name: (m.value, m.baseline, m.target) for m in seed.service.impact().metrics}


def test_the_funnel_counts_the_last_30_days(seed):
    impact = seed.service.impact()

    assert impact.since == NOW - timedelta(days=30)
    assert (impact.candidates, impact.consent_drop_offs, impact.consented, impact.completed) == (
        5,
        1,
        4,
        3,
    )


def test_the_completion_rate_counts_completed_screenings_out_of_consents(seed):
    # 3 completed (Qualified, Qualified to review, Rejection proposed) out of 4 consents,
    # next to the 40% reached by phone (60% never answer) and the 60% target.
    assert metrics(seed)["completion_rate"] == (0.75, pytest.approx(0.4), 0.6)


def test_the_time_to_first_message_is_the_average_delay_of_the_greeting(seed):
    assert metrics(seed)["first_message_minutes"] == (pytest.approx(0.4), None, 5)


def test_the_hours_saved_count_the_calls_and_the_unanswered_attempts_avoided(seed):
    # (3 completed + 6 contacted × 1.5 unanswered attempts) × 15 min = 3 h in 30 days,
    # per week next to the 975 calls a week by phone (13 recruiters × 15 calls × 5 days).
    value, baseline, target = metrics(seed)["hours_saved_per_week"]

    assert value == pytest.approx(3 * 7 / 30)
    assert baseline == pytest.approx(975 * 15 / 60)
    assert target is None


def test_the_share_of_recruiter_time_on_qualified_candidates_is_an_estimate(seed):
    [metric] = [m for m in seed.service.impact().metrics if m.name == "qualified_time_share"]

    assert (metric.value, metric.baseline, metric.target) == (
        pytest.approx(2 / 3),
        pytest.approx(0.2),
        0.8,
    )
    assert "estimate" in metric.label.lower()


def test_the_needs_review_share_counts_completed_screenings_with_a_needs_review_field(seed):
    assert metrics(seed)["needs_review_share"] == (pytest.approx(1 / 3), None, 0.1)


def test_the_llm_cost_per_candidate_is_priced_from_the_recorded_tokens(seed):
    # 1M input × 0.4 + 0.5M output × 1.6 = 1.2 over 5 candidates, next to a 15 min call at 20/h.
    assert metrics(seed)["llm_cost_per_candidate"] == (pytest.approx(0.24), 5.0, None)


def test_with_no_candidates_nothing_is_divided_by_zero():
    values = {m.name: m.value for m in Seed().service.impact().metrics}

    assert values == {
        "completion_rate": None,
        "first_message_minutes": None,
        "hours_saved_per_week": 0,
        "qualified_time_share": None,
        "needs_review_share": None,
        "llm_cost_per_candidate": None,
    }
