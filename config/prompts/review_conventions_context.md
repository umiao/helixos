{{task_schema_context}}

{{project_rules_context}}

### State Machine Rules
- Any workflow with status transitions must document all valid states,
  triggers for each transition, and side-effects attached to each transition.
- Side-effects on transitions are the backend's responsibility; the frontend
  only initiates the status change, never the side-effect directly.

### Smoke Test Enforcement
- UX tasks cannot be marked DONE without a manual smoke test description.
- Cross-component regression: verify changes work in ALL rendering contexts.

Evaluate the plan against these project rules. Flag violations in your suggestions.
