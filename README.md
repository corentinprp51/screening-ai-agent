# screening-ai-agent

A messaging agent that screens delivery-driver applicants for a client, and a dashboard where recruiters follow each candidate to an outcome. Agent behavior is specified in [`docs/process_design.md`](docs/process_design.md); vocabulary in [`CONTEXT.md`](CONTEXT.md).

## Run

Requires [uv](https://docs.astral.sh/uv/) and [Task](https://taskfile.dev/).

```
task dev:install   # uv sync
task dev:run       # web chat at http://localhost:8000/chat, dashboard at /dashboard
task dev:cli       # the same screening in the terminal
task dev:test
```

`CLIENT_ID` selects the client config in `config/clients/` (default `grupo_sazon`). `DATABASE_URL` defaults to `sqlite:///data/screening.db`; delete `data/*.db` when the schema changes.

In v0 the FakeLLM does no language understanding: type canonical values (`yes`, `no`, `shared` for a shared vehicle, `Ana López`).

## Architecture

```
app/
  domain/        pure Python: models (state, extraction contract, config), fields (validators), flow (next_action)
  application/   ports.py (LLMPort, CandidateRepository, Clock) + screening_service.py + recruiter_service.py
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
- **Attempts are counted only for the question asked.** Each invalid answer to it uses one attempt and clears the value (the raw answer is kept); the 3rd marks the field needs review and the flow moves on. An invalid answer volunteered for another field is ignored, while valid volunteered answers are kept and not asked again. A needs-review field turns Qualified into Qualified to review.
- **Validators receive "today" as an argument.** The service reads it from the `Clock` port, so the start-date rules (past → invalid, beyond +90 days → valid with a flag) stay pure and testable.
- **Values stay canonical and language-neutral.** Availability is a list of options, a start date is stored as `immediate` or an ISO date, experience is years plus platforms. Pydantic coercion on the extraction contract types the FakeLLM echo (`"full_time, weekends"` → a list, `"2"` → 2 years).
- **An opt-out after consent closes as Withdrawn from any stage.** The extraction's `opt_out` intent sets `opted_out` on the state and `next_action()` returns the Withdrawn close; an `opted_out` event keeps the stage it happened at. During the consent question, a refusal is a declined consent instead.
- **Messages after an outcome never reopen the screening.** Any message while the status is not In progress (an outcome or Rejection proposed) is stored and answered with the fixed `after_close` YAML template in the candidate's last language, with no LLM call. The candidate gets the `message_after_close` flag and moves to the top of the queue; reopening is a recruiter decision.
- **An LLM failure leaves the state unchanged.** Both LLM calls of a turn run before any write, and the turn's events are buffered until the reply exists. If either call raises, the service stores the candidate's message, sends the `fallback` YAML template in their last language, flags `llm_failure` and records an event; the next message gets the same question. The timeout and the single retry with the validation error fed back belong to the v1 PydanticAI adapter; the service only sees the final failure.
- **Knock-outs are checked on every turn, before the fields are walked.** A field's `knock_out` flag comes from the YAML; only a validated "no" (no license, no own vehicle) fails it. A failed knock-out never rejects: the status becomes Rejection proposed, the questions stop, and the `Close` action carries the rule and the YAML review delay so the reply can say when a recruiter answers. A `rejection_proposed` event records the rule and the candidate's answer.
- **An unclear knock-out answer goes to needs review, never to a rejection.** A license answer still unparseable after three attempts is needs review and the screening continues. A shared or borrowed vehicle gets one follow-up on access; an explicit yes or no settles it, anything else marks the field needs review and leaves the knock-out to a recruiter (unlike the surname, it is not accepted with a flag). A license or vehicle type is kept once given and never asked for.
- **One confirmation rule everywhere: unsure values and corrections wait on the candidate's yes.** A value understood below the YAML `confidence_threshold`, or a different value for a field that is already valid, is stored as the field's `unconfirmed` value; `next_action()` returns `Confirm` for it right after the knock-outs, wherever the candidate is in the flow, including the recap. Yes keeps it, and the knock-outs run again on the next `next_action()` (a license corrected to "no" proposes a rejection). No drops it: a correction keeps the previous value, an unsure first answer uses one attempt and is asked again. The same value extracted again is not a correction, and a message that only corrects another answer uses no attempt on the question asked.
- **A recap answered "no" without a correction asks what to change.** Any recap answer that is neither a yes nor a correction counts one recap attempt and gets an `AskCorrection` question; a correction resets the count and a new recap follows. On the 3rd attempt (the same limit as a field) the recap is left unconfirmed and the outcome is Qualified to review.
- **An expired or pending license gets one follow-up, then counts as a no.** The license knock-out passes only on an explicit yes, so a license still expired or pending after the follow-up, or an unclear answer to it, proposes a rejection.
- **A recruiter confirms or overrides a proposed rejection; the proposal itself is never stored.** The failed rule is recomputed by `next_action()` from the state, so the "To confirm" tab, Confirm and Override all read the same thing. Confirm sets Rejected and sends the LLM reply for a `Close` carrying the rule. Override adds the rule to `overridden_knock_outs`, which `next_action()` ignores from then on, and sends the next question right away; a different knock-out failing later proposes a rejection again. Both are refused outside Rejection proposed. Their LLM call runs before any write: on failure the candidate is flagged `llm_failure`, nothing else changes and the recruiter can try again.
- **Three ports only; an outbound message is a stored agent message.** A recruiter action does not push anything: it stores the agent message, and the chat page polls the transcript every 3 s to show it. A channel port (to send a message out) arrives with WhatsApp or SMS, where the provider needs an explicit send.
- **The priority score is a pure function of the valid fields, never the LLM.** `priority_score(state, scoring, today)` in `app/domain/scoring.py` gives 0 to 100 points: shift match 50, as availability overlapping the open shifts' availability (30) plus a schedule in the open shifts or `flexible` (20); start date 30, full up to 7 days then linear down to 0 at 90 days; experience 20, as min(years, 5) / 5 of the weight (× 4 at weight 20). A field that is missing, incomplete or needs review scores 0 on its component. The weights (which must add up to 100) and the open shifts come from the `scoring` block of the YAML; the 7-day, 90-day and 5-year thresholds are domain constants. The score is recomputed on every turn (and set at application), from the `Clock`'s today, and stored on the candidate: the total in a `score` column for sorting, the points per field in `score_json` for the breakdown shown on the candidate detail. The start-date points of a candidate who stops writing are frozen at their last turn; a recompute on the re-engagement tick is left to v2.
- **The queue is sorted by priority score, then by last activity.** Status tabs cover every status; Rejection proposed is labelled "To confirm". The dashboard has no authentication in v0.
