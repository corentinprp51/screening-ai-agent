# Screening Agent Process Design

## 1. Context and goal

The agent moves first-contact screening from phone calls to asynchronous messaging, so recruiters spend their time on qualified candidates only.

- **Volume:** \~1,000 applications/week across 45 locations in Spain and Mexico.
- **Capacity today:** 13 recruiters × \~15 calls/day ≈ 975 calls/week, saturated before any follow-up call.
- **Pain:** 60% of candidates never answer; 80% of recruiter time goes to unqualified candidates.

| Success metric (30-day pilot)                    | Initial target, to agree with client                                                             |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| Screening completion rate                        | ≥ 60% of contacted candidates (≈ 40% reached by phone today)                                     |
| Time from application to first message           | < 5 minutes                                                                                      |
| Recruiter hours saved per week                   | Tracked: screenings done by the agent × average call time, plus unanswered call attempts avoided |
| Recruiter time spent on qualified candidates     | > 80% (≈ 20% today)                                                                              |
| Conversations with a field marked `needs_review` | < 10%                                                                                            |
| LLM cost per screened candidate                  | Tracked and compared with the cost of a recruiter call                                           |

These figures are computed from live data in an impact view of the recruiter dashboard. Baseline values (call duration, calls per day) sit in the client config and are shown next to the figures.

## 2. Discovery questions and client answers

The kickoff questions below were answered by Orbio on the client's behalf. The design follows those answers; where a choice was left open, the decision and its reason are stated.

| Question                                                      | Answer and design decision                                                                                                                                                                                                                            |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Which channel, and what triggers first contact?               | Text first, never a call: WhatsApp or SMS depending on the country, voice offered as an option. First message within minutes of the application. The prototype uses a web chat behind a channel adapter, so WhatsApp or SMS is one adapter to add.    |
| What exactly is a service area?                               | Assumption confirmed: a list of cities and zones per country in the client config, fuzzy-matched and confirmed with the candidate when unsure.                                                                                                        |
| Which criteria are knock-outs?                                | License, zone and vehicle ownership are the usual knock-outs. **Decision:** all three, assuming client has no delivery fleet. Experience is never a knock-out: in a high-turnover role it would shrink the pool, so it only feeds the priority score. |
| Are rejections automatic or reviewed?                         | **No automated rejection** (GDPR Art. 22, EU AI Act). A knock-out only proposes a rejection; a recruiter confirms it before any rejection message is sent.                                                                                            |
| What do recruiters use today, and what comes after screening? | Assumption: a standalone dashboard with a next action per candidate, plus an ATS integration spec. Next step: recruiter call or interview slot.                                                                                                       |
| How will success be measured?                                 | Completion rate and recruiter hours saved, plus the other metrics in section 1, shown in an impact view of the dashboard.                                                                                                                             |

## 3. Conversation stages and branching

The stage order is enforced in code; the LLM only interprets answers and phrases the next question.

&#91;embedded content: screening flow · 10 stages, 3 knock-outs\]

How to read the flow:

1. **Consent first.** The opening message states who is writing, why, and asks to continue (GDPR). A "no" closes the chat and deletes the data.
2. **Gate.** Greeting and name, then the three knock-out checks: license, own vehicle, zone. A failed check stops the questions and proposes a rejection; a recruiter either confirms it, and only then is a rejection message sent, or reopens the conversation.
3. **C\*\***a\***\*n\*\***d\***\*idate \*\***I\***\*n\*\***f\***\*or\*\***ma\***\*tion\*\***.\*\* Availability, schedule, experience and start date. None of them disqualifies; they feed a priority score.
4. **Close.** The recap lets the candidate fix any answer, then the status becomes Qualified and the summary goes to the recruiter.

**For \*\***e\***\*very** **sta\*\***ge\***\*:** questions, opt-outs, silence and repeated invalid answers are handled without losing the candidate's place in the flow.

## 4. Agent personality and message guidelines

The agent is **Lucía, from the client hiring team**: warm, direct and quick, like a friendly shift manager rather than an HR form.

- **Transparent:** the first message says Lucía is a virtual assistant and a recruiter reviews every profile.
- **Register:** informal "tú" in Spain and Mexico. Local words are understood both ways (carnet / licencia, moto / motoneta, Uber Eats / Didi Food).
- **Length:** one question per message, at most 2 sentences and \~250 characters. The \~250 characters are the target the agent writes to; code checks every reply but the recap against 300 characters, a tolerance, and a longer one is rewritten once, then replaced by a safe message and the conversation flagged. Lists only in the final recap.
- **Emoji:** at most one, only in the greeting and the closing.
- **Adaptive:** a confused candidate gets simpler wording and an example; a frustrated one gets a short acknowledgment and the option to talk to a person.
- **Never:** promise a job or a salary, ask about age, nationality, health or immigration status, or go off-topic beyond the FAQ.

## 5. Data fields and validation rules

The LLM extracts values; deterministic Python code validates them and decides the next step.

| Field               | Stored as                                           | Valid when                                                         | Invalid or ambiguous                                                                                         |
| ------------------- | --------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| Full name           | string                                              | First name and surname, letters only                               | Only a first name: ask once for the surname, then accept and flag                                            |
| Driver's license    | `has_license` bool + type (car / moped-motorcycle)  | Explicit yes or no                                                 | **No → rejection proposed.** Expired or pending: one confirmation question, then treated as No               |
| Own vehicle         | `owns_vehicle` bool + type (car / moped-motorcycle) | Explicit yes or no                                                 | **No → rejection proposed.** Shared or borrowed vehicle: one clarifying question, then marked `needs_review` |
| City / zone         | country, city, zone, `in_service_area`              | Matches the client's area list after normalization                 | Close match: confirm ("¿Getafe, en Madrid?"). **Outside → rejection proposed**                               |
| Availability        | list of `full_time`, `part_time`, `weekends`        | At least one value; full_time and part_time are mutually exclusive | Map free text ("solo findes" → weekends), else offer the 3 options                                           |
| Preferred schedule  | `morning`, `afternoon`, `evening`, `flexible`       | One value                                                          | "Me da igual" → flexible, else offer the options                                                             |
| Delivery experience | years (number) + platforms (list)                   | 0 to 40 years; 0 is valid, not a knock-out                         | "Un par de años" → 2; platforms mapped to a known list or "other"                                            |
| Start date          | ISO date or `immediate`                             | Today to +90 days                                                  | Relative dates resolved ("el lunes"); beyond 90 days kept and flagged                                        |

Each value keeps the candidate's raw answer and a confidence score. Below a configurable value (e.g. 0.7) the agent asks for confirmation. After 2 failed re-asks the field is marked `needs_review` and the flow moves on, so a candidate is never stuck in a loop.

## 6. Edge cases

Every edge case ends in a defined state; none can crash the conversation or leave a candidate without a reply.

| Case                            | Agent behavior                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Stops responding**            | Short Nudge at +1 h, a second Nudge at +20 h ("solo quedan 3 preguntas"), both inside WhatsApp's 24 h window. Then one approved template at +48 h, and `abandoned` at +72 h. Nudges are timed from the last unanswered question, with no quiet hours (see known limits). On return, a one-line recap and the flow resumes at the same stage. If only the recap confirmation is missing, the outcome is Qualified, to review, not abandoned. |
| **Invalid or ambiguous answer** | Re-ask with a reworded question and an example, max 2 times, then `needs_review` (section 5). Audio or images: ask for a text reply.                                                                                                                                                                                                                                     |
| **Switches language**           | Language detected on every message; the reply follows the latest one. Mixed messages get the dominant language. A message with no clear language ("ok", "2", a name) keeps the current one; any other language falls back to the closest of es / en, else the client's default. Data is stored as language-neutral values.                                                                                                                                                                                                               |
| **Corrects an earlier answer**  | "Perdón, vivo en Móstoles": confirm, overwrite, re-run the knock-out check.                                                                                                                                                                                                                                                                                              |
| **Asks a question**             | Never answered by the agent (no FAQ): "I'll pass it to the recruiter", the question is logged on the profile in the candidate's words and flagged in the queue. Then back to the pending question; an answer in the same message is kept, and asking uses no attempt.                                                                                                     |
| **Opts out**                    | "No me interesa" or "stop": polite close, no more nudges, status `withdrawn`.                                                                                                                                                                                                                                                                                            |
| **Abuse or prompt injection**   | One neutral refocus; if repeated, polite close and flag. Injected text cannot change the flow, which is decided in code.                                                                                                                                                                                                                                                 |
| **Applies twice**               | Same phone number: resume the existing conversation instead of restarting.                                                                                                                                                                                                                                                                                               |
| **Technical failure**           | LLM timeout or invalid output: one retry, then a safe message to the candidate and the conversation is flagged.                                                                                                                                                                                                                                                          |

**Known limits.** Nudges have no quiet hours and no time zones: the delays run on server time from the last unanswered question, so a Nudge can reach a candidate at night.

## 7. Outcomes and recruiter handoff

Every conversation ends in one of five outcomes, each with a message for the candidate and a next action for the recruiter. A failed knock-out first sets the status to **Rejection proposed**, a waiting state and not an outcome: no rejection is sent without a recruiter's confirmation (GDPR Art. 22, EU AI Act), and confirming takes seconds, versus minutes for a screening call.

| Outcome                  | Trigger                                                                                                                         | Candidate receives                                                                                                                                                                  | Recruiter sees                                                                                                                                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Qualified**            | All fields valid, no knock-out, recap confirmed                                                                                 | Thanks + clear next step: a recruiter calls within X hours, or an interview slot                                                                                                    | Top of the queue, ranked by priority score, with summary and full JSON                                                                                                                                            |
| **Qualified, to review** | All data valid but the recap went unconfirmed after the nudges, or qualified with at least one field `needs_review`             | Same message as qualified                                                                                                                                                           | Review queue, fields marked `needs_review` highlighted                                                                                                                                                            |
| **Rejected**             | A knock-out failed (no license, no own vehicle, or zone outside service areas) and a recruiter confirmed the proposed rejection | While proposed: questions stop, a recruiter replies within X hours. Once confirmed: neutral message with the reason; outside zone: offer to be contacted if a nearby location opens | While proposed: "To confirm" queue with the rule and the candidate's answer. Confirm: rejection message sent (approved template if outside the 24 h window). Override: the conversation resumes at the next stage |
| **Withdrawn**            | Candidate opts out                                                                                                              | Short confirmation                                                                                                                                                                  | Archived; data kept only per the retention policy                                                                                                                                                                 |
| **Abandoned**            | No reply after 72 h, required data still missing                                                                                | Last reminder template                                                                                                                                                              | Drop-off stage in analytics; can be reopened                                                                                                                                                                      |

The priority score is deterministic and explainable: availability matching open shifts, earliest start date, then experience. No LLM ranks candidates. A 3-line summary is generated when the conversation ends, with key data, points of attention and the suggested next action.

## Appendix: sample messages

The greeting is the only message allowed a third sentence, because it carries the AI disclosure and the consent request.

| Moment                                  | Message                                                                                                                                                                      |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Greeting + consent                      | ¡Hola! Soy Lucía, asistente virtual de selección de {cliente} 👋 Te escribo por tu candidatura de repartidor/a: son 2 minutos y un reclutador revisa cada perfil. ¿Seguimos? |
| Knock-out question                      | Genial, Ana. ¿Tienes carnet de conducir en vigor, de coche o de moto?                                                                                                        |
| Ambiguous zone                          | ¿Te refieres a Getafe, en Madrid?                                                                                                                                            |
| Language switch                         | _"Sorry, can we do this in English?"_ → Of course! Which city or area do you live in?                                                                                        |
| Nudge at +1 h                           | ¿Seguimos, Ana? Solo quedan 3 preguntas.                                                                                                                                     |
| Knock-out, rejection proposed           | Gracias, Ana. Un reclutador revisará tu perfil y te responderá en las próximas X h.                                                                                          |
| Rejection, after recruiter confirmation | Gracias, Ana. Para este puesto necesitamos carnet de conducir, así que por ahora no podemos seguir con tu candidatura. ¡Mucha suerte!                                        |
| Qualified                               | ¡Listo, Ana! Tu perfil encaja con lo que buscamos. Un reclutador te llamará en las próximas X h.                                                                             |

To avoid:

- Email register: "Estimado/a candidato/a, le agradecemos su interés en formar parte de nuestra organización…"
- Several questions at once: "¿Tienes carnet, en qué ciudad vives y cuál es tu disponibilidad?"
