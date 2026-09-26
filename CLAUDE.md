# Candidate Screening Agent

Take-home case: a messaging agent that screens delivery-driver applicants for a company, plus a dashboard for recruiters to track candidates. The code is reviewed live by an engineer: keep it readable, explainable and demo-safe.

## Read before working

- `docs/process_design.md` is the source of truth for agent behavior: stages, fields, validation, edge cases, outcomes, tone. Read it before touching the flow, validators, prompts or outcomes. If code and doc disagree, ask before changing either.
- `README.md` holds architecture and design decisions. When you make a design decision, add it to "Key design decisions".

## Core principle: code decides, the LLM understands and phrases

- Stage order, knock-outs, transitions, outcomes and scoring are deterministic Python, never prompt logic.
- The LLM does two things per turn: (1) extract structured data, language, intent and sentiment from the candidate message; (2) write the reply for an action chosen by code.

## Turn pipeline (`app/core/engine.py`)

input guardrails → LLM extraction (Pydantic structured output, temperature 0) → deterministic validation → state update + `next_action()` → LLM reply for that action → output guardrails → persist

## Architecture: light hexagonal

```
app/
  domain/         # pure Python, no I/O: models, fields (type registry), flow (next_action), scoring
  application/    # ports.py + screening_service.py + recruiter_service.py
  adapters/
    llm/          # pydantic_ai_llm.py, fake_llm.py, prompts/
    persistence/  # sqlite_repo.py, tables.py
    config/       # yaml_loader.py -> ClientConfig (Pydantic)
  api/            # FastAPI routes (JSON under /api, HTML pages) + deps.py (composition root)
  web/templates/  # Jinja2: chat, dashboard, candidate detail, partials/
config/clients/<client>.yaml, config/faq/<client>.md
evals/            # personas + runner; transcripts saved to samples/conversations/
tests/
```

- Exactly three ports (`application/ports.py`): `LLMPort` (extract, reply, summarize), `CandidateRepository`, `Clock`. Do not add other abstractions.
- `domain/` never imports from `adapters/`, FastAPI, SQLModel or PydanticAI.
- JSON routes and HTML routes call the same services. No business logic in routes or templates.

## Storage (SQLite)

- Tables: `candidates` (client_id, handle, name, status, score, city, `state_json`, `score_json`, `summary_json`, timestamps), `messages` (candidate_id, role, content, language, created_at), `events` (candidate_id, type, stage, payload_json, created_at) for analytics.
- `create_all()` at startup, no migrations: delete `data/*.db` when the schema changes.

## Stack

Python 3.12, FastAPI, Pydantic v2, SQLModel + SQLite, PydanticAI, Jinja2 + HTMX + Tailwind (CDN), pytest, uv, Docker.
LLM provider: OpenAI through PydanticAI (`adapters/llm/pydantic_ai_llm.py`), picked by `LLM_MODEL` in `.env`, behind `LLMPort`; empty `LLM_MODEL` runs on the FakeLLM.

## Rules

- Client-specific values (zones, fields, persona, timings) live in YAML config, never hard-coded.
- Validators and `next_action()` are pure functions with unit tests. Tests mock the LLM.
- Every LLM call has a timeout and one retry with the validation error fed back; then a safe fallback message and a flag on the conversation. A turn never raises to the caller.

- All LLM calls go through PydanticAI with a Pydantic output type: the flow is `next_action()`.
- Every LLM call has a timeout and one retry with the validation error fed back; then a safe fallback message and a flag on the conversation. A turn never raises to the caller.
- Read time only through the `Clock` port, never `datetime.now()` in domain or application code.
- Validators and `next_action()` are pure functions with unit tests. Tests use `FakeLLM`, never a real API.
- Agent messages: one question, at most 2 sentences, in the language of the candidate's latest message.
- Frontend: server-rendered Jinja2 + HTMX, Tailwind via CDN, no Node build step.
- Git: use always conventional commits
- Every command goes through the Taskfile (`task …`): never document or run a raw `uv run …` command. A new command gets a new task in `Taskfile.yaml` first.
- No `typing.Any` in `app/` (enforced by ruff TID251): use a precise type, e.g. `pydantic.JsonValue` for JSON data.
- Before committing, run `task dev:format` and `task dev:lint`; commit only when both pass (use `task dev:lint:fix` for auto-fixable issues).

## Build order Suggestion

1. v0: domain + `FakeLLM` (rule-based) + SQLite repo + routes + chat and dashboard pages. End-to-end with no LLM call.
2. v1: PydanticAI adapter for extract, reply and summarize. Only the adapter changes.
3. v2: edge cases, re-engagement with `Clock` and `/api/dev/tick`, analytics, evals and sample conversations, Dockerfile + docker-compose.
4. Complete the README.

## Main Commands

```
task dev:install                          # uv sync
task dev:run                              # web app with reload, chat at http://localhost:8000/chat
task dev:cli                              # terminal chat
task dev:smoke                            # scripted messages against the real LLM (needs .env)
task dev:test                             # pytest (args after --: task dev:test -- tests/domain)
task dev:format                           # ruff format
task dev:lint                             # ruff check
task dev:lint:fix                         # ruff check --fix
```

## Coding guidelines

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Agent skills

### Issue tracker

Issues are tracked in this repo's GitHub Issues (`corentinprp51/screening-ai-agent`) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
