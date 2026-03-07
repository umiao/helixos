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







#### T-P2-75: Raw-response decoupling postmortem integration test
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Integration test asserting raw_response contains fields (model, usage, session_id) not present in summary/suggestions. Validates decoupled raw_response design.
- **Acceptance Criteria**:
  1. Test in `tests/test_review_pipeline.py` with mocked CLI
  2. Asserts raw_response dict keys are distinct from parsed review fields


#### T-P1-77: Scheduler finalization epoch ID
- **Priority**: P1
- **Complexity**: M
- **Depends on**: T-P1-76
- **Description**: Prevent race conditions where concurrent paths both try to finalize a task. Add execution epoch ID to scheduler (from T-P0-49).
- **Acceptance Criteria**:
  1. Epoch ID column on task model
  2. Finalization checks epoch match before state transition
  3. Test for concurrent finalization attempt


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
- T-P1-85 depends on T-P1-84
- T-P1-87 depends on T-P1-86
- T-P1-88 depends on T-P1-86 (complete)
- T-P2-91 depends on T-P1-89 (complete)


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 99 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

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
