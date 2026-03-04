# Task Backlog

> **Convention**: Pick tasks from top of Active (highest priority first).
> Move to In Progress when starting. Move to Completed when done.
> PRD reference: helixos_prd_v0.3.md (single source of truth for architecture)
>
> **Task Schema Template** (required fields for every new task):
> ```
> #### T-PX-NN: Title
> - **Priority**: P0 | P1 | P2 | P3
> - **Complexity**: S (< 1 session) | M (1-2 sessions) | L (3+ sessions)
> - **Depends on**: T-XX-NN | None
> - **Description**: What and why (2-4 sentences)
> - **Acceptance Criteria**:
>   1. Specific, verifiable outcome
>   2. At least one full user journey AC
>   3. Manual smoke test AC for UX tasks
> ```
>
> **Size invariant**: Active TASKS.md must stay under 300 lines. Completed tasks
> are archived to [archive/completed_tasks.md](archive/completed_tasks.md).

## In Progress
<!-- Only ONE task here at a time. Focus. -->

## Active Tasks

### P0 -- Must Have (core functionality)

### Tech Debt (tracked, not blocking current work)
- [ ] T-P0-28 postmortem: integration test asserting raw_response contains fields not present in summary/suggestions
- [ ] Log retention/purge policy for execution_logs + review_history tables
- [ ] Unified timeout policy for enrichment CLI subprocess calls (review covered by T-P0-31)
- [ ] Unify subprocess management into shared `SubprocessRunner` abstraction (T-P0-30/T-P0-31 tech debt)
- [ ] Review state machine diagram documentation
- [ ] (from web UI) Done column: investigate random ordering, add sort/filter.
      Self-editing workflow: test changes then restart (queued stage only?).
- [ ] Audit completed UX tasks (T-P0-8a through T-P3-11) for scenario-matrix gaps
- [ ] Clarify Pause/Gate/Launch semantic boundaries in PRD (does Pause affect review pipeline?)
- [ ] Plan generation 503 error taxonomy + retry strategy (structured error types for CLI unavailable, timeout, parse failure)
- [ ] Scheduler single finalization point / execution epoch ID (prevent race conditions where concurrent paths both try to finalize a task; from T-P0-49)
- [ ] State machine transition audit -- enumerate all race condition windows in status transitions (timeout vs completion, SSE vs DB, concurrent drag vs scheduler)
- [ ] SSE event payload structure: add explicit `origin` field (execution/review/scheduler) for clean log categorization (from T-P0-55)
- [ ] Deduplicate `_is_process_alive()` -- currently copy-pasted in port_registry.py, process_manager.py, subprocess_registry.py. Extract to shared module (e.g. `src/platform_utils.py`) and import everywhere. (from os.kill CTRL_C_EVENT bug)

### P1-UX -- Polish


#### T-P0-55: Execution log visual markers for review activity
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: Review progress entries in the execution log are visually
  indistinguishable from execution logs. Add a visual marker to review-originated
  log entries. Use existing SSE event type field if available to distinguish origin;
  if not, use pragmatic prefix detection for now and add SSE structure improvement
  to tech debt.
- **Acceptance Criteria**:
  1. Log entries from review progress SSE events have a distinct visual marker
     (e.g., colored "REVIEW" prefix badge or different background tint)
  2. Review log entries are distinguishable from execution log entries at a glance
  3. Implementation uses existing SSE event type/structure for origin detection
     (avoid fragile string parsing of message content)
  4. Manually verify: trigger a review for a task -> watch execution log -> review
     entries are visually distinct from execution entries

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 79 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P0-51: TASKS.md lifecycle model + archive separation -- 2026-03-04
- Archived 78 completed tasks to archive/completed_tasks.md. Relocated dependency graph to docs/architecture/dependency-graph-history.md. Added task schema template with required fields. TASKS.md reduced from 474 to 97 lines (under 300 invariant). 1000 tests passing.

#### [x] T-P0-54: Fix review panel header -- left-align task info, natural wrapping -- 2026-03-04
- Restructured ReviewPanel header: task info left-aligned in a bg-gray-50 identity strip, title wraps naturally (overflow-wrap: break-word, no truncate/max-w-48), task ID in mono/muted style, title text-sm, clear visual separation via border-t + background.
