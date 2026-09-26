"""The real LLM: PydanticAI behind `LLMPort`, one Jinja2 Markdown prompt per call.

- extract: `Extraction` as output type, at temperature 0; an invalid output gets one
  retry with the validation error fed back.
- reply / summarize: text, at a slightly higher temperature.
- Synchronous, with a fresh OpenAI client per call (ADR 0002). Every error (a timeout,
  an API error, a second invalid output) reaches the service, which sends the fallback.
"""

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pydantic_ai
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from openai import AsyncOpenAI
from pydantic import JsonValue
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from app.domain.fields import format_value
from app.domain.flow import Action
from app.domain.models import CandidateState, ClientConfig, Extraction, Language, Message

logger = logging.getLogger(__name__)
# No startup banner: it would land in the middle of the terminal chat.
pydantic_ai.BANNER_ENABLED = False

EXTRACT_TEMPERATURE = 0.0
WRITE_TEMPERATURE = 0.4
MAX_MESSAGE_CHARS = 1000  # a longer candidate message is cut before it reaches the prompt
MESSAGE_TAG = "candidate_message"
LANGUAGE_NAMES: dict[Language, str] = {"es": "Spanish", "en": "English"}

_prompts = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "prompts"),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def openai_model(model_name: str, api_key: str) -> Callable[[], Model]:
    """Builds the model with a fresh OpenAI client for each call (ADR 0002). The SDK
    retries a failed request once (a connection error, a 429 or a 5xx)."""

    def build() -> Model:
        client = AsyncOpenAI(api_key=api_key, max_retries=1)
        return OpenAIResponsesModel(model_name, provider=OpenAIProvider(openai_client=client))

    return build


class PydanticAILLM:
    def __init__(self, model: Callable[[], Model], config: ClientConfig, timeout: float) -> None:
        self._model = model
        self._config = config
        self._timeout = timeout
        self._extractor = Agent(output_type=Extraction, retries={"output": 1})
        self._writer = Agent(output_type=str)

    def extract(
        self,
        message: str,
        action: Action,
        state: CandidateState,
        today: date,
        last_agent_message: str | None,
    ) -> Extraction:
        # The message is data: capped, and without angle brackets it cannot write a tag that
        # closes the delimiters wrapping it, whatever its spelling.
        message = message.replace("<", "").replace(">", "")
        prompt = _prompts.get_template("extract.md").render(
            message=message[:MAX_MESSAGE_CHARS],
            tag=MESSAGE_TAG,
            kind=type(action).__name__,
            action=action,
            today=today.isoformat(),
            language=state.language,
            default_language=self._config.default_language,
            platforms=self._config.platforms,
            last_agent_message=last_agent_message,
        )
        return self._run("extract", self._extractor, prompt, EXTRACT_TEMPERATURE)

    def reply(
        self,
        action: Action,
        state: CandidateState,
        language: Language,
        transcript: list[Message],
    ) -> str:
        prompt = _prompts.get_template("reply.md").render(
            persona=self._config.persona,
            call_within_hours=self._config.call_within_hours,
            kind=type(action).__name__,
            action=action,
            values={field: _display(state, field) for field in state.fields},
            name=state.field("name").value if state.field("name").status == "valid" else None,
            language=LANGUAGE_NAMES[language],
            transcript=transcript,
        )
        return self._run("reply", self._writer, prompt, WRITE_TEMPERATURE)

    def summarize(self, facts: dict[str, JsonValue], language: Language) -> str:
        prompt = _prompts.get_template("summarize.md").render(
            facts=facts, language=LANGUAGE_NAMES[language]
        )
        return self._run("summarize", self._writer, prompt, WRITE_TEMPERATURE)

    def _run[T](self, call: str, agent: Agent[None, T], prompt: str, temperature: float) -> T:
        result = agent.run_sync(
            prompt,
            model=self._model(),
            model_settings=OpenAIResponsesModelSettings(
                temperature=temperature,
                timeout=self._timeout,
                # Without it, gpt-6-luna reasons and ignores the temperature.
                openai_reasoning_effort="none",
            ),
        )
        logger.info("llm %s usage: %s", call, result.usage)
        return result.output


def _display(state: CandidateState, field: str) -> str:
    """The field as the reply may mention it: its pending value first, as in a Confirm."""
    field_state = state.field(field)
    if field_state.unconfirmed is not None:
        return format_value(field_state.unconfirmed.value)
    if field_state.status == "needs_review":
        return "to be checked by a recruiter"
    return format_value(field_state.value)
