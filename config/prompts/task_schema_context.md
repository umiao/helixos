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
