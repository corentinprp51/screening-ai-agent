"""The real LLM: PydanticAI behind `LLMPort`, one Jinja2 Markdown prompt per call.

- extract: `Extraction` as output type, at temperature 0; an invalid output gets one
  retry with the validation error fed back.
- reply / summarize: text, at a slightly higher temperature. A reply breaking the message
  rules, or a summary longer than 3 lines or 600 characters, gets one retry with the
  reason fed back.
- Synchronous, with a fresh OpenAI client per call (ADR 0002). Every error (a timeout,
  an API error, a second invalid output) reaches the service, which sends the fallback.
"""

import logging
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pydantic_ai
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from openai import AsyncOpenAI
from pydantic import JsonValue
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from app.domain.fields import format_value
from app.domain.flow import Action, Close, Recap
from app.domain.models import CandidateState, ClientConfig, Extraction, Language, Message

logger = logging.getLogger(__name__)
# No startup banner: it would land in the middle of the terminal chat.
pydantic_ai.BANNER_ENABLED = False

EXTRACT_TEMPERATURE = 0.0
WRITE_TEMPERATURE = 0.4
MAX_MESSAGE_CHARS = 1000  # a longer candidate message is cut before it reaches the prompt
MESSAGE_TAG = "candidate_message"
MAX_SUMMARY_LINES = 3
MAX_SUMMARY_CHARS = 600
LANGUAGE_NAMES: dict[Language, str] = {"es": "Spanish", "en": "English"}
MAX_REPLY_CHARS = 300
MAX_REPLY_SENTENCES = 2
EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff]")

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
        self._replier = Agent(output_type=str, deps_type=Action, retries={"output": 1})
        self._replier.output_validator(_check_reply)
        self._summarizer = Agent(output_type=str, retries={"output": 1})
        self._summarizer.output_validator(_check_summary)

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
            max_sentences=MAX_REPLY_SENTENCES,
            call_within_hours=self._config.call_within_hours,
            kind=type(action).__name__,
            action=action,
            values={field: _display(state, field) for field in state.fields},
            name=state.field("name").value if state.field("name").status == "valid" else None,
            language=LANGUAGE_NAMES[language],
            transcript=transcript,
        )
        return self._run("reply", self._replier, prompt, WRITE_TEMPERATURE, deps=action)

    def summarize(self, facts: dict[str, JsonValue], language: Language) -> str:
        prompt = _prompts.get_template("summarize.md").render(
            facts=facts,
            language=LANGUAGE_NAMES[language],
            max_chars=MAX_SUMMARY_CHARS,
        )
        return self._run("summarize", self._summarizer, prompt, WRITE_TEMPERATURE)

    def _run[D, T](
        self, call: str, agent: Agent[D, T], prompt: str, temperature: float, deps: D | None = None
    ) -> T:
        result = agent.run_sync(
            prompt,
            deps=deps,
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


def _check_summary(summary: str) -> str:
    """The summary fits the dashboard, and is stored without its blank lines."""
    lines = [line.strip() for line in summary.splitlines() if line.strip()]
    summary = "\n".join(lines)
    if len(lines) > MAX_SUMMARY_LINES:
        raise ModelRetry(
            f"The summary has {len(lines)} lines: write at most {MAX_SUMMARY_LINES} lines."
        )
    if len(summary) > MAX_SUMMARY_CHARS:
        raise ModelRetry(
            f"The summary has {len(summary)} characters: "
            f"write at most {MAX_SUMMARY_CHARS} characters."
        )
    return summary


def _check_reply(ctx: RunContext[Action], reply: str) -> str:
    """The message rules, checked by code before the candidate sees the reply. The recap is
    a list, exempt from the length and sentence limits; an emoji only closes a screening."""
    text = reply.strip()
    if not isinstance(ctx.deps, Recap):
        if len(text) > MAX_REPLY_CHARS:
            raise ModelRetry(
                f"The message has {len(text)} characters: "
                f"write at most {MAX_REPLY_CHARS} characters."
            )
        sentences = [part for part in re.split(r"(?<=[.!?…])\s+", text) if part]
        if len(sentences) > MAX_REPLY_SENTENCES:
            raise ModelRetry(
                f"The message has {len(sentences)} sentences: "
                f"write at most {MAX_REPLY_SENTENCES} sentences."
            )
    if text.count("?") > 1:
        raise ModelRetry("The message asks several questions: ask exactly one question.")
    if EMOJI.search(text) and not isinstance(ctx.deps, Close):
        raise ModelRetry("The message has an emoji: use no emoji outside a closing message.")
    return reply


def _display(state: CandidateState, field: str) -> str:
    """The field as the reply may mention it: its pending value first, as in a Confirm."""
    field_state = state.field(field)
    if field_state.unconfirmed is not None:
        return format_value(field_state.unconfirmed.value)
    if field_state.status == "needs_review":
        return "to be checked by a recruiter"
    return format_value(field_state.value)
