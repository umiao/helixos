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


#### T-P0-64: Real-time log streaming for review pipeline
- **Priority**: P0
- **Complexity**: S (< 1 session)
- **Depends on**: T-P0-63a (uses same `on_log: Callable[[str], None]` callback interface for SSE + DB dual-write)
- **Description**: Review pipeline currently emits SSE `review_progress` events but does NOT persist logs to `execution_logs` table. User cannot see review activity in the ExecutionLog panel after the fact. Also, individual reviewer Claude CLI calls (`_call_reviewer`) buffer all output via `proc.communicate()` -- no streaming during the review subprocess.
  Fix: Apply the same streaming pattern from T-P0-63a to review subprocess calls. Write review progress to execution_logs with `source="review"`. Add `metadata_json` column for structured reviewer context. SSE events must follow the contract from 63a: `{type, task_id, data: {message?, source?}, timestamp}`.
- **Acceptance Criteria**:
  1. `execution_logs` table gets optional `metadata_json` column (Text, nullable) for structured context like `{"reviewer_focus": "feasibility", "reviewer_model": "claude-sonnet-4-5"}` -- avoids schema explosion; source="review" + metadata_json covers all reviewer-specific needs
  2. `_call_claude_cli()` in review_pipeline.py refactored from `proc.communicate()` to `proc.stdout.readline()` loop, accepting `on_log` callback (same interface as 63a's `generate_task_plan`)
  3. `review_pipeline.py` passes `on_log` to `_call_reviewer`, which emits SSE + DB dual-write per line via `event_bus.emit("log", ...) + history_writer.write_log(...)`
  4. Review pipeline `on_progress` callbacks write to `execution_logs` via `history_writer` (source="review")
  5. ExecutionLog component shows review logs interleaved with execution logs for the same task, with "REVIEW" source badge
  6. **User journey**: User triggers review -> ExecutionLog shows "Review started" -> shows Claude CLI output from each reviewer in real-time -> shows "Review completed: approved/rejected"
  7. **Inverse case**: If a reviewer subprocess fails mid-stream, partial review logs are preserved in DB, review status transitions to failed, error appears in ExecutionLog
  8. **Manual smoke test**: Trigger review on a task with plan, watch ExecutionLog -- reviewer output lines must appear within 5 seconds, not only after all reviewers complete

#### T-P0-65: Plan generation button discoverability + Kanban card visual feedback
- **Priority**: P0
- **Complexity**: S (< 1 session)
- **Depends on**: T-P0-63b (needs SSE plan_status events wired in frontend)
- **Description**: The "Generate Plan" button in TaskCardPopover is nearly invisible (requires precise 300ms hover on small card). The Kanban card itself shows no visual indicator during plan generation. Fix: (A) Add persistent "Generate Plan" button directly on TaskCard face (not just in popover) for tasks with `plan_status=none/failed`. (B) Add pulsing/animated border on TaskCard when `plan_status=generating` (like the "running" animation pattern). (C) Backend 409 guard from 63a prevents concurrent generation -- frontend disables button but does NOT solely rely on client-side state for protection.
- **Acceptance Criteria**:
  1. TaskCard shows a small "Plan" action button on the card face for tasks needing plans (plan_status=none or failed), without requiring hover
  2. TaskCard shows pulsing/glowing border animation when plan_status=generating (similar to running task animation)
  3. Frontend double-click prevention (disable button on click) + backend 409 guard from T-P0-63a (defense in depth)
  4. **Inverse case**: Cards with plan_status=ready show no plan button (already has plan). Cards in done/failed/blocked show no plan button.
  5. **User journey**: User sees card in Queued column with visible "Plan" chip -> clicks it -> card starts pulsing -> ExecutionLog shows progress -> pulsing stops when plan ready -> "Plan" chip disappears
  6. **Manual smoke test**: Open Kanban, find task without plan -- "Plan" button must be visible WITHOUT hovering. Click it, card must visually pulse within 1 second.

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
- [ ] Unified TaskEvent model: formalize the SSE event contract `{type, task_id, data: {...}, timestamp}` into a shared Pydantic model. Currently defined as a convention in T-P0-63a; should become enforced schema after 63a/64 are complete.
- [ ] Deduplicate `_is_process_alive()` -- currently copy-pasted in port_registry.py, process_manager.py, subprocess_registry.py. Extract to shared module (e.g. `src/platform_utils.py`) and import everywhere. (from os.kill CTRL_C_EVENT bug)
- [ ] Post-mortem: T-P0-57/T-P0-59 were marked DONE without manual smoke test. Lesson: any task touching subprocess + UX must have a real invocation test (not just mocked unit tests). Add to CLAUDE.md as enforcement rule.



### P1-UX -- Polish


## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
- T-P0-63b -> T-P0-63a (frontend wiring depends on backend streaming)
- T-P0-64 -> T-P0-63a (review streaming uses same on_log interface)
- T-P0-65 -> T-P0-63b (card visual feedback depends on frontend SSE wiring)

---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 79 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P0-51: TASKS.md lifecycle model + archive separation -- 2026-03-04
- Archived 78 completed tasks to archive/completed_tasks.md. Relocated dependency graph to docs/architecture/dependency-graph-history.md. Added task schema template with required fields. TASKS.md reduced from 474 to 97 lines (under 300 invariant). 1000 tests passing.

#### [x] T-P0-54: Fix review panel header -- left-align task info, natural wrapping -- 2026-03-04
- Restructured ReviewPanel header: task info left-aligned in a bg-gray-50 identity strip, title wraps naturally (overflow-wrap: break-word, no truncate/max-w-48), task ID in mono/muted style, title text-sm, clear visual separation via border-t + background.

#### [x] T-P0-61: Timeout normalization to 60min -- 2026-03-04
- review_timeout_minutes default 10->60, enrichment_timeout_minutes added (default 60), enrichment CLI subprocess calls use asyncio.wait_for with configurable timeout. ProcessManager dev server timeout unchanged at 10s. 1006 tests passing.

#### [x] T-P0-58: Done tasks show green completion in ReviewPanel -- 2026-03-04
- Green "completed" badge in ReviewPanel header for done tasks. Done+no-plan shows "Task completed" instead of "No plan" error. Edit/Generate Plan buttons hidden for done tasks. Non-done tasks unaffected.

#### [x] T-P0-57: Hover-to-generate-plan UX on TaskCard -- 2026-03-04
- Added "Generate Plan" button to TaskCardPopover for tasks with no plan (hidden when plan exists or task is done/failed/blocked). Button calls generatePlan API with loading state and double-click prevention. Error display on failure. onTaskUpdated callback threaded through SwimLane -> KanbanBoard -> TaskCard -> TaskCardPopover for immediate UI refresh.

#### [x] T-P0-59: Plan generation progress feedback -- 2026-03-04
- Added plan_status field (none/generating/failed/ready) to Task model. API sets generating->ready/failed lifecycle. Frontend shows animated spinner + retry button. Sync POST approach chosen based on architecture analysis. 1024 tests passing.

#### [x] T-P0-60: Process failure detection via hard timeout + exit code -- 2026-03-04
- ProcessMonitor background task scans SubprocessRegistry every 5s, detects dead PIDs, emits `process_failed` SSE events. Health-check endpoint `GET /api/processes/status`. Frontend toast+log on crash. No activity-based stall detection. 1021 tests passing.

#### [x] T-P0-63a: Backend plan generation streaming + SSE events -- 2026-03-04
- Refactored generate_task_plan() to readline() loop with on_log callback. POST returns 202 with background task. SSE plan_status_change + log events with source="plan". Per-line DB writes. 409 idempotency guard, startup zombie cleanup, 30s heartbeat. 1028 tests passing.

#### [x] T-P0-63b: Frontend plan generation UX wiring -- 2026-03-04
- Wired SSE plan_status_change events in App.tsx for real-time plan_status updates. Added "PLAN" badge in ExecutionLog for source="plan" logs. Added elapsed timer in ReviewPanel during generation. Updated API client and components for 202 async flow. TypeScript clean, Vite build clean, 1028 tests passing.

#### [x] T-P0-55: Execution log visual markers for review activity -- 2026-03-04
- Added purple "REVIEW" badge on review-originated log entries. Extended LogEntry with source field, SSE handlers pass source="review" for review_started/review_progress events. Uses SSE event type for origin detection.
