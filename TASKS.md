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


#### T-P1-70: Extract `_is_process_alive()` to shared module
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: Deduplicate `_is_process_alive()` from port_registry.py, process_manager.py, subprocess_registry.py. Extract to `src/platform_utils.py` with proper `sys.platform` guard.
- **Acceptance Criteria**:
  1. Single implementation in `src/platform_utils.py`
  2. All 3 callsites import from shared module
  3. Existing tests pass unchanged


#### T-P1-73: Log retention/purge policy
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: Add retention/purge policy for execution_logs + review_history tables. Prevent unbounded DB growth.
- **Acceptance Criteria**:
  1. Configurable retention period (default 30 days)
  2. Purge runs on app startup or scheduled interval
  3. Test verifies old entries are cleaned


#### T-P2-75: Raw-response decoupling postmortem integration test
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Integration test asserting raw_response contains fields (model, usage, session_id) not present in summary/suggestions. Validates decoupled raw_response design.
- **Acceptance Criteria**:
  1. Test in `tests/test_review_pipeline.py` with mocked CLI
  2. Asserts raw_response dict keys are distinct from parsed review fields


#### T-P1-76: State machine transition race condition audit
- **Priority**: P1
- **Complexity**: M
- **Depends on**: None
- **Description**: Enumerate all race condition windows in status transitions: timeout vs completion, SSE vs DB, concurrent drag vs scheduler, review vs plan generation.
- **Acceptance Criteria**:
  1. Written audit doc in `docs/architecture/`
  2. Each race window has mitigation strategy (optimistic lock, epoch ID, etc.)
  3. Critical races have test coverage

#### T-P1-77: Scheduler finalization epoch ID
- **Priority**: P1
- **Complexity**: M
- **Depends on**: T-P1-76
- **Description**: Prevent race conditions where concurrent paths both try to finalize a task. Add execution epoch ID to scheduler (from T-P0-49).
- **Acceptance Criteria**:
  1. Epoch ID column on task model
  2. Finalization checks epoch match before state transition
  3. Test for concurrent finalization attempt

#### T-P2-78: SubprocessRunner design doc
- **Priority**: P2
- **Complexity**: S
- **Depends on**: T-P1-70
- **Description**: Design shared `SubprocessRunner` abstraction unifying subprocess management patterns across enrichment.py, review_pipeline.py, code_executor.py, process_manager.py.
- **Acceptance Criteria**:
  1. Design doc in `docs/architecture/subprocess-runner.md`
  2. Covers: process group isolation, timeout, readline streaming, persist-first, platform guards

#### T-P2-79: SubprocessRunner implementation + refactor
- **Priority**: P2
- **Complexity**: M
- **Depends on**: T-P2-78
- **Description**: Implement SubprocessRunner and refactor 4 callsites to use it.
- **Acceptance Criteria**:
  1. `src/subprocess_runner.py` with shared abstraction
  2. All 4 callsites refactored
  3. Existing tests pass unchanged


#### T-P2-80: State machine diagram documentation
- **Priority**: P2
- **Complexity**: S
- **Depends on**: T-P1-76
- **Description**: Document all valid states, triggers, and side-effects in review state machine.
- **Acceptance Criteria**:
  1. Diagram in `docs/architecture/state-machine.md`
  2. All transitions from ReviewLifecycleState enum covered

#### T-P2-81: PRD clarification (Pause/Gate/Launch semantics)
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Clarify Pause/Gate/Launch semantic boundaries in PRD. Does Pause affect review pipeline?
- **Acceptance Criteria**:
  1. Updated PRD section with clear definitions
  2. Edge cases documented

#### T-P2-82: UX audit + smoke test enforcement
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Audit completed UX tasks (T-P0-8a through T-P3-11) for scenario-matrix gaps. Add smoke test enforcement rule to CLAUDE.md (post-mortem from T-P0-57/T-P0-59).
- **Acceptance Criteria**:
  1. Audit results documented
  2. CLAUDE.md enforcement rule added
  3. Gap list for any missing coverage

#### T-P3-83: Done column ordering investigation
- **Priority**: P3
- **Complexity**: S
- **Depends on**: None
- **Description**: Investigate random ordering in Done column. Add sort/filter capability.
- **Acceptance Criteria**:
  1. Root cause identified (missing ORDER BY or frontend sort)
  2. Fix applied or task spec written for fix

#### T-P1-84: Persist plan_status to TASKS.md (bidirectional sync)
- **Priority**: P1
- **Complexity**: M
- **Depends on**: None
- **Description**: plan_status is DB-only; every sync resets it to "none". Add `- **Plan**: ready` marker to TASKS.md after plan generation succeeds, and teach the parser/sync to read it back. Three-way semantics: line absent = don't touch DB (DB wins), explicit `ready`/`failed` = overwrite DB, explicit `none` = reset DB. Writer uses backup+validate pattern.
- **Acceptance Criteria**:
  1. `ParsedTask` has `plan_status: str | None = None` (None = line absent, sentinel semantics)
  2. Parser recognizes `- **Plan**: <value>` with whitelist validation (`none`, `ready`, `failed`); invalid values -> None + warning
  3. `TasksWriter.update_task_plan_status(task_id, status)` inserts/updates Plan field in TASKS.md (with .bak backup)
  4. `upsert_task()` only updates DB plan_status when TASKS.md value is not None (absence = DB wins)
  5. API `generate_plan` writes `- **Plan**: ready` to TASKS.md after DB update; failure logged at WARNING (non-fatal, with explicit message "DB plan_status=ready but TASKS.md not updated")
  6. Round-trip test: generate plan -> TASKS.md shows `ready` -> sync -> DB plan_status still `ready`
  7. Absence test: DB=ready, TASKS.md line absent -> sync -> DB still `ready`
  8. All existing parser/writer tests still pass, ruff clean

#### T-P1-85: Replace Launch button with "Start All Planned Tasks"
- **Priority**: P1
- **Complexity**: M
- **Depends on**: T-P1-84
- **Description**: Replace dev server Launch/Stop button (LaunchControl.tsx) with a "Start N Planned" button that batch-moves all BACKLOG tasks with plan_status=ready into the pipeline. Respects review gate: gate ON -> REVIEW (triggers review pipeline), gate OFF -> QUEUED (scheduler picks up). Uses optimistic locking (expected_updated_at) per-task for concurrent safety.
- **Acceptance Criteria**:
  1. `POST /api/projects/{project_id}/start-all-planned` endpoint with `StartAllPlannedResponse` schema
  2. Endpoint queries BACKLOG tasks with plan_status=ready, reads `updated_at`, passes `expected_updated_at` to `update_status()`
  3. `OptimisticLockError` caught per-task and reported in `skipped_details`
  4. Review gate ON: tasks move to REVIEW + review pipeline enqueued; gate OFF: tasks move to QUEUED
  5. Frontend `StartAllPlanned.tsx` component shows "Start N Planned" (count from client-side task list), disabled when N=0, loading spinner during request
  6. `SwimLaneHeader.tsx` uses StartAllPlanned instead of LaunchControl (tasks prop passed from parent)
  7. `LaunchControl.tsx` deleted; backend launch/stop endpoints kept (no UI, API-only)
  8. Tests: gate ON -> REVIEW, gate OFF -> QUEUED, no planned tasks -> started=0, concurrent request -> skipped via optimistic lock
  9. Manual smoke test: click "Start N Planned" -> tasks move to correct column -> SSE updates UI in real-time

### P1-UX -- Polish

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
- T-P1-85 depends on T-P1-84

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

#### [x] T-P0-64: Real-time log streaming for review pipeline -- 2026-03-04
- Refactored _call_claude_cli() from communicate() to readline() loop with on_log callback. Added metadata_json column to execution_logs. Wired SSE + DB dual-write for review logs (source="review"). Review pipeline emits lifecycle messages. on_progress writes to execution_logs. Error handler preserves partial logs. 1031 tests passing.

#### [x] T-P0-65: Plan generation button discoverability + Kanban card visual feedback -- 2026-03-04
- Persistent "Plan" button on TaskCard face for tasks needing plans. Pulsing blue border animation during generation. "Planning" spinner badge replaces "No Plan" during generation. Double-click prevention + backend 409 guard. TypeScript clean, Vite build clean, 1031 tests passing.

#### [x] T-P0-66: Fix three critical plan generation bugs -- 2026-03-04
- Fixed hasNoPlan using description proxy instead of plan_status field (TaskCard, TaskCardPopover, KanbanBoard). Raised budget caps ($0.10->$1.00 enrichment, $0.50->$5.00 plan gen). Plan generation now visible in Running indicator and RunningJobsPanel (blue "Planning" theme). TypeScript clean, Vite build clean, 1031 tests passing.

#### [x] T-P0-67: Harden plan generation pipeline -- result-first persistence -- 2026-03-04
- Persist raw CLI output before parsing (write_raw_artifact, no truncation). plan_json column for structured data. Structural validation rejects empty plans. Atomic update_plan() method. Removed --permission-mode plan (conflicts with --json-schema). 1040 tests passing (9 new).

#### [x] T-P0-68: Investigate and design fix for tech debts -- 2026-03-04
- Investigated all 14 tech debt items, designed 5-phase remediation plan (type safety, operational reliability, race condition hardening, subprocess abstraction, documentation). Broke into 14 prioritized sub-tasks (T-P1-70 through T-P3-83) with dependencies, acceptance criteria, and complexity estimates.

#### [x] T-P1-71: Unified TaskEvent Pydantic model for SSE contract -- 2026-03-05
- Converted Event dataclass to TaskEvent Pydantic BaseModel in src/events.py. EventBus.emit() validates via Pydantic on construction. Backward-compatible Event alias. 10 new schema enforcement tests (22 total in test_events.py). All tests passing.

#### [x] T-P1-72: SSE origin field for log categorization -- 2026-03-05
- Added `origin` field (Literal: execution/review/scheduler/plan/api/system) to TaskEvent Pydantic model. Updated EventBus.emit() with keyword-only origin parameter. Updated all 37 emit() callers across api.py, scheduler.py, process_manager.py, process_monitor.py, git_ops.py. format_sse() includes origin in SSE payload. 7 new tests, 27 total in test_events.py. 1060 tests passing, ruff clean.

#### [x] T-P1-74: Plan generation error taxonomy + retry strategy -- 2026-03-05
- Added PlanGenerationErrorType enum (cli_unavailable, timeout, parse_failure, budget_exceeded, cli_error) with retryable/user_message properties. PlanGenerationError exception class replaces RuntimeError. API returns structured {error_type, retryable, detail} in 503 responses. SSE plan_status_change includes error_type/error_message/retryable on failure. Frontend shows actionable per-type messages. 16 new tests, 1076 total passing, ruff clean.

#### [x] T-P1-86: Fix stop hook JSON error + harden LLM JSON validation -- 2026-03-05
- Fixed run_hook() to emit {"ok": true/false} JSON stdout on all exit paths (stop hooks were producing empty stdout). Added Pydantic validation models (EnrichmentResult, PlanResult, ReviewResult) to all 4 LLM JSON parsers with raw content logging on failure. 23 new tests, 1099 total passing, ruff clean.

#### [x] T-P0-55: Execution log visual markers for review activity -- 2026-03-04
- Added purple "REVIEW" badge on review-originated log entries. Extended LogEntry with source field, SSE handlers pass source="review" for review_started/review_progress events. Uses SSE event type for origin detection.
