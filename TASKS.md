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









### P1 -- Should Have (agentic intelligence)




### P2 -- Nice to Have

#### T-P2-101: Typography + contrast improvements for log display
- **Priority**: P2
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Log display (ConversationView + ExecutionLog) uses tiny fonts (10-12px)
  with poor contrast, making logs hard to read. Bump font sizes to readable defaults,
  improve text/background contrast ratios, and normalize badge sizes for consistency.
- **Acceptance Criteria**:
  1. ConversationView body text >= 14px, tool badge text >= 12px
  2. All text meets WCAG AA contrast ratio (4.5:1 for normal text, 3:1 for large text)
  3. ExecutionLog message text >= 13px with consistent line-height
  4. Badge/pill sizes normalized (no mixed 10px/12px within same view)
  5. User journey: open a task with execution logs -> text is comfortably readable without zooming
  6. [AUTO-VERIFIED] Build succeeds, TypeScript clean, visual grep confirms font-size values

#### T-P2-102: Markdown + code syntax highlighting via Prism
- **Priority**: P2
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P2-101
- **Description**: Add rehype-prism-plus and remark-gfm to the react-markdown pipeline in
  ConversationView for syntax highlighting of code blocks and JSON tool I/O. Include a
  performance guard: render blocks > 5KB as plain preformatted text to avoid highlighting lag.
- **Acceptance Criteria**:
  1. `rehype-prism-plus` and `remark-gfm` added to package.json and wired into react-markdown
  2. Fenced code blocks (```python, ```json, etc.) render with syntax highlighting
  3. Tool result content containing JSON is highlighted as JSON
  4. Code blocks > 5KB rendered as plain `<pre>` without Prism processing
  5. A Prism CSS theme is imported (e.g., prism-one-dark) matching the app's dark UI
  6. User journey: view a task conversation with code in assistant response -> code has colored syntax highlighting
  7. [AUTO-VERIFIED] Build succeeds, TypeScript clean, dependency installed, size-guard logic present in source

#### T-P2-103: Tool block structured rendering
- **Priority**: P2
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P2-101
- **Description**: Render tool_use + tool_result pairs as visually connected, bordered blocks
  in ConversationView. Each tool block shows a collapsed summary preview (e.g.,
  "Read src/foo.py (42 lines)") that expands to show full input/output. No double-collapse
  layers -- a single expand/collapse per tool invocation.
- **Acceptance Criteria**:
  1. tool_use and its matching tool_result rendered as a single bordered block (visual pairing)
  2. Collapsed state shows tool name + summary (e.g., "Read src/foo.py", "Bash: npm test (12 lines)")
  3. Expand/collapse toggle reveals full tool input and output content
  4. Only one level of collapse per tool block (no nested accordions)
  5. Unmatched tool_use or tool_result still renders gracefully (no crash, shows available data)
  6. User journey: view conversation with 5+ tool calls -> each tool is a compact summary line, click one -> full I/O expands inline
  7. [AUTO-VERIFIED] Build succeeds, TypeScript clean, tool pairing logic verified via grep

#### T-P2-104: ExecutionLog filter UX improvement
- **Priority**: P2
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Replace the level filter dropdown in ExecutionLog with a segmented control
  or toggle chips for the most common log levels (INFO, WARN, ERROR). Keep a "More" dropdown
  or overflow menu for less common levels to maintain extensibility.
- **Acceptance Criteria**:
  1. Common levels (INFO, WARN, ERROR) rendered as toggle chips or segmented control (not dropdown)
  2. Clicking a chip toggles that level on/off (multi-select, not single-select)
  3. Less common levels accessible via "More" dropdown or overflow
  4. Active filter state visually distinct (e.g., filled vs outlined chip)
  5. Filter state persists during the session (not reset on re-render)
  6. User journey: open ExecutionLog -> click ERROR chip -> only error entries shown -> click WARN chip -> both ERROR and WARN entries shown
  7. [AUTO-VERIFIED] Build succeeds, TypeScript clean, chip/segmented control component present in source

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
- T-P1-102 depends on None [DONE]
- T-P1-103 depends on None [DONE]
- T-P1-104 depends on T-P1-101 [DONE] [DONE]
- T-P2-100 depends on None [DONE]
- T-P2-101 depends on None
- T-P2-102 depends on T-P2-101
- T-P2-103 depends on T-P2-101
- T-P2-104 depends on None


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 99 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P2-100: Clean up plan log display (hide raw JSON artifacts) -- 2026-03-07
- Excluded `level='artifact'` entries from `get_logs()`/`count_logs()` by default. Added `include_artifacts` param to both methods + API endpoint. Artifacts still persisted in DB for forensic access. 5 new tests. 1312 pass, ruff clean.

#### [x] T-P1-104: Task Generator -- deterministic proposal-to-TASKS.md pipeline -- 2026-03-07
- Pure Python task generator: extracts `proposed_tasks[]` from plan_json, allocates sequential IDs per priority, validates schema/dependencies, detects cycles (DFS), enforces max 8 tasks, generates human-readable diff. Two API endpoints: preview (GET diff) and confirm (write + auto-pause). Added `DECOMPOSED` plan status. 43 new tests. 1308 pass, ruff clean.

#### [x] T-P1-103: Selective hooks loading for plan/review agents -- 2026-03-07
- Added `setting_sources` to QueryOptions/ClaudeAgentOptions. Plan and review agents use `setting_sources=[]` to disable CLI hooks. Created `session_context_loader.py` to inject session context into system prompts. Execution agent unchanged. 16 new tests. 1292 pass, ruff clean.

#### [x] T-P1-102: Enrich review prompt with project conventions -- 2026-03-06
- Injected CLAUDE.md rules (task planning, state machine, smoke test, key constraints) + TASKS.md schema conventions into all review prompts. Imported shared context from enrichment.py. Upgraded adversarial reviewer + synthesis model to opus 4.6. 4 new tests. 1244 pass, ruff clean.

#### [x] T-P1-101: Enrich plan prompt with project context + proposed_tasks schema -- 2026-03-06
- Enriched plan prompt with CLAUDE.md rules + TASKS.md schema conventions. Extended JSON schema with `proposed_tasks[]` (title, desc, priority, complexity, deps, ACs). Added `ProposedTask` model, `MAX_TASKS_PER_PLAN=8` validation. 13 new tests. 1273 pass, ruff clean.

#### [x] T-P1-100: Enable plan mode + upgrade plan model to opus 4.6 -- 2026-03-06
- Changed `generate_task_plan()` to use `model="claude-opus-4-6"` (was `claude-sonnet-4-5`) and `permission_mode="plan"` (read-only). Updated test assertion. 1260 pass, ruff clean.

#### [x] T-P0-99: Auto-sync frontend board after drag and task completion -- 2026-03-06
- Added `board_sync` SSE event on all task state changes (api, scheduler). Frontend debounced (500ms) full refetch on board_sync events. StartAllPlanned gets success toast via onStarted callback. 3 new tests. 1259 pass, ruff clean, TS clean, Vite build clean.

#### [x] T-P0-100: Fix stop/cancel task signal propagation -- 2026-03-06
- Root cause: no frontend cancel mechanism + no backend auto-cancel on RUNNING status change. Added `cancelTask()` API, "Stop Execution" context menu button, backend auto-cancel in `update_task_status()`. 4 new tests. 1257 pass, ruff clean, TS clean.

#### [x] T-P0-101: Priority-based dependency-aware queue scheduling + cycle detection -- 2026-03-06
- Added `extract_priority()` helper and priority-sorted `get_ready_tasks()`. Added `validate_dependency_graph()` with DFS cycle detection and missing-ref validation. Enhanced `_deps_fulfilled()` with missing-ref alerts. Fixed tick over-fetch. 16 new tests, 79 scheduler tests pass, ruff clean.

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
