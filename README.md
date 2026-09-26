# screening-ai-agent

A messaging agent that screens delivery-driver applicants for a client, and a dashboard where recruiters follow each candidate to an outcome. Agent behavior is specified in [`docs/process_design.md`](docs/process_design.md); vocabulary in [`CONTEXT.md`](CONTEXT.md).

## Run

Requires [uv](https://docs.astral.sh/uv/) and [Task](https://taskfile.dev/).

```
task dev:install   # uv sync
task dev:run       # web chat at http://localhost:8000/chat
task dev:cli       # the same screening in the terminal
task dev:test
```

`CLIENT_ID` selects the client config in `config/clients/` (default `grupo_sazon`). `DATABASE_URL` defaults to `sqlite:///data/screening.db`; delete `data/*.db` when the schema changes.

In v0 the FakeLLM does no language understanding: type canonical values (`yes`, `no`, `Ana López`).

## Architecture

```
app/
  domain/        pure Python: models (state, extraction contract, config), fields (validators), flow (next_action)
  application/   ports.py (LLMPort, CandidateRepository, Clock) + screening_service.py
  adapters/      llm/fake_llm.py, persistence/ (SQLModel + SQLite), config/yaml_loader.py, clock.py
  api/           deps.py (composition root), json_routes.py (/api), pages.py (HTML + HTMX)
  web/templates/ Jinja2 pages and partials
  cli.py         terminal chat
```

A turn: LLM extraction → validation in code → state update → `next_action()` → LLM reply for that action → persist.

## Key design decisions

- **The next action is derived from field state, not stored as a stage pointer** ([ADR 0001](docs/adr/0001-derive-next-action-from-field-state.md)). `next_action(state, config)` is a pure function: consent, then the configured fields in order, then the recap, then the outcome. The stage is computed from it and written to the candidate row for the dashboard.
- **The FakeLLM is a dumb echo.** It puts the raw message into the slot the pending action asks for and lets Pydantic coercion type it (`"yes"` → `True`); a failed coercion is an invalid answer. Replies are visible `[fake] …` placeholders. Tests can queue scripted extractions instead. No keywords or NLU, so the whole flow is exercised offline and v1 only swaps in a real adapter behind `LLMPort`.
- **Consent declined is a hard delete.** The candidate, its messages and its events are deleted; the closing message is returned to the chat but not stored. It is not an outcome.
- **A missing part gets one follow-up, then is accepted with a flag.** For the name, an answer to the surname follow-up that does not repeat the first name is taken as the surname(s) (`Ana` + `López García`).
