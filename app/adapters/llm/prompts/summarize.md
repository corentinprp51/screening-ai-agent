Write a summary of a delivery-driver screening for a recruiter, in {{ language }}.

- At most 3 short lines: the key data, the points of attention (fields to review, flags), and the next action.
- Use only these facts, computed by code; do not add, guess or judge anything.
- Write only the summary, with no title.

## Facts

{% for key, value in facts.items() %}
- {{ key }}: {{ value }}
{% endfor %}
