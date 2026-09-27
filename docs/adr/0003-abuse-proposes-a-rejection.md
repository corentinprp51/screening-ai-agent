# Abuse proposes a rejection, it never closes the screening

A second abusive message in a screening (insults, insistent off-topic, attempts to steer the agent) sets the status to Rejection proposed, like a failed knock-out: the questions stop, the candidate is told a recruiter will reply, and a recruiter confirms the rejection or overrides it (an override resumes the screening and resets the abuse count). Abuse is read by the LLM, so closing on it alone would be an automated decision with a negative effect on the candidate, which the process design rules out (GDPR Art. 22, EU AI Act). Abuse stays a separate concept from a knock-out: a knock-out is a requirement of the position, abuse is behaviour in the screening, and the recruiter sees which one it is.

## Considered Options

- **Close as Withdrawn with an `abuse` flag.** No recruiter step, but Withdrawn means the candidate opted out, and the closure would still be automated.
- **A sixth outcome, "Closed by agent".** Explicit, but still an automated closure, and one more status for every screen and metric.

## Consequences

- Before consent, no data may be kept for a recruiter to review: a second abusive message there is treated as declined consent and the candidate is erased.
