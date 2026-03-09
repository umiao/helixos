{{reviewer_role}}

{{review_questions}}

{{include:_shared_rules.md}}

Evaluate the plan against these project rules. Flag violations in your suggestions.

Respond in JSON with this exact structure:
{"blocking_issues": [{"issue": "...", "severity": "high" or "medium"}], "suggestions": ["..."], "pass": true or false}

- Set "pass" to true if the plan is acceptable (possibly with minor suggestions). Set "pass" to false if there are blocking issues that must be resolved before implementation.
- "blocking_issues" lists problems that prevent approval. Each has an "issue" description and "severity" (high = must fix, medium = strongly recommended).
- "suggestions" lists optional improvements that do not block approval.
