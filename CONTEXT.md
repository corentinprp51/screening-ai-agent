# Candidate Screening

A messaging agent that screens delivery-driver applicants on behalf of a client, and a dashboard where the client's recruiters follow each candidate to an outcome.

## Language

### Parties

**Client**:
The hiring company the agent screens for (e.g. Grupo Sazón). It owns the service areas, fields, persona and open shifts.
_Avoid_: customer, tenant

**Candidate**:
An applicant together with their single screening for one client, identified by their handle.
_Avoid_: applicant, lead, user

**Handle**:
The candidate's identifier on the messaging channel (a phone number). Applying twice with the same handle resumes the same screening.
_Avoid_: phone, user id

**Recruiter**:
A client employee who works the candidate queue on the dashboard and can override an outcome.

### Screening

**Screening**:
The message exchange that collects a candidate's fields and ends in an outcome. It belongs to the candidate and is not a separate thing.
_Avoid_: conversation (as a thing of its own), interview, session

**Consent**:
The candidate's agreement to continue, asked in the greeting before any field. Declining it erases the candidate.

**Field**:
One piece of candidate data the screening collects (name, license, own vehicle, service area, availability, schedule, experience, start date), declared per client.

**Field type**:
The kind of a field, which fixes how its value is validated.

**Stage**:
Where the screening currently stands: consent, one of the fields, or the recap.

**Recap**:
The final list of captured fields that the candidate confirms or corrects before an outcome.

**Knock-out**:
A rule whose failure stops the questions and proposes a rejection: no valid driver's license, no own vehicle, or a city outside the service area.

**Service area**:
The cities, and optionally their zones, that a client serves in each country. The city is what the knock-out checks.

**Needs review**:
A field a recruiter must check: the agent could not validate it after two re-asks, or the answer leaves a knock-out undecided (e.g. a shared vehicle). It turns a Qualified outcome into Qualified to review.

**Flag**:
A note for the recruiter attached to a candidate or a field (e.g. surname missing, start date beyond 90 days). It never changes the outcome.
_Avoid_: warning, alert

### Outcomes

**Status**:
In progress, Rejection proposed, or one of the five outcomes.

**Outcome**:
The status a screening ends in: Qualified, Qualified to review, Rejected, Withdrawn or Abandoned.

**Qualified**:
All fields valid, no knock-out, recap confirmed.

**Qualified to review**:
Qualified, but with at least one field needing review or a recap left unconfirmed.
_Avoid_: to review (too close to "needs review")

**Rejection proposed**:
The waiting status after a knock-out fails: questions stop until a recruiter confirms the rejection or overrides it. It is not an outcome.
_Avoid_: disqualified

**Rejected**:
A proposed rejection that a recruiter confirmed. Only then is the candidate told.

**Withdrawn**:
The candidate opted out after giving consent.

**Abandoned**:
The candidate stopped answering before the required fields were captured.

**Override**:
A recruiter's decision to set aside a proposed rejection. The screening resumes at the next field.

### Ranking

**Priority score**:
A 0 to 100 number that ranks candidates by shift match, start date and experience. It is computed by rules, never by the LLM.

**Open shifts**:
The availability and schedules a client is currently hiring for. Matching them raises the priority score.
