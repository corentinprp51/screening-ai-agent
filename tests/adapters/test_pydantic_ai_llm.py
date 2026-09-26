"""The PydanticAI adapter against a FunctionModel: settings, retries and errors."""

import logging
from datetime import UTC, date, datetime

import pytest
from pydantic_ai import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.pydantic_ai_llm import PydanticAILLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.application.screening_service import ScreeningService
from app.domain.flow import Ask, AskCorrection, Close, Confirm, FollowUp, Greet, Recap
from app.domain.models import CandidateState, Extraction, FieldState, Message, Status

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
CONFIG = load_client_config("grupo_sazon")


class ScriptedModel:
    """A FunctionModel answering from a list, one answer per request, that records what
    it was sent. A dict answer is an extraction, a str a text reply, an exception is raised."""

    def __init__(self, *answers: dict | str | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[tuple[list[ModelMessage], AgentInfo]] = []

    def __call__(self) -> FunctionModel:
        return FunctionModel(self._respond)

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append((messages, info))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, str):
            return ModelResponse(parts=[TextPart(answer)])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])

    def settings(self, request: int = 0) -> dict:
        return dict(self.requests[request][1].model_settings or {})

    def prompt(self, request: int = 0) -> str:
        messages, _ = self.requests[request]
        return str(messages[0].parts[-1].content)


def adapter(model: ScriptedModel) -> PydanticAILLM:
    return PydanticAILLM(model, CONFIG, timeout=15)


def extract(llm: PydanticAILLM, message: str = "sí, claro") -> Extraction:
    return llm.extract(message, Greet(), CandidateState(), date(2026, 9, 26), "¿Seguimos?")


def test_extract_returns_the_extraction_at_temperature_0():
    model = ScriptedModel({"language": "es", "yes_no": True})

    extraction = extract(adapter(model))

    assert extraction == Extraction(language="es", yes_no=True)
    assert model.settings() == {
        "temperature": 0.0,
        "timeout": 15,
        "openai_reasoning_effort": "none",
    }


def test_the_extract_prompt_carries_the_message_today_and_the_question_asked():
    model = ScriptedModel({"language": "es", "yes_no": True})

    extract(adapter(model), message="vale, empezamos")

    prompt = model.prompt()
    assert "vale, empezamos" in prompt
    assert "2026-09-26" in prompt
    assert "¿Seguimos?" in prompt


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (Greet(), "start the screening"),
        (FollowUp("license", missing="validity"), "`license.has_license` true"),
        (Confirm("service_area"), "confirm their `service_area`"),
        (Recap(fields=("name",)), "check all their answers"),
    ],
)
def test_the_extract_prompt_says_where_the_answer_goes(action, expected):
    model = ScriptedModel({"language": "es"})

    adapter(model).extract("sí", action, CandidateState(), date(2026, 9, 26), None)

    assert expected in model.prompt()


def test_an_invalid_extraction_is_retried_once_with_the_error_fed_back():
    model = ScriptedModel({"language": "fr"}, {"language": "en", "yes_no": True})

    extraction = extract(adapter(model))

    assert extraction == Extraction(language="en", yes_no=True)
    retry_messages, _ = model.requests[1]
    [retry] = [p for p in retry_messages[-1].parts if isinstance(p, RetryPromptPart)]
    assert "language" in retry.model_response()


def test_a_second_invalid_extraction_raises():
    model = ScriptedModel({"language": "fr"}, {"language": "fr"})

    with pytest.raises(UnexpectedModelBehavior):
        extract(adapter(model))


def test_an_api_error_raises():
    model = ScriptedModel(ModelHTTPError(status_code=500, model_name="gpt-6-luna"))

    with pytest.raises(ModelHTTPError):
        extract(adapter(model))


def test_a_failed_extraction_reaches_the_service_which_sends_the_fallback():
    model = ScriptedModel({"language": "fr"}, {"language": "fr"})
    service = ScreeningService(
        config=CONFIG,
        llm=adapter(model),
        repo=SqliteCandidateRepository(create_sqlite_engine("sqlite://")),
        clock=FixedClock(NOW),
    )
    service.apply("600000001")

    reply = service.handle_message("600000001", "sí")

    assert reply == CONFIG.templates.fallback["es"]
    assert service.candidate("600000001").state.flags == ["llm_failure"]


def test_reply_writes_the_text_following_the_transcript():
    model = ScriptedModel("Genial, Ana. ¿Tienes carnet de conducir en vigor?")
    transcript = [
        Message(role="agent", content="¿Cómo te llamas?", language="es", created_at=NOW),
        Message(role="candidate", content="Ana López", language="es", created_at=NOW),
    ]

    reply = adapter(model).reply(Ask("license", attempt=0), CandidateState(), "es", transcript)

    assert reply == "Genial, Ana. ¿Tienes carnet de conducir en vigor?"
    assert model.settings()["temperature"] == 0.4
    assert "Ana López" in model.prompt() and "Lucía" in model.prompt()


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (Greet(), "go on with the screening"),
        (Ask("start_date", attempt=1), "give an example"),
        (FollowUp("name", missing="surname"), "surname"),
        (Confirm("service_area"), "Getafe"),
        (Recap(fields=("name", "service_area", "schedule")), "- schedule: —"),
        (AskCorrection(attempt=1), "which answer"),
        (Close(status=None, reason="consent_declined"), "data is deleted"),
        (Close(status=Status.WITHDRAWN), "stopped the screening"),
        (
            Close(status=Status.REJECTION_PROPOSED, reason="no_license", within_hours=24),
            "24 hours",
        ),
        (
            Close(status=Status.REJECTED, reason="outside_service_area", offer_contact=True),
            "location opens near them",
        ),
        (Close(status=Status.QUALIFIED), "within 48 hours"),
    ],
)
def test_the_reply_prompt_describes_every_action(action, expected):
    model = ScriptedModel("ok")
    state = CandidateState(
        fields={
            "name": FieldState(status="valid", value="Ana López"),
            "service_area": FieldState(unconfirmed=FieldState(status="valid", value="Getafe")),
        }
    )

    adapter(model).reply(action, state, "es", [])

    assert expected in model.prompt()


def test_summarize_writes_the_text_from_the_facts():
    model = ScriptedModel("Ana López, qualified: call within 48 h.")
    facts = {"status": "qualified", "fields": {"name": "Ana López"}, "next_action": "Call"}

    summary = adapter(model).summarize(facts, "es")

    assert summary == "Ana López, qualified: call within 48 h."
    assert model.settings()["temperature"] == 0.4
    assert "Ana López" in model.prompt()


def test_token_usage_is_logged_per_call(caplog):
    model = ScriptedModel({"language": "es", "yes_no": True}, "¿Cómo te llamas?")
    llm = adapter(model)

    with caplog.at_level(logging.INFO, logger="app.adapters.llm.pydantic_ai_llm"):
        extract(llm)
        llm.reply(Ask("name", attempt=0), CandidateState(), "es", [])

    assert [record.getMessage().split(":")[0] for record in caplog.records] == [
        "llm extract usage",
        "llm reply usage",
    ]
    assert "input_tokens" in caplog.records[0].getMessage()
