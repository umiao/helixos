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

#### T-P0-57: Hover-to-generate-plan UX on TaskCard
- **Priority**: P0
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Add a "Generate Plan" button in TaskCardPopover for tasks that have no plan. Wire it to the existing `generatePlan()` API call. Provides discoverability for plan generation directly from the board view.
- **Acceptance Criteria**:
  1. TaskCardPopover shows "Generate Plan" button when task has no plan (`task.plan` is null/empty)
  2. Button is hidden when task already has a plan
  3. Clicking the button calls `generatePlan(taskId)` API
  4. Button shows loading state while API call is in flight, prevents double-click
  5. Manual smoke test: hover over a backlog task with no plan -> popover shows Generate Plan button -> click -> API fires

#### T-P0-58: Done tasks show green completion in ReviewPanel
- **Priority**: P0
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Done tasks should show a green completion badge in ReviewPanel. Define invariant: done+no-plan is valid (task completed without formal plan) -- show "Completed" not "No plan". Hide Generate Plan button for done tasks.
- **Acceptance Criteria**:
  1. ReviewPanel shows green "Completed" badge for tasks with status=done
  2. Done tasks with no plan show "Completed" badge, NOT "No plan" error state
  3. Generate Plan button is hidden for done tasks (regardless of plan status)
  4. Non-done tasks without a plan still show "No plan" state as before
  5. Manual smoke test: drag a task to Done column -> open ReviewPanel -> green completion badge visible, no Generate Plan button

#### T-P0-59: Plan generation progress feedback
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: Timing investigation (sub-task 1 below)
- **Description**: Add progress feedback during plan generation. Requires measuring actual latency first to decide sync vs async approach. Defines plan_failed semantics to prevent state gaps.
- **Sub-tasks**:
  1. Measure actual plan generation latency (5 runs, log p50/p95)
  2. If <30s: sync POST + loading spinner + disable re-click
  3. If >30s: async with SSE (requires tech debt "SSE event payload structure" unification first)
- **plan_failed semantics**: task.status stays unchanged (backlog/queued), task.plan_status set to "failed", UI shows retry option
- **Acceptance Criteria**:
  1. Sub-task 1 completed with documented latency measurements
  2. Appropriate feedback mechanism chosen based on latency data
  3. User sees progress indication during plan generation (spinner or SSE updates)
  4. Failed plan generation does NOT change task.status; sets plan_status="failed"
  5. UI shows retry option when plan_status="failed"
  6. User journey: click Generate Plan -> see progress feedback -> plan appears (or error with retry)
  7. Manual smoke test: trigger plan generation -> observe feedback -> verify task status unchanged on failure

#### T-P0-60: Process failure detection via hard timeout + exit code
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: None
- **Description**: Implement process failure detection using ONLY hard timeout expiry, non-zero exit code, and process-not-alive checks. NO activity-based stall detection (LLM silence is normal, false positives destroy trust). Surface timeout/crash events to UI via existing SSE channels.
- **Acceptance Criteria**:
  1. Hard timeout triggers process termination and error event
  2. Non-zero exit code detected and surfaced as failure
  3. Process-not-alive (crashed) detected and surfaced as failure
  4. NO activity-based stall detection (no "idle for X seconds" logic)
  5. Timeout/crash events sent to UI via existing SSE event types
  6. Health-check endpoint: GET /api/processes/status returns list of active subprocesses (PID, start_time, task_id)
  7. Manual smoke test: kill a subprocess -> UI shows failure notification within 10s

#### T-P0-61: Timeout normalization to 60min
- **Priority**: P0
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Normalize timeouts: review_timeout_minutes 10->60, add enrichment CLI timeout at 60min (currently none). Keep ProcessManager dev server timeout at 10s (different concern -- fast-fail for dev server startup).
- **Acceptance Criteria**:
  1. review_timeout_minutes changed from 10 to 60
  2. Enrichment CLI subprocess has 60min timeout (was unlimited)
  3. ProcessManager dev server timeout remains at 10s (unchanged)
  4. All timeout values use consistent units (minutes) in config
  5. Tests pass with updated timeout values

### P1-UX -- Polish

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
- T-P0-57, T-P0-58, T-P0-61: independent, can run in parallel
- T-P0-59: blocked until timing investigation (sub-task 1) done; if async path chosen, also blocked on tech debt "SSE event payload structure" unification
- T-P0-60: independent

---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 79 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P0-51: TASKS.md lifecycle model + archive separation -- 2026-03-04
- Archived 78 completed tasks to archive/completed_tasks.md. Relocated dependency graph to docs/architecture/dependency-graph-history.md. Added task schema template with required fields. TASKS.md reduced from 474 to 97 lines (under 300 invariant). 1000 tests passing.

#### [x] T-P0-54: Fix review panel header -- left-align task info, natural wrapping -- 2026-03-04
- Restructured ReviewPanel header: task info left-aligned in a bg-gray-50 identity strip, title wraps naturally (overflow-wrap: break-word, no truncate/max-w-48), task ID in mono/muted style, title text-sm, clear visual separation via border-t + background.

#### [x] T-P0-55: Execution log visual markers for review activity -- 2026-03-04
- Added purple "REVIEW" badge on review-originated log entries. Extended LogEntry with source field, SSE handlers pass source="review" for review_started/review_progress events. Uses SSE event type for origin detection.
