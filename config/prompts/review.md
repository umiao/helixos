{{reviewer_role}}

{{review_questions}}

## Task Schema (from TASKS.md conventions)

Task IDs follow the format T-P{priority}-{number} (e.g., T-P0-1, T-P1-42).
Do NOT assign IDs in your proposals -- IDs are allocated downstream.

Each task spec must include:
- **Priority**: P0 (must have) | P1 (should have) | P2 (nice to have) | P3 (stretch)
- **Complexity**: S (< 1 session) | M (1-2 sessions) | L (3+ sessions)
- **Depends on**: other task titles from your proposals, or existing task IDs, or None
- **Description**: What and why (2-4 sentences)
- **Acceptance Criteria**: Specific, verifiable outcomes
  - At least one full user journey AC per task
  - Every conditional AC must specify the inverse case

## Project Rules (from CLAUDE.md)

### Task Planning Rules
1. Scenario matrix: list ALL condition branches with expected outcomes.
2. Journey-first ACs: at least one AC per task must be a full user journey.
3. Cross-boundary integration: when spanning backend + frontend, at least one
   AC must verify end-to-end wiring.
4. "Other case" gate: every conditional AC must specify what happens when false.
5. Manual smoke test AC: every UX task needs a manual verification AC.
6. New-field consumer audit: when adding a model field, list all components
   that render related data and verify each uses the correct source of truth.

### Key Constraints
- All API keys and cookies from .env, never hardcoded.
- Every function must have type hints and docstring.
- No emoji characters anywhere in the project.
- Explicit UTF-8 for all file I/O and subprocess calls.
- Windows-compatible: no bash-only commands without PowerShell alternatives.
- Schema changes require migration (never assume users will delete their database).

### State Machine Rules
- Any workflow with status transitions must document all valid states,
  triggers for each transition, and side-effects attached to each transition.
- Side-effects on transitions are the backend's responsibility; the frontend
  only initiates the status change, never the side-effect directly.

### Smoke Test Enforcement
- UX tasks cannot be marked DONE without a manual smoke test description.
- Cross-component regression: verify changes work in ALL rendering contexts.

Evaluate the plan against these project rules. Flag violations in your suggestions.

Respond in JSON with this exact structure:
{"blocking_issues": [{"issue": "...", "severity": "high" or "medium"}], "suggestions": ["..."], "pass": true or false}

- Set "pass" to true if the plan is acceptable (possibly with minor suggestions). Set "pass" to false if there are blocking issues that must be resolved before implementation.
- "blocking_issues" lists problems that prevent approval. Each has an "issue" description and "severity" (high = must fix, medium = strongly recommended).
- "suggestions" lists optional improvements that do not block approval.
