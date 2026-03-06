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

#### T-P0-92: Fix schema/parsing in plan + review pipelines
- **Priority**: P0
- **Complexity**: S
- **Depends on**: None (T-P0-91 complete)
- **Description**: T-P0-91 CONFIRMED: when `--json-schema` is used, Claude CLI puts structured output in `structured_output` field (a JSON object), NOT `result` (which is null). All 5 callsites read `result` and get null. Fix: read `structured_output` when `--json-schema` is present. The value is already a parsed object, so skip `json.loads()`.
- **Findings from T-P0-91**:
  - `enrichment.py:224` -- reads `result`, should read `structured_output`
  - `enrichment.py:461` -- reads `result`, should read `structured_output`
  - `review_pipeline.py:629` -- reads `result`, should read `structured_output`
  - `review_pipeline.py:639` -- raw_response builder reads `result`
  - `review_pipeline.py:731` -- synthesis reads `result`, should read `structured_output`
  - Key difference: `structured_output` is already a JSON object (dict), not a JSON string. So `json.loads()` on it will fail. Parsers need to handle both dict and str.
- **Acceptance Criteria**:
  1. All 5 callsites read `structured_output` when `--json-schema` was used
  2. Parsers handle `structured_output` as dict (no json.loads needed)
  3. Existing tests updated to mock `structured_output` field instead of `result`
  4. Manual verification: run plan/review on a real task, check DB has meaningful content

#### T-P0-93: Harden stream-json event parser + add --verbose flag
- **Priority**: P0
- **Complexity**: S
- **Depends on**: None (T-P0-91 complete)
- **Description**: T-P0-91 found TWO issues: (1) `--verbose` flag is MISSING from code_executor.py CLI args -- without it, stream-json only emits the final result event (explains empty logs). (2) Parser coverage gaps: `_simplify_stream_event` handles 5 types but real CLI emits 6+.
- **Findings from T-P0-91**:
  - **CRITICAL**: `--verbose` is required for stream-json to emit intermediate events (system, assistant, stream_event). Without it, only final `result` appears. Our code_executor.py line 271-280 is missing `--verbose`.
  - Real event types: system, assistant, stream_event, result, rate_limit_event, user
  - `stream_event` wraps delta as `event.delta.type == "text_delta"`, not top-level `content_block_delta`
  - `system` with subtype `init` contains model/tools info (useful for log enrichment)
  - `result` event contains `structured_output` field when `--json-schema` used
- **Acceptance Criteria**:
  1. `--verbose` added to CLI args in code_executor.py
  2. `_simplify_stream_event` handles all 6 real event types
  3. `stream_event` delta nesting correctly parsed (`.event.delta.text`)
  4. `system` init events logged as `[INIT] model=X`
  5. Execution JSONL files contain complete stream events (verified with real CLI)
  6. ConversationView shows real content for execution tasks

#### T-P0-94: Enable stream-json for review pipeline + ConversationView
- **Priority**: P0
- **Complexity**: M
- **Depends on**: T-P0-92, T-P0-93
- **Description**: Switch review_pipeline from `--output-format json` to `stream-json`. Wire `on_stream_event` callback through review_task -> _call_claude_cli. Add JSONL persistence. Emit `execution_stream` SSE events during review. T-P0-91 CONFIRMED: stream-json + --json-schema are compatible. JSON schema result appears at end in `result` event's `structured_output` field. Natural language text streams normally via `stream_event` deltas.
- **Acceptance Criteria**:
  1. Review execution produces JSONL stream log files with real content
  2. SSE `execution_stream` events emitted during review
  3. ConversationView shows real-time review progress
  4. Review result still correctly parsed from stream output
  5. Manual smoke test: start review -> see live updates in ConversationView

#### T-P0-95: Enable stream-json for plan generation + ConversationView
- **Priority**: P0
- **Complexity**: M
- **Depends on**: T-P0-92, T-P0-93
- **Description**: Same as T-P0-94 but for enrichment.py plan generation. Switch to stream-json, wire callbacks, add JSONL persistence, emit SSE events.
- **Acceptance Criteria**:
  1. Plan generation produces JSONL stream log files with real content
  2. SSE `execution_stream` events emitted during plan generation
  3. ConversationView shows real-time plan generation progress
  4. Plan result still correctly parsed from stream output
  5. Manual smoke test: generate plan -> see live updates in ConversationView

#### T-P0-96: Fix log creation strategy -- lazy file creation + cleanup
- **Priority**: P0
- **Complexity**: S
- **Depends on**: T-P0-93
- **Description**: Log files should only be created on first event, not at process start. This prevents empty file accumulation. Also clean existing empty files under `data/logs/`. Add startup cleanup that removes 0-byte log files.
- **Acceptance Criteria**:
  1. JSONL/raw log files created on first write, not at process start
  2. No empty files left after a failed/aborted run
  3. Existing empty files cleaned from `data/logs/`
  4. Startup hook or init removes stale 0-byte files

#### T-P0-97: Add real-CLI integration test for stream pipeline
- **Priority**: P0
- **Complexity**: S
- **Depends on**: T-P0-94, T-P0-95
- **Description**: End-to-end test that runs actual Claude CLI (not mocked). Verifies JSONL files contain real stream events. Verifies API endpoint returns non-empty events. Marked as manual/nightly (not blocking CI -- may fail without API key/network).
- **Acceptance Criteria**:
  1. Integration test script that exercises plan, review, and execution
  2. Asserts JSONL files non-empty with valid JSON events
  3. Asserts stream-log API returns events
  4. Clearly marked as manual/nightly (skipped in normal pytest run)

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
- **Description**: Design shared `SubprocessRunner` abstraction unifying subprocess management patterns across enrichment.py, review_pipeline.py, code_executor.py, process_manager.py. (Reverted to queued -- was picked up by orchestrator during stream-json logging bug investigation.)
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
- T-P0-92 depends on None (T-P0-91 complete)
- T-P0-93 depends on None (T-P0-91 complete)
- T-P0-94 depends on T-P0-92, T-P0-93
- T-P0-95 depends on T-P0-92, T-P0-93
- T-P0-96 depends on T-P0-93
- T-P0-97 depends on T-P0-94, T-P0-95
- T-P1-85 depends on T-P1-84


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 99 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P0-91: Investigate CLI --json-schema output behavior -- 2026-03-06
- Confirmed root cause via official docs (code.claude.com/docs/en/headless): `--json-schema` puts output in `structured_output` field (object), NOT `result` (null). All 5 callsites in enrichment.py and review_pipeline.py read `result` and get null. Stream-json + --json-schema confirmed compatible. Documented 6 real stream-json event types vs 5 handled by parser. Added LESSONS #20 and #21.

#### [x] T-P0-90: Frontend Popover Enhancement -- 2026-03-06
- Enhanced TaskCardPopover with "Live Activity" section for running tasks: shows tool call count, elapsed minutes, and last activity (tool name or text snippet). Added StreamSummary type, computed via useMemo in App.tsx from streamEvents, threaded through SwimLane->KanbanBoard->TaskCard->TaskCardPopover. Non-running tasks unaffected. TypeScript clean, Vite build clean.

#### [x] T-P0-89: Frontend Conversation View -- 2026-03-06
- Created `ConversationView.tsx` with markdown assistant bubbles, collapsible color-coded tool badges, tool results matched by `tool_use_id`, result banner. Added `StreamEvent`/`StreamDisplayItem`/`StreamLogResponse` types, `fetchStreamLog` API. App.tsx handles `execution_stream` SSE (capped 2000/task), `viewMode` toggle between Conversation and Plain Log. TypeScript clean, Vite build clean.

#### [x] T-P0-87: Backend stream-json + Log Persistence -- 2026-03-06
- Switched `--output-format json` to `stream-json`. Added `_StreamJsonBuffer` for incremental JSON parsing, `on_stream_event` callback through executor chain, `_simplify_stream_event` for backward-compat `on_log`, JSONL persistence to `data/logs/{task_id}/`, `GET /api/tasks/{task_id}/stream-log` endpoint, `execution_stream` SSE event type. 26 new tests, 1124 total passing, ruff clean.
