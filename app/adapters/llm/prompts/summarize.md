Write a summary of a delivery-driver screening for a recruiter, in {{ language }}.

- 3 short lines, in this order:
  1. The key data: the name and the main field values.
  2. The points of attention: the fields to review and the flags, or that there are none.
  3. The next action.
- At most {{ max_chars }} characters in all.
- Use only these facts, computed by code; do not add, guess or judge anything.
- Write only the summary, with no title and no bullets.

## Facts

{% for key, value in facts.items() %}
- {{ key }}: {{ value }}
{% endfor %}
