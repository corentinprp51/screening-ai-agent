"""The code checks of an eval run: what code can decide is never left to the judge.

A run meets its persona's expectation (status, failed rule, field statuses and values,
flags, events, language), no LLM call failed, and every reply the candidate got keeps the
message rules. The rules are checked again here, on what reached the candidate, rather than
trusted to the adapter's guardrails."""

import re
from collections import Counter
from dataclasses import dataclass, field

from app.adapters.llm.pydantic_ai_llm import EMOJI, MAX_REPLY_CHARS, MAX_REPLY_SENTENCES
from app.domain.fields import format_value
from app.domain.flow import Action, Close, Recap
from app.domain.models import Candidate, Event, FieldStatus, Language, Message, Status


@dataclass(frozen=True)
class Expectation:
    """What code asserts at the end of a run. `values` are compared as the dashboard shows
    them (`format_value`)."""

    status: Status
    rule: str | None = None  # the failed rule of a proposed rejection
    fields: dict[str, FieldStatus] = field(default_factory=dict)
    values: dict[str, str] = field(default_factory=dict)
    flags: tuple[str, ...] = ()
    events: tuple[str, ...] = ()  # an event listed n times must happen at least n times
    language: Language | None = None


@dataclass(frozen=True)
class Reply:
    """A reply the LLM wrote, with the action code chose for it."""

    action: Action
    text: str


@dataclass
class Run:
    """The end of a persona run. `candidate` is None when the candidate was erased."""

    candidate: Candidate | None
    events: list[Event]
    messages: list[Message]
    replies: list[Reply]


def check_run(expect: Expectation, run: Run) -> list[str]:
    """Every missed expectation and broken message rule, as one line each."""
    candidate = run.candidate
    if candidate is None:
        return ["the candidate was erased"]
    state = candidate.state
    failures = []
    if candidate.status != expect.status:
        failures.append(f"status: expected {expect.status}, got {candidate.status}")
    if expect.rule is not None:
        rules = [e.payload.get("rule") for e in run.events if e.type == "rejection_proposed"]
        rule = rules[-1] if rules else None
        if rule != expect.rule:
            failures.append(f"rule: expected {expect.rule}, got {rule}")
    for field_type, status in expect.fields.items():
        if state.field(field_type).status != status:
            failures.append(
                f"{field_type}: expected {status}, got {state.field(field_type).status}"
            )
    for field_type, value in expect.values.items():
        shown = format_value(state.field(field_type).value)
        if shown != value:
            failures.append(f"{field_type}: expected {value}, got {shown}")
    flags = state.all_flags()
    failures += [f"flag missing: {flag}" for flag in expect.flags if flag not in flags]
    happened = Counter(e.type for e in run.events)
    for event_type, times in Counter(expect.events).items():
        if not happened[event_type]:
            failures.append(f"event missing: {event_type}")
        elif happened[event_type] < times:
            failures.append(f"event {event_type}: expected {times}, got {happened[event_type]}")
    if expect.language is not None and state.language != expect.language:
        failures.append(f"language: expected {expect.language}, got {state.language}")
    if "llm_failure" in flags:
        failures.append("an LLM call failed: the candidate got the fallback")
    for number, reply in enumerate(run.replies, start=1):
        failures += [f"reply {number}: {broken}" for broken in message_rule_violations(reply)]
    return failures


def message_rule_violations(reply: Reply) -> list[str]:
    """One question, at most 2 sentences and 300 characters (the recap is a list, exempt),
    and at most one emoji, only in a closing message."""
    text = reply.text.strip()
    broken = []
    if not isinstance(reply.action, Recap):
        if len(text) > MAX_REPLY_CHARS:
            broken.append(f"{len(text)} characters, at most {MAX_REPLY_CHARS}")
        sentences = [part for part in re.split(r"(?<=[.!?…])\s+", text) if part]
        if len(sentences) > MAX_REPLY_SENTENCES:
            broken.append(f"{len(sentences)} sentences, at most {MAX_REPLY_SENTENCES}")
    if text.count("?") > 1:
        broken.append(f"{text.count('?')} questions, at most 1")
    emoji = len(EMOJI.findall(text))
    if emoji and not isinstance(reply.action, Close):
        broken.append("an emoji outside a closing message")
    elif emoji > 1:
        broken.append(f"{emoji} emoji, at most 1")
    return broken
