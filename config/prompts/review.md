{{reviewer_role}}

{{review_questions}}

{{include:_shared_rules.md}}

Evaluate the plan against these project rules. Flag violations in your suggestions.

Respond in JSON with this exact structure:
{"blocking_issues": [{"issue": "...", "severity": "high" or "medium"}], "suggestions": ["..."], "pass": true or false}

- Set "pass" to true if the plan is acceptable (possibly with minor suggestions). Set "pass" to false if there are blocking issues that must be resolved before implementation.
- "blocking_issues" lists problems that prevent approval. Each has an "issue" description and "severity" (high = must fix, medium = strongly recommended).
- "suggestions" lists optional improvements that do not block approval.

## Calibration Examples

### Example: PASS (minor suggestions only)

```json
{
  "blocking_issues": [],
  "suggestions": [
    "Step 3 could batch the two DB queries into one for efficiency",
    "Consider adding a debug log in the error branch for observability"
  ],
  "pass": true
}
```

Rationale: The plan is structurally sound -- steps are actionable with specific files, ACs cover each step, and dependencies form a valid DAG. The suggestions are optional improvements that do not affect correctness.

### Example: FAIL (blocking issues found)

```json
{
  "blocking_issues": [
    {"issue": "Step 2 modifies src/models.py but no AC verifies the schema migration path -- existing databases will break", "severity": "high"},
    {"issue": "Steps 3 and 5 both depend on Step 4 but Step 4 depends on Step 5, creating a dependency cycle", "severity": "high"},
    {"issue": "No AC covers the case when the feature flag is OFF -- missing inverse case", "severity": "medium"}
  ],
  "suggestions": [
    "Step 1 could reuse the existing utility in src/utils.py instead of creating a new helper"
  ],
  "pass": false
}
```

Rationale: The plan has structural defects -- a missing migration path risks data loss (high), a dependency cycle makes the plan unimplementable (high), and a missing inverse case leaves behavior undefined (medium). These must be fixed before implementation can begin.

### Threshold guidance

- **PASS**: Plan is implementable as-is. Steps are specific, ACs are verifiable, dependencies are acyclic. Minor style or efficiency suggestions are fine.
- **FAIL**: Plan has at least one of: missing/untestable ACs, dependency cycles, unaddressed schema migrations, missing inverse cases for conditionals, steps that reference nonexistent files, or scope that clearly exceeds the task description.
