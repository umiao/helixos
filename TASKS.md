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







#### T-P0-99: Auto-sync frontend board after drag and task completion
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: None
- **Description**: Frontend board does not refresh after drag-drop or task
  completion. Backend should emit `board_sync` SSE event on every task state
  change; frontend listens and re-fetches board state. Drag-drop handler must
  call sync after successful API update. This is P0 because a kanban-driven
  agentic workflow with stale board state leads to wrong operator decisions.
- **Acceptance Criteria**:
  1. After dragging a task card to a new column, board reflects new state without manual refresh
  2. After a task completes (DONE/FAILED/BLOCKED), board updates within 2s
  3. After "Start All Planned" batch operation, board reflects all moved tasks
  4. Manual smoke test: drag task Backlog -> Queued -> card appears immediately

#### T-P0-100: Fix stop/cancel task signal propagation (20-min bar)
- **Priority**: P0
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Stop button shows "stopping" bar for up to 20 minutes.
  Investigate cancel signal propagation: frontend -> API -> SDK session.
  Ensure cancel terminates the SDK query and updates task state promptly.
- **Acceptance Criteria**:
  1. Clicking stop terminates execution within 10 seconds
  2. Task transitions to appropriate terminal state after stop
  3. Frontend "stopping" bar disappears after task stops
  4. Manual smoke test: click Stop -> bar gone within 10s -> task shows stopped

#### T-P0-101: Priority-based dependency-aware queue scheduling + cycle detection
- **Priority**: P0
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Scheduler should pick highest-priority task with no unmet
  dependencies. Also add dependency graph validation: detect cycles and
  missing task references. Without cycle detection, scheduler will deadlock.
- **Acceptance Criteria**:
  1. Given P0 and P2 tasks both QUEUED, scheduler picks P0 first
  2. Given P0 task with unmet dep and P1 with no deps, scheduler picks P1
  3. Circular dependency (A->B->A) detected and reported as error
  4. Reference to non-existent task ID in dependency detected and reported
  5. Test: queue 3 tasks with mixed priorities, verify dispatch order

### P1 -- Should Have (agentic intelligence)

#### T-P1-100: Enable plan mode + upgrade plan model to opus 4.6
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Plan agent uses standard query mode (sonnet 4.5). Switch to
  `permission_mode="plan"` (read-only: Read/Glob/Grep/LS) and upgrade model
  to `claude-opus-4-6` for deeper architectural analysis. Plan mode prevents
  accidental file edits during planning.
- **Acceptance Criteria**:
  1. `generate_task_plan()` passes `permission_mode="plan"` in QueryOptions
  2. Model changed from `claude-sonnet-4-5` to `claude-opus-4-6`
  3. Plan agent can Read/Glob/Grep but cannot Write/Edit
  4. Existing plan generation tests pass with updated settings

#### T-P1-101: Enrich plan prompt with project context + proposed_tasks schema
- **Priority**: P1
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P1-100
- **Description**: Plan prompt is generic. Inject: CLAUDE.md rules (relevant
  subset), TASKS.md schema template, existing task IDs for dependency refs.
  Extend plan JSON schema with `proposed_tasks[]` field containing title,
  description, suggested_priority, suggested_complexity, dependencies (by
  title or ID), and acceptance_criteria. Plan agent outputs PROPOSALS, not
  final TASKS.md entries -- ID allocation and validation happen downstream.
- **Acceptance Criteria**:
  1. Plan prompt includes CLAUDE.md content (or relevant subset) as context
  2. Plan prompt includes TASKS.md schema template and conventions
  3. JSON schema extended with `proposed_tasks[]` (title, description,
     suggested_priority, suggested_complexity, dependencies, ACs)
  4. Plan output contains structured task proposals (not final IDs)
  5. `max_tasks_per_plan = 8` enforced in schema validation

#### T-P1-102: Enrich review prompt with project conventions
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Review prompts are generic. Inject CLAUDE.md rules (task
  planning rules, state machine rules, smoke test enforcement) so reviewers
  evaluate plans against actual project standards. Also upgrade review model
  config to use opus 4.6 for both reviewers.
- **Acceptance Criteria**:
  1. Review system prompts include relevant CLAUDE.md sections
  2. Reviewers check plans against task planning rules
  3. Review model config updated to opus 4.6
  4. Existing review tests pass with updated prompts

#### T-P1-103: Selective hooks loading for plan/review agents
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Plan/review SDK sessions load no hooks. Add selective hook
  loading: plan agent gets context hooks only (session_context.py); review
  agent gets context hooks only. Execution agent keeps all hooks. Safety
  hooks like block_dangerous.py should NOT run during planning (they may
  interfere with read-only exploration).
- **Acceptance Criteria**:
  1. Plan agent session loads session_context.py hook
  2. Review agent session loads session_context.py hook
  3. Execution agent continues loading all hooks
  4. block_dangerous.py does NOT run during plan/review sessions

#### T-P1-104: Task Generator -- deterministic proposal-to-TASKS.md pipeline
- **Priority**: P1
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P1-101
- **Description**: After review approval, if plan contains `proposed_tasks[]`,
  a deterministic Task Generator (NOT LLM) processes proposals:
  1. Allocate next available T-PX-NN IDs
  2. Resolve dependencies (validate targets exist)
  3. Validate schema (all required fields)
  4. Enforce max_tasks_per_plan = 8
  5. Detect dependency cycles
  6. Generate diff for human approval
  7. On approval: write to TASKS.md + auto-pause pipeline
  Human-in-the-loop is mandatory. No auto-write without approval.
- **Acceptance Criteria**:
  1. Task Generator is pure Python (no LLM calls)
  2. IDs auto-allocated as next available number per priority level
  3. Invalid dependency references rejected with error message
  4. Circular dependencies rejected with error message
  5. > 8 proposed tasks rejected with error message
  6. Diff shown to user before TASKS.md write
  7. Pipeline auto-pauses after task insertion (configurable)
  8. Parent task status updated to reflect decomposition complete

### P2 -- Nice to Have

#### T-P2-100: Clean up plan log display (hide raw JSON artifacts)
- **Priority**: P2
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: `[plan_cli_output]` artifacts show as unreadable JSON in
  log view. Keep persistence for forensics but filter from plain log display.
- **Acceptance Criteria**:
  1. Plain log view does not show raw JSON artifact entries
  2. Artifacts still persisted in DB for forensic access
  3. Stream/conversation view unaffected

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
- T-P0-99 depends on None
- T-P0-100 depends on None
- T-P0-101 depends on None
- T-P1-100 depends on None
- T-P1-101 depends on T-P1-100
- T-P1-102 depends on None
- T-P1-103 depends on None
- T-P1-104 depends on T-P1-101
- T-P2-100 depends on None


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 99 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P3-83: Done column ordering fix -- 2026-03-06
- Root cause: `completed_at` only set for DONE status, not FAILED/BLOCKED. Fixed to set on all terminal transitions and clear on backward transitions. Frontend sort/filter already existed.

#### [x] T-P2-82: UX audit + smoke test enforcement -- 2026-03-06
- Audited 34 completed UX tasks against 5 Task Planning Rules. Key finding: 0/34 tasks had manual browser smoke tests documented. Added "Smoke Test Enforcement" section (3 rules) and planning rule #6 "New-field consumer audit" to CLAUDE.md. Full audit at docs/audits/ux-task-audit.md.

#### [x] T-P2-81: PRD clarification (Pause/Gate/Start All Planned semantics) -- 2026-03-06
- Added PRD section 5.4 defining Pause (scheduler dispatch only), Review Gate (two-layer review enforcement), and Start All Planned (batch operation). Includes per-control behavior tables and 7 edge case combinations. Answers: Pause does NOT affect review pipeline.

#### [x] T-P2-80: State machine diagram documentation -- 2026-03-06
- Created `docs/architecture/state-machine.md` documenting both TaskStatus (9 states, 22 transitions) and ReviewLifecycleState (7 states, 16 transitions) state machines. Includes ASCII diagrams, transition tables with triggers and side-effects, guards/gates, backward cleanup matrix, cross-machine interactions, and race condition references.

#### [x] T-P2-75: Raw-response decoupling postmortem integration test -- 2026-03-06
- Integration test `test_raw_response_decoupled_from_parsed_fields` validates 5 decoupling invariants: raw_response metadata keys disjoint from parsed fields, correct SDK metadata, correct parsed extraction, result mirroring, and no field leakage. 1248 pass, ruff clean.

#### [x] T-P1-85: Replace Launch button with "Start All Planned Tasks" -- 2026-03-06
- Added `POST /api/projects/{project_id}/start-all-planned` endpoint. Batch-moves BACKLOG tasks with plan_status=ready: gate ON -> REVIEW + pipeline, gate OFF -> QUEUED. Optimistic locking per-task. Created `StartAllPlanned.tsx`, replaced LaunchControl in SwimLaneHeader, deleted `LaunchControl.tsx`. 8 new tests. 1209 pass, ruff clean, TS clean.

#### [x] T-P1-77: Scheduler finalization epoch ID -- 2026-03-06
- Added `execution_epoch_id` to TaskRow/Task model. Scheduler generates UUID epoch on dispatch, verifies before DONE/FAILED finalization. Epoch cleared on backward transitions. 11 new tests. 1201 pass, ruff clean.

#### [x] T-P1-76: State machine transition race condition audit -- 2026-03-06
- Audit doc in `docs/architecture/race-condition-audit.md` covering 8 race windows with severity ratings and mitigations. Fixed `_cleanup_on_backward()` to reset `review_lifecycle_state`. 11 new tests. 1228 pass, ruff clean.

#### [x] T-P1-73: Log retention/purge policy -- 2026-03-06
- Added `log_retention_days` (default 30) to OrchestratorSettings. Added `purge_old_entries()` to HistoryWriter (deletes execution_logs + review_history older than retention). Wired into lifespan startup. 4 new tests. 1206 pass, ruff clean.

#### [x] T-P2-99: Expose review conversation_turns in API + ReviewPanel -- 2026-03-06
- Added `conversation_turns_json`/`conversation_summary_json` columns to ReviewHistoryRow (auto-migrated). Updated write/get in history_writer. Added to ReviewHistoryEntry schema (Python + TS). Extracted TOOL_COLORS to shared `streamUtils.ts`. Added collapsible conversation section to ReviewPanel. 4 new tests. 1175 pass, ruff clean.

#### [x] T-P1-98: Add claude-agent-sdk to dependency smoke test -- 2026-03-06
- Added `claude_agent_sdk`, `ruamel.yaml`, `filelock` to `test_core_dependencies_importable()`. Synced `pyproject.toml` dependencies with `requirements.txt` (added 3 missing packages). Documented dependency source-of-truth convention in CLAUDE.md. 1171 tests pass, ruff clean.

#### [x] T-P2-91: Conversation extraction mock tests with real fixtures -- 2026-03-06
- Created 3 JSON fixtures (review, execution, enrichment sessions) in `tests/fixtures/`. 37 tests verify `collect_turns()` produces non-empty turns/actions, `_extract_conversation_summary()` extracts findings/actions/conclusion, and `ClaudeResult.structured_output` is correctly populated. All deterministic. 1211 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-90: Remove dead subprocess boilerplate -- 2026-03-06
- Removed `_StreamJsonBuffer`, `_simplify_stream_event()`, `_terminate_process_group()`, `_kill_process_group()`, `_truncate_stderr()`, `MAX_STDERR_BYTES` from code_executor.py. Removed `SUBPROCESS_STREAM_LIMIT` from config.py. Removed 31 associated tests. Also committed T-P1-70 (extract `_is_process_alive()` to `platform_utils.py`). Net reduction: 512 lines. 1174 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-70: Extract _is_process_alive() to shared module -- 2026-03-06
- Deduplicated `_is_process_alive()` from port_registry.py, process_manager.py, subprocess_registry.py into `src/platform_utils.py` with proper `sys.platform` guard. All 3 callsites import from shared module. 1205 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-89: Migrate review_pipeline.py + conversation extraction -- 2026-03-06
- Replaced `_call_claude_cli()` subprocess with `_call_claude_sdk()` using `run_claude_query()` + producer-task + queue pattern. Added `conversation_turns` and `conversation_summary` fields to `LLMReview` model. Integrated `collect_turns()` for turn reconstruction and `_extract_conversation_summary()` for structured findings/actions/conclusion. Removed subprocess process-group helpers. Updated 98 review_pipeline tests + 4 integration tests + 1 subprocess_stream_limit test. Full suite: 1205 pass + 4 skipped, ruff clean.

#### [x] T-P1-88: Migrate code_executor.py to Agent SDK -- 2026-03-06
- Replaced `asyncio.create_subprocess_exec` in `CodeExecutor.execute()` with `run_claude_query()` from `sdk_adapter`. Uses producer-task + queue pattern with 30s heartbeat, manual time-based session/inactivity timeout (avoids nested asyncio.timeout/wait_for conflicts). Preflight check updated to verify SDK importability. Cancel support via producer task cancellation. Updated 76 tests (+ stream_json + subprocess_stream_limit) to mock `run_claude_query` instead of subprocess. 1208 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-87: Migrate enrichment.py to Agent SDK -- 2026-03-06
- Replaced both `asyncio.create_subprocess_exec` calls in `enrichment.py` with `run_claude_query()` from `sdk_adapter`. `enrich_task_title()` iterates SDK events for structured output. `generate_task_plan()` uses producer-task + queue pattern for heartbeat-safe streaming. `is_claude_cli_available()` updated to check SDK import. 94 tests updated, full suite 1218 pass + 4 skipped, ruff clean.

#### [x] T-P1-86: Claude Agent SDK adapter layer -- 2026-03-06
- Added `claude-agent-sdk>=0.1.40` to requirements.txt. Created `src/sdk_adapter.py` with Pydantic models (ClaudeEvent, AssistantTurn, ToolAction, ClaudeResult, QueryOptions), `run_claude_query()` async iterator wrapping SDK `query()`, and `collect_turns()` for conversation reconstruction. SDK flag spike documented inline (permission_mode, add_dirs, max_budget_usd). No JSONL logging in adapter. 33 new tests in `tests/test_sdk_adapter.py`. 1219 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-84: Persist plan_status to TASKS.md (bidirectional sync) -- 2026-03-06
- Added `plan_status: str | None` to ParsedTask, parser extracts `- **Plan**: <value>` with whitelist validation. TasksWriter.update_task_plan_status() inserts/updates Plan field with .bak backup. upsert_task() respects None=DB-wins semantics. API generate_plan writes Plan=ready to TASKS.md (non-fatal). 23 new tests. 1186 tests pass + 4 skipped, ruff clean.

#### [x] T-P0-97: Add real-CLI integration test for stream pipeline -- 2026-03-06
- Created `tests/integration/test_stream_cli.py` with 4 tests covering execution (CodeExecutor), plan generation (generate_task_plan), review (_call_claude_cli), and API endpoint (stream-log). Tests use `@pytest.mark.cli_integration` and are auto-skipped unless `-m cli_integration` is passed. Added `pytest_collection_modifyitems` hook in `tests/conftest.py` for auto-skip. Registered `cli_integration` marker in `pyproject.toml`. 1159 tests pass + 4 skipped, ruff clean.

#### [x] T-P0-96: Fix log creation strategy -- lazy file creation + cleanup -- 2026-03-06
- Added `_LazyFileWriter` class that defers file creation to first `write()` call, preventing empty log files. Replaced eager `open()` in code_executor.py, enrichment.py, review_pipeline.py. Added `cleanup_empty_log_files()` for startup cleanup of 0-byte files, wired into `lifespan()`. 11 new tests. 1159 tests pass, ruff clean.

#### [x] T-P0-95: Enable stream-json for plan generation + ConversationView -- 2026-03-06
- Switched `generate_task_plan` from `--output-format json` to `stream-json --verbose`. Added `_StreamJsonBuffer` parsing, JSONL persistence (`plan_stream_*.jsonl` + `plan_raw_*.log`), `on_stream_event` callback, partial buffer flush at EOF. Wired SSE `execution_stream` emission with `origin="plan"` in `api.py`. Result still correctly extracted from stream `result` event's `structured_output`. Added 5 new tests (CLI args, event callback, None safety, multi-event, JSONL persistence). 1148 tests pass, ruff clean. AC5 (manual smoke test) deferred to T-P0-97.

#### [x] T-P0-94: Enable stream-json for review pipeline + ConversationView -- 2026-03-06
- Switched `_call_claude_cli` from `--output-format json` to `stream-json --verbose`. Added `_StreamJsonBuffer` parsing, JSONL persistence (`review_stream_*.jsonl`), `on_stream_event` callback threaded through `review_task` -> `_call_reviewer` -> `_call_claude_cli` -> `_synthesize`. Wired SSE `execution_stream` emission in `api.py`. Result still correctly extracted from `result` event's `structured_output`. Added 5 new tests. 1143 tests pass, ruff clean. AC5 (manual smoke test) deferred to T-P0-97 (end-to-end verification).

#### [x] T-P0-93: Harden stream-json event parser + add --verbose flag -- 2026-03-06
- Replaced `--include-partial-messages` with `--verbose` in CLI args. Extended `_simplify_stream_event` to handle all 6 real event types: added `stream_event` (nested delta parsing), `system` init (`[INIT] model=X`), `user` (suppressed), `rate_limit_event` (suppressed). Added 8 new tests. 1138 tests pass, ruff clean. AC5/AC6 (real CLI verification) deferred to T-P0-97.

#### [x] T-P0-92: Fix schema/parsing in plan + review pipelines -- 2026-03-06
- Fixed all 5 callsites to read `structured_output` (dict) instead of `result` (null). Updated 4 parse functions to accept `str | dict` and skip `json.loads()` when already a dict. Updated all test helpers to simulate real `structured_output` field. 1131 tests pass, ruff clean.

#### [x] T-P0-91: Investigate CLI --json-schema output behavior -- 2026-03-06
- Confirmed root cause via official docs (code.claude.com/docs/en/headless): `--json-schema` puts output in `structured_output` field (object), NOT `result` (null). All 5 callsites in enrichment.py and review_pipeline.py read `result` and get null. Stream-json + --json-schema confirmed compatible. Documented 6 real stream-json event types vs 5 handled by parser. Added LESSONS #20 and #21.

#### [x] T-P0-90: Frontend Popover Enhancement -- 2026-03-06
- Enhanced TaskCardPopover with "Live Activity" section for running tasks: shows tool call count, elapsed minutes, and last activity (tool name or text snippet). Added StreamSummary type, computed via useMemo in App.tsx from streamEvents, threaded through SwimLane->KanbanBoard->TaskCard->TaskCardPopover. Non-running tasks unaffected. TypeScript clean, Vite build clean.

#### [x] T-P0-89: Frontend Conversation View -- 2026-03-06
- Created `ConversationView.tsx` with markdown assistant bubbles, collapsible color-coded tool badges, tool results matched by `tool_use_id`, result banner. Added `StreamEvent`/`StreamDisplayItem`/`StreamLogResponse` types, `fetchStreamLog` API. App.tsx handles `execution_stream` SSE (capped 2000/task), `viewMode` toggle between Conversation and Plain Log. TypeScript clean, Vite build clean.

#### [x] T-P0-87: Backend stream-json + Log Persistence -- 2026-03-06
- Switched `--output-format json` to `stream-json`. Added `_StreamJsonBuffer` for incremental JSON parsing, `on_stream_event` callback through executor chain, `_simplify_stream_event` for backward-compat `on_log`, JSONL persistence to `data/logs/{task_id}/`, `GET /api/tasks/{task_id}/stream-log` endpoint, `execution_stream` SSE event type. 26 new tests, 1124 total passing, ruff clean.
