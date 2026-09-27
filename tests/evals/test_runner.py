"""A persona run end to end, offline: the agent on the FakeLLM, the candidate played by a
FunctionModel answering from a script."""

from datetime import UTC, datetime

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.domain.models import Status
from evals.checks import Expectation, check_run
from evals.personas import Persona
from evals.runner import run_persona

START = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
CONFIG = load_client_config("grupo_sazon")
ALL_ANSWERS = ["yes", "Ana López", "yes", "yes", "Madrid", "full_time", "evening", "2", "immediate"]


def scripted_candidate(*answers: str):
    """A candidate model that sends these messages in order, and records its prompts."""
    remaining = list(answers)
    prompts: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompts.append(str(messages[0].parts[-1].content))
        return ModelResponse(parts=[TextPart(remaining.pop(0))])

    return (lambda: FunctionModel(respond)), prompts


def test_a_persona_plays_the_screening_until_it_closes():
    model, prompts = scripted_candidate(*ALL_ANSWERS, "yes", "never sent")
    persona = Persona(
        key="happy",
        title="Happy path",
        profile="Ana López, lives in Madrid.",
        expect=Expectation(status=Status.QUALIFIED, fields={"start_date": "valid"}),
    )

    run = run_persona(persona, CONFIG, FakeLLM(), model, START)

    assert check_run(persona.expect, run) == []
    assert len(run.replies) == 10  # one per candidate message, the recap and the close
    assert "Ana López, lives in Madrid." in prompts[0]
    assert CONFIG.greeting("es") in prompts[0]  # the candidate sees the conversation


def test_a_silent_persona_gets_the_nudges_then_its_deadline_outcome():
    model, _ = scripted_candidate("yes", "Ana López")
    persona = Persona(
        key="silent",
        title="Silent",
        profile="Answers twice, then goes quiet.",
        expect=Expectation(status=Status.ABANDONED, events=("nudge_sent", "abandoned")),
        silent_after=2,
    )

    run = run_persona(persona, CONFIG, FakeLLM(), model, START)

    assert check_run(persona.expect, run) == []
    assert [e.payload["number"] for e in run.events if e.type == "nudge_sent"] == [1, 2, 3]
    assert run.messages[-1].created_at >= datetime(2026, 9, 28, 10, 0, tzinfo=UTC)


def test_an_erased_candidate_keeps_the_transcript_of_the_run():
    model, _ = scripted_candidate("no")
    persona = Persona(
        key="declines",
        title="Declines",
        profile="Does not want to go on.",
        expect=Expectation(status=Status.QUALIFIED),
    )

    run = run_persona(persona, CONFIG, FakeLLM(), model, START)

    assert run.candidate is None
    assert [(m.role, m.content) for m in run.messages] == [
        ("agent", CONFIG.greeting("es")),
        ("candidate", "no"),
        ("agent", "[fake] close:consent_declined"),
    ]
