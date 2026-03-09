# Completed Tasks Archive

> 21 completed tasks archived as of latest archival.

#### [x] T-P1-105: Split api.py into domain-specific route modules -- 2026-03-07
- Split src/api.py (2470 lines) into 5 route modules under src/routes/ (dashboard, execution, projects, reviews, tasks) + src/api_helpers.py for shared helpers. api.py retained lifespan, middleware, create_app(), router mounting (323 lines). All 1359 tests pass unmodified, ruff clean.

#### [x] T-P1-110: Add task filtering by priority and complexity -- 2026-03-07
- Added priority (P0/P1/P2/P3) and complexity (S/M/L) multi-select filter chips to filter bar. Priority extracted from local_task_id, complexity from description. AND-composed with existing status/project/search filters. Clear button resets both. 1359 pass, TS clean, Vite build clean.

#### [x] T-P1-112: Extract dependency_graph module from scheduler.py -- 2026-03-07
- Created src/dependency_graph.py with validate_dependency_graph(), detect_cycles(), extract_priority(). scheduler.py and task_manager.py import from new module. task_generator.py deduplicated cycle detection via shared detect_cycles(). 1359 pass, ruff clean.

#### [x] T-P0-111: Inject review suggestions into re-execution prompt -- 2026-03-07
- Added `build_review_feedback()` to scheduler.py (caps at last 3 reviews, includes suggestions + summary + human_reason). Scheduler fetches review history before execution and passes formatted feedback through `_run_with_retry` -> `executor.execute()` -> `_build_prompt()`. No new DB fields needed (uses existing `get_reviews()`). 10 new tests (7 build_review_feedback + 3 _build_prompt). 1359 pass, ruff clean.

#### [x] T-P0-107: Add React ErrorBoundary to crash-prone components -- 2026-03-07
- Created reusable ErrorBoundary.tsx (componentDidCatch, fallback UI with component name + error + retry button). Wraps entire bottom panel in App.tsx. KanbanBoard/header remain functional on crash. 1350 pass, TS clean, Vite build clean.

#### [x] T-P0-102: Project research and improvement decomposition -- 2026-03-07
- Researched codebase (99 tasks, 30+ modules, 25+ components). Identified top improvements: monolith splitting (api.py/App.tsx/scheduler.py), ErrorBoundary, review feedback loop, E2E tests, cost dashboard, priority filtering. Decomposed into T-P0-107 through T-P1-112 (8 tasks).

#### [x] T-P2-103: Tool block structured rendering -- 2026-03-07
- Refactored tool_use + tool_result into single bordered blocks with collapsed-by-default summary (tool name + input detail + line count). Single expand/collapse per block, no nested accordions. Orphaned tool_results render gracefully. 1350 pass, TS clean, Vite build clean.

#### [x] T-P2-102: Markdown + code syntax highlighting via Prism -- 2026-03-07
- Added rehype-prism-plus, remark-gfm, prism-themes to frontend. Wired remark-gfm + rehype-prism-plus into ReactMarkdown in ConversationView. Imported prism-one-dark CSS theme. Added 5KB size guard (strips language tag from large code blocks). 1350 pass, Vite build clean.

#### [x] T-P2-104: ExecutionLog filter UX improvement -- 2026-03-07
- Replaced level filter dropdown with multi-select toggle chips (ERROR, WARN, INFO) + "More" dropdown (DEBUG). Active chips show colored ring. Clear button resets filters. 1350 pass, TS clean, Vite build clean.

#### [x] T-P2-101: Typography + contrast improvements for log display -- 2026-03-07
- ConversationView body text bumped to 14px (`text-sm`), headings scaled up. ExecutionLog message text bumped to 13px. All badges normalized to 12px (`text-xs`). Contrast improved across both components. 1350 pass, ruff clean, TS clean.

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

#### [x] T-P0-55: Execution log visual markers for review activity -- 2026-03-04
- Added purple "REVIEW" badge on review-originated log entries. Extended LogEntry with source field, SSE handlers pass source="review" for review_started/review_progress events. Uses SSE event type for origin detection.

#### [x] T-P0-51: TASKS.md lifecycle model + archive separation -- 2026-03-04
- Archived 78 completed tasks to archive/completed_tasks.md. Relocated dependency graph to docs/architecture/dependency-graph-history.md. Added task schema template with required fields. TASKS.md reduced from 474 to 97 lines (under 300 invariant). 1000 tests passing.

#### [x] T-P0-50: Right-click context menu Edit (inline title/description editing) -- 2026-03-04
- Added "Edit" option to TaskContextMenu. EditTaskModal component with title/description editing, auto-focus, Escape/backdrop-click to close. Saves via PATCH /api/tasks/{id} using existing updateTask() API. Card updates immediately on save. Frontend builds clean, 1000 tests passing.

#### [x] T-P0-53: Active process pulsing badges on task cards -- 2026-03-04
- Centralized isActive check (status === "running" || review_status === "running") drives animate-pulse on TaskCard status badge. RUNNING and active review cards pulse consistently. Pulse stops on task exit. Frontend builds clean, 1000 tests passing.

#### [x] T-P0-52: Immediate next-task dispatch after task completion -- 2026-03-03
- Added immediate tick dispatch after task completion via asyncio.create_task(self.tick()) in _execute_task finally block. Added asyncio.Lock to tick() for re-entrancy safety. 4 regression tests (immediate <1s dispatch, slot-freed dispatch, concurrent completions no duplicate, exception releases lock). 1000 tests passing.

#### [x] T-P0-49: Fix inactivity timeout race condition -- kill vs. successful completion -- 2026-03-03
- Fixed race where inactivity timeout fires but process already exited 0. code_executor.py: after kill sequence, if returncode==0 override timeout flags to report success. scheduler.py: idempotent DONE guard (re-fetch before transition, skip if already DONE) + state guard before FAILED (verify still RUNNING). 4 regression tests. 996 tests passing.

#### [x] T-P0-48: Running Jobs Panel -- click top-right "Running" to see active job list -- 2026-03-03
- Created RunningJobsPanel component showing all running tasks with task ID, title, project name, elapsed timer, phase, and retry count. "Running: N" header indicator is now clickable to toggle the panel. Added "Running" as third bottom panel tab alongside Execution Log and Review. Panel auto-updates via SSE (no polling). Empty state shown when no jobs running. Entries removed in real-time when jobs complete. 992 tests passing, frontend builds clean.

#### [x] T-P0-47: No Plan badges + visual guidance in swim lanes -- 2026-03-03
- Added amber "No Plan" badge on TaskCard when `task.description` is empty/whitespace. Added planless task count indicator in BACKLOG/REVIEW column headers. Generate Plan button is now a prominent CTA for planless tasks (indigo-600 with shadow) and subtle for tasks with plans. Plan section auto-expands after successful generate-plan call. 992 tests passing, frontend builds clean.

#### [x] T-P0-46: Unified MarkdownRenderer abstraction layer -- 2026-03-03
- Created MarkdownRenderer.tsx using react-markdown with unified styling tokens (headings, lists, code blocks, tables, blockquotes). Font size toggle (S/M/L) with localStorage persistence. Applied to plan content (view mode), reviewer raw output, and new edit-preview mode in inline plan editor. Scroll container with max-height. 992 tests passing.

#### [x] T-P0-38: Backward-drag confirmation dialog redesign -- 2026-03-03
- Replaced browser `window.prompt()` with styled BackwardDragModal component. Displays task title, ID, source/target columns with arrow visualization, consequence text, and optional reason input. Amber color scheme, consistent with ReviewSubmitModal design. Enter/Escape keyboard support. Forward drags unaffected. 992 tests passing.

#### [x] T-P0-45: Generic default project selection via `is_primary` field -- 2026-03-03
- Added `is_primary: bool` (default False) to ProjectConfig, Project model, and API schemas. First-time load defaults to primary project(s); falls back to first project if none marked. Existing localStorage selections respected. Set helixos as `is_primary: true` in config. 14 new tests, 992 total passing.

#### [x] T-P0-44: Define plan validity model + enforce in review gate -- 2026-03-03
- Added `is_plan_valid()` function (>= 20 chars after stripping) and `PlanInvalidError` exception. `update_status()` enforces plan validity on BACKLOG->REVIEW when gate enabled (Layer 2). API returns 428 with `gate_action: "plan_invalid"`. Frontend opens ReviewSubmitModal for both `review_required` and `plan_invalid`. Modal shows plan validity warning, character counter, disables submit when plan too short. 20 new tests, 978 total passing.

#### [x] T-P0-43: Fix soft-delete sync with deleted_source tracking -- 2026-03-03
- Added `deleted_source` column to TaskRow (`"user"` | `"sync"` | NULL). `delete_task()` sets `deleted_source="user"`. `upsert_task()` skips user-deleted tasks (SKIPPED_DELETED) but allows resurrection for sync-deleted/legacy tasks. `sync_mark_removed()` marks tasks removed from TASKS.md as sync-deleted. `SyncResult`/`SyncResponse` gain `skipped` field. Schema auto-migrated. 13 new tests, 958 total passing.

#### [x] T-P0-40: Define Canonical ReviewLifecycleState enum in backend -- 2026-03-03
- Created ReviewLifecycleState(StrEnum) with 7 values (NOT_STARTED, RUNNING, PARTIAL, FAILED, REJECTED_SINGLE, REJECTED_CONSENSUS, APPROVED) and REVIEW_LIFECYCLE_TRANSITIONS state machine map. Added lifecycle_state column to ReviewHistoryRow and review_lifecycle_state column to TaskRow (auto-migrated). Exposed in API schemas (TaskResponse, ReviewHistoryEntry). Added set_review_lifecycle_state() to TaskManager. Updated HistoryWriter and frontend types. Full state machine diagram documented in code comments. 24 new tests, 930 total passing.

#### [x] T-P0-37: Fix sync crash on soft-deleted tasks + task creation feedback -- 2026-03-03
- Added UpsertResult StrEnum and upsert_task() to TaskManager: handles create/resurrect/update/unchanged without exceptions. Simplified sync_project_tasks() to single upsert_task() call per parsed task, removing existing_map query and create-or-update branches. Added sync_error field to CreateTaskResponse schema. Frontend: onCreated callbacks now pass synced boolean, App.tsx shows warning toast on sync failure. Added *.md.bak to .gitignore. 6 new tests (4 upsert + 2 sync resilience), 906 total passing.

#### [x] T-P0-36: Structured plan generation via Claude CLI -- 2026-03-03
- Feasibility: no `--plan` flag exists in Claude CLI. Implemented using stable features: `claude -p` + `--system-prompt` + `--json-schema` + `--add-dir` (codebase context) + `--permission-mode plan`. generate_task_plan() produces structured plan (summary, steps with files, acceptance criteria). format_plan_as_text() converts to readable markdown. POST /api/tasks/{id}/generate-plan auto-saves to task.description. Frontend: "Generate Plan" button in ReviewPanel. Graceful degradation: 503 when CLI unavailable, raw text fallback on parse failure. 18 new tests, 900 total passing.

#### [x] T-P0-35: Inline plan editing + versioned review history -- 2026-03-03
- Added plan_snapshot TEXT NULL column to ReviewHistoryRow (auto-migrated). Review pipeline stores immutable snapshot of task.description at pipeline start (first round only). PlanDiffView component with LCS-based unified line diff. ReviewPanel groups history entries by review_attempt with "Attempt N" headers + timestamps. Inline plan editor (Edit Plan -> textarea + Save/Cancel) using existing PATCH endpoint. Plan diff banner between attempt groups when plan changed. App.tsx onTaskUpdated refreshes state after inline edit. 9 new tests, 882 total passing.

#### [x] T-P0-34: Request Changes decision + human feedback loop -- 2026-03-03
- Added "request_changes" as third decision type (requires non-empty reason, 400 if empty). REVIEW_NEEDS_HUMAN -> REVIEW transition with review_status=idle. get_human_feedback() in HistoryWriter fetches all previous human feedback. Re-review injects feedback into reviewer prompts. Frontend: 3-button decision area (Approve/Request Changes/Reject), amber styling for Request Changes, Re-review button after request_changes, disabled buttons during running review. 13 new tests, 873 total passing.

#### [x] T-P0-33: Fix review panel data bugs (T-P0-28 regressions) -- 2026-03-03
- Fixed 3 data-path bugs: (1) raw_response now stores explicit CLI fields (model, usage, result, session_id) as structured JSON, decoupling DB from CLI contract. (2) Collapsible "Plan Under Review" section in ReviewPanel shows task.description (or explicit empty message). (3) human_reason column on ReviewHistoryRow, persisted E2E through write_review_decision->API->frontend with display below decision label. 6 new tests, 860 total passing.

#### [x] T-P0-32: Review + execution progress phase reporting via SSE -- 2026-03-03
- Extended on_progress to (completed, total, phase) with "Starting {focus} review...", "Completed {focus} review", "Synthesizing..." phase strings. API forwards phase in SSE review_progress events. CodeExecutor emits [PROGRESS] log entries every 60s (elapsed, line count, since last output) via background task. Frontend: ReviewPanel shows live phase label, ExecutionLog shows live M:SS elapsed counter. SSE task_id guard: reviewPhase only updates for selected task, cleared on task switch. 11 new tests, 854 total passing.

#### [x] T-P0-31: Apply timeout to review pipeline subprocess calls -- 2026-03-03
- Process group isolation in _call_claude_cli (start_new_session / CREATE_NEW_PROCESS_GROUP). Timeout via asyncio.wait_for on proc.communicate() (review_timeout_minutes, default 10, 0=disabled). On timeout: SIGTERM -> 5s grace -> SIGKILL -> RuntimeError -> review_status=failed + SSE alert + Retry. Retry semantics: review_attempt column on ReviewHistoryRow (auto-migrated, default 1), get_max_review_attempt() query, next attempt = max+1. Synthesis step covered by same timeout. 23 new tests, 843 total passing.

#### [x] T-P0-30: Subprocess inactivity timeout + process group cleanup for execution pipeline -- 2026-03-03
- Process group isolation: start_new_session=True (Unix) / CREATE_NEW_PROCESS_GROUP (Windows) matching ProcessManager pattern. On timeout/cancel, entire process group killed via os.killpg/CTRL_BREAK_EVENT with SIGKILL fallback. Inactivity detection: per-line asyncio.wait_for(readline(), timeout) replaces async-for iteration. No output for inactivity_timeout_minutes (default 20, 0=disabled) -> INACTIVITY_TIMEOUT error type, process group terminated. Config: inactivity_timeout_minutes on OrchestratorSettings. 13 new tests, 820 total passing.

#### [x] T-P0-29: Upgrade primary reviewer to Opus + per-reviewer budget config + cost tracking -- 2026-03-03
- Primary reviewer upgraded to claude-opus-4-6 with max_budget_usd:2.00. Adversarial stays claude-sonnet-4-5 at 0.50. Per-reviewer max_budget_usd config field (default 0.50, backward compatible). _extract_cost_usd() computes approximate cost from CLI usage data with model-specific pricing table. cost_usd nullable column on ReviewHistoryRow (auto-migrated), persisted in HistoryWriter, returned via API. Frontend shows ~$X.XX cost badge per review entry (hidden when NULL). Synthesis stays claude-sonnet-4-5. 15 new tests, 807 total passing.

#### [x] T-P0-28: Store full reviewer raw_response + surface in ReviewPanel -- 2026-03-03
- Added raw_response TEXT column to ReviewHistoryRow (auto-migrated, 200KB truncation limit). Capture raw CLI result text in review_pipeline.py, persist in HistoryWriter, return via API. Frontend: collapsible "Show Full Response (debug)" section in ReviewPanel with amber warning banner, collapsed by default, hidden for legacy/empty entries. 8 new tests, 792 total passing.

#### [x] T-P0-27: Add planning quality rules to CLAUDE.md + LESSONS.md postmortem -- 2026-03-03
- Added 6 actionable rules to CLAUDE.md: Task Planning Rules (5 rules: scenario matrix, journey-first ACs, cross-boundary integration, "other case" gate, manual smoke test AC) and State Machine Rules (1 rule: document states/triggers/side-effects, backend owns side-effects). Added LESSONS.md entry #12 with T-P0-24 root cause analysis (missing scenario matrix, no journey-first AC, cross-boundary gap, no manual smoke test).

#### [x] T-P0-26: Fix drag-to-REVIEW workflow -- transition-driven pipeline + review_status -- 2026-03-03
- Transition-driven review pipeline: status transition to REVIEW auto-enqueues pipeline (sets review_status=running). Pipeline success -> review_status=done + transition to REVIEW_AUTO_APPROVED/REVIEW_NEEDS_HUMAN. Pipeline failure -> review_status=failed + SSE alert. Backward transitions reset to idle. POST /api/tasks/{id}/review repurposed as retry-only (409 if running). Frontend: auto-focus ReviewPanel on drag-to-REVIEW, review_status-based rendering (idle/running/done/failed), retry button. 25 new tests, 784 total passing.

#### [x] T-P0-24: Review gate UX -- edit modal + preview before review submission -- 2026-03-03
- PATCH /api/tasks/{id} endpoint for title/description updates. Frontend 428 detection opens ReviewSubmitModal with edit fields + live preview. PATCH-if-changed then BACKLOG->REVIEW transition. "Send to Review" context menu for BACKLOG/QUEUED tasks. Auto-focus in ReviewPanel on submit. Gate OFF = direct transition, no modal. 15 new tests, 759 total passing.

#### [x] T-P0-23: Bidirectional state transitions + concurrency control -- 2026-03-03
- Bidirectional VALID_TRANSITIONS (backward drags: REVIEW->BACKLOG, QUEUED->BACKLOG/REVIEW, DONE->BACKLOG/QUEUED, FAILED->BACKLOG). RUNNING stays strict (DONE/FAILED only). Timestamp cleanup matrix clears completed_at/execution_state on backward moves. OptimisticLockError with updated_at comparison (Z/+00:00 normalized). StatusTransitionRequest gains reason + expected_updated_at. API returns 409 with conflict=true on lock mismatch. Frontend: KanbanBoard backward-drag prompt, App.tsx sends expected_updated_at, auto-refresh on conflict. 52 new tests, 744 total passing.

#### [x] T-P0-22: Soft-delete tasks via context menu + API -- 2026-03-02
- is_deleted column + auto-migration, TaskManager.delete_task() with RUNNING/dependents guards, DELETE endpoint (204/404/409 with dependents list), frontend deleteTask + context menu Delete with confirmation and force-delete flow. 22 new tests, 692 total passing.

#### [x] T-P0-21: Fix review gate bypass -- 5 vulnerable paths -- 2026-03-02
- Fixed all 5 bypass paths: sync auto-promotion, execute, retry, review/decide, status endpoint. ReviewGateBlockedError returns 428 (not 409). 15 new regression tests, 670 total passing.

#### [x] T-P0-1: Project scaffold (FastAPI + React + SQLite) -- 2026-03-01
- Scaffold complete: pyproject.toml, requirements.txt, frontend (Vite+React+TS+Tailwind v4), orchestrator_config.yaml, contracts/, scripts/start.ps1, src/executors/, src/sync/

#### [x] T-P0-11: Unified .env loader + env injection -- 2026-03-01
- EnvLoader class with per-project key filtering, validation, missing-file handling, ANTHROPIC_API_KEY warning. 15 tests passing.

#### [x] T-P0-2: Data model + TaskManager + database layer -- 2026-03-01
- Pydantic models (TaskStatus 9 values, ExecutorType, Project, Task, ReviewState, LLMReview, ExecutionState, Dependency). SQLAlchemy 2.0 async DB (TaskRow, DependencyRow, indexes). TaskManager CRUD + state machine + startup recovery. 82 tests passing.

#### [x] T-P0-3: Project registry + YAML config loader -- 2026-03-01
- Pydantic settings models (OrchestratorSettings, ProjectConfig, GitConfig, ReviewerConfig, DependencyConfig, OrchestratorConfig). YAML loader with validation. ProjectRegistry with get_project, list_projects, get_project_config. Path expansion via expanduser. 33 tests passing.

#### [x] T-P0-4: TASKS.md parser (one-way sync) -- 2026-03-01
- TasksParser with regex-based T-P\d+-\d+ extraction, section-to-status mapping, configurable status_sections. sync_project_tasks async upsert (BACKLOG->QUEUED, DONE force-update). ParsedTask/SyncResult dataclasses. Edge cases: no IDs, duplicates, empty sections. 43 tests passing.

#### [x] T-P0-5: CodeExecutor (subprocess + timeout + streaming) -- 2026-03-01
- ExecutorResult model + BaseExecutor ABC (execute + cancel). CodeExecutor spawns claude CLI via asyncio.create_subprocess_exec with stdout streaming, timeout (terminate->grace->kill), cancel support, and _build_prompt per PRD 7.2. Last 100 log lines kept. All decoding UTF-8. 26 tests passing.

#### [x] T-P0-6a: Scheduler core (EventBus + tick loop + concurrency) -- 2026-03-02
- EventBus pub/sub (Event dataclass, emit/subscribe, bounded queues max 1000, drop oldest). Scheduler with tick loop (5s interval), per-project + global concurrency control, dependency checking, executor factory (CodeExecutor MVP), task execution (success->DONE, failure->FAILED), start/stop lifecycle. 35 tests passing (12 events + 23 scheduler).

#### [x] T-P0-6b: Scheduler hardening (retry + recovery + cancel) -- 2026-03-02
- _run_with_retry with exponential backoff (30s, 60s, 120s), max retries -> BLOCKED. startup_recovery marks orphaned RUNNING tasks as FAILED with alerts. cancel_task calls executor.cancel() + asyncio task cancel, updates FAILED. _auto_commit_hook placeholder. 39 tests passing (16 new + 23 existing scheduler).

#### [x] T-P0-12: Git auto-commit with staged safety check -- 2026-03-01
- GitOps.auto_commit with git add -A, staged file count via numstat, safety check (max_files limit), unstage+alert on abort, configurable commit message template. check_repo_clean utility. Wired into Scheduler._auto_commit_hook with try/except guard. 8 tests passing.

#### [x] T-P0-7: Review pipeline (Anthropic-only, opt-in, async) -- 2026-03-01
- ReviewPipeline with review_task (required + optional adversarial for M/L), _call_reviewer (Anthropic Messages API), _build_review_prompt (focus-area prompts), _parse_review (JSON -> LLMReview with fallback), _synthesize (multi-review consensus), SynthesisResult model. Scoring: approve=1.0, reject=0.3, multi=synthesized. Configurable threshold, on_progress callback. 20 tests passing.

#### [x] T-P0-9: SSE event stream endpoint -- 2026-03-01
- format_sse (Event -> SSE data frame), sse_stream async generator (EventBus subscriber with keepalive on idle), sse_router (GET /api/events, StreamingResponse, text/event-stream). Disconnect cleanup via generator finally. Event JSON: {type, task_id, data, timestamp}. 21 tests passing.

#### [x] T-P0-8a: Dashboard Kanban -- static layout + TaskCard -- 2026-03-01
- TypeScript interfaces matching backend Pydantic models. API client stubs with mock data (5 tasks). KanbanBoard 5 columns (BACKLOG, REVIEW, QUEUED, RUNNING, DONE). TaskCard with project ID, task ID, title, status badge, dependency indicator. App layout with header (title, Sync All, running count), filter bar (project, status, search). npm run build succeeds.

#### [x] T-P0-10: API endpoints (CRUD + sync + execute + review + lifespan) -- 2026-03-01
- FastAPI app with lifespan (init DB, config, services, startup_recovery, scheduler start/stop). CORS for localhost:5173. Static mount for frontend/dist/. All 14 PRD Section 10 endpoints: project CRUD, task CRUD+filter, status transitions (state machine validated), review trigger (202 async), review decide, force-execute, retry, cancel, project sync, sync-all, dashboard summary, SSE events. Pydantic request/response schemas (src/schemas.py). Error responses with 404/409 codes. 32 tests passing.

#### [x] T-P0-8b: Dashboard Kanban -- drag-drop + API integration -- 2026-03-01
- Installed @dnd-kit/core. Real fetch calls replacing mock data. Drag-drop cards between columns with PATCH /api/tasks/{id}/status and optimistic update + rollback. Invalid transitions show error toast. Sync All calls POST /api/sync-all and refreshes. SkeletonCard loading states. Filter bar (project, status, search) functional. Toast notification system. npm run build succeeds.

#### [x] T-P0-8c: Dashboard -- ExecutionLog + ReviewPanel + SSE -- 2026-03-01
- useSSE hook (EventSource, auto-reconnect with exponential backoff 1s/2s/4s/max 30s, connected boolean). ExecutionLog (scrollable dark log, task filter, auto-scroll with scroll-lock, timestamps, max 500 lines). ReviewPanel (progress bar, consensus score, decision points, approve/reject buttons). SSE status_change auto-updates card positions, alert events as toasts, log events populate ExecutionLog. Connection indicator in header. Elapsed timer on running cards. Bottom panel with log/review tabs. npm run build succeeds.

#### [x] T-P0-13: Integration testing (end-to-end) -- 2026-03-01
- 19 integration tests across 5 modules. conftest with MockExecutor, MockAnthropicClient, temp git repo, config factory. test_sync_to_execute (sync->QUEUED->RUNNING->DONE->git commit). test_review_flow (approve/reject/human decide/multi-reviewer synthesis). test_failure_retry (retry backoff 30/60/120s, max retries->BLOCKED). test_concurrency (per-project + global limits, dependency blocking). test_startup_recovery (orphaned RUNNING->FAILED, alerts, error_summary). 335 total tests passing.

#### [x] T-P1-1: Review pipeline refactor -- Replace Anthropic SDK with `claude -p` -- 2026-03-01
- Replaced Anthropic SDK calls with `asyncio.create_subprocess_exec("claude", "-p", ...)` using `--system-prompt`, `--model`, `--output-format json`, `--json-schema`, `--no-session-persistence`, `--max-budget-usd 0.50`. Removed `anthropic_client` parameter from `__init__`. Added `_call_claude_cli()` method. Adapted all 20 unit tests and 4 integration tests to use subprocess mocking. Updated api.py lifespan. 335 tests passing.

#### [x] T-P1-2: API lifespan cleanup -- Remove Anthropic SDK init -- 2026-03-02
- Added `claude --version` check at startup. If Claude CLI is in PATH, logs version and creates ReviewPipeline. If not found, logs warning and sets review_pipeline to None. Removed ANTHROPIC_API_KEY from test fixtures. 335 tests passing.

#### [x] T-P1-3: Remove ANTHROPIC_API_KEY dependency from env/config -- 2026-03-02
- Removed ANTHROPIC_API_KEY warning from env_loader. Removed anthropic SDK from dependencies. Changed reviewer api default from "anthropic" to "claude_cli". Updated all test fixtures. 333 tests passing.

#### [x] T-P1-4: Update review pipeline tests for subprocess mocking -- 2026-03-02
- Verified T-P1-1 already replaced all MockAnthropicClient with subprocess mocking. No MockAnthropicClient references remain in any .py files. Fixed pre-existing SSE test timing race condition. 333 tests passing.

#### [x] T-P1-5: Fix orchestrator config for self-management -- 2026-03-02
- Fixed repo_path from ~/projects/helixos to ~/Desktop/Gen_AI_Proj/helixos. Added ~/.helixos/ directory auto-creation in API lifespan. 333 tests passing.

#### [x] T-P1-6: Create root-level QUICKSTART.md -- 2026-03-02
- Comprehensive guide with prerequisites, installation, configuration (orchestrator_config.yaml, adding projects), running (dev/production/Windows), TASKS.md format, all 14 API endpoints documented, autonomous mode, and troubleshooting section. 333 tests passing.

#### [x] T-P1-7: E2E startup verification -- 2026-03-02
- Full pipeline verified: server starts on port 8000, dashboard loads from static build, sync-all parses 20 tasks from TASKS.md, all 14 API endpoints respond correctly, SSE streams with text/event-stream, review pipeline initialized with Claude CLI 2.1.63, state machine enforces transitions. Verification checklist in docs/e2e_verification.md. 333 tests passing.

#### [x] T-P2-1: Extend ProjectConfig + OrchestratorSettings for P2 features -- 2026-03-02
- Added PortRange model, port_ranges dict and max_total_subprocesses to OrchestratorSettings. Added launch_command, project_type (Literal), preferred_port to ProjectConfig. All fields optional with defaults (backward compatible). 24 new tests, 359 total passing.

#### [x] T-P2-2: PortRegistry -- auto-assign ports, conflict detection, persistence -- 2026-03-02
- PortRegistry with assign_port (preferred_port + exclude_ports), release_port, get_assignment, update_pid, list_assignments, cleanup_orphans. Atomic persistence via tmp + os.replace to ports.json. 33 new tests, 392 total passing.

#### [x] T-P2-3: Project validation + import API + config writer (ruamel.yaml) -- 2026-03-02
- config_writer.py (ruamel.yaml comment-preserving read-modify-write, atomic write, suggest_next_project_id), project_validator.py (directory validation with limited-mode detection). POST /api/projects/validate and POST /api/projects/import endpoints. Auto-assign port, auto-sync, duplicate/invalid-path rejection. 29 new tests, 421 total passing.

#### [x] T-P2-4: TasksWriter -- create tasks by appending to TASKS.md (with filelock) -- 2026-03-02
- TasksWriter with filelock + threading.Lock for concurrent write safety. ID generation inside lock, .bak backup before every write, post-write validation. Handles empty file, no Active section, ID format variations. POST /api/projects/{id}/tasks endpoint with auto-sync. 28 new tests, 449 total passing.

#### [x] T-P2-5: ProcessManager + SubprocessRegistry -- launch/stop project processes -- 2026-03-03
- SubprocessRegistry (unified tracker, shared global limit, orphan cleanup). ProcessManager (launch with PORT injection, graceful stop with timeout, stop_all, cleanup_orphans). Windows compatible (CREATE_NEW_PROCESS_GROUP + CTRL_BREAK_EVENT). 3 API endpoints (launch, stop, process-status). Shutdown order enforced. 31 new tests, 480 total passing.

#### [x] T-P2-6: Frontend -- ProjectSelector + SwimLane + KanbanBoard refactor -- 2026-03-03
- ProjectSelector.tsx (multi-select checkbox dropdown with Select all/Clear, localStorage persistence). SwimLane.tsx (per-project wrapper with header bar + KanbanBoard, solo/multi-lane height modes). App.tsx refactored: swim lane layout, tasks grouped by project, each SwimLane has own DndContext (no cross-project drag), visible dividers between lanes, global status/search filters apply across all lanes. npm run build succeeds, 480 tests passing.

#### [x] T-P2-7: Frontend -- SwimLaneHeader + ImportModal + NewTaskModal + LaunchControl -- 2026-03-03
- SwimLaneHeader.tsx (per-project action bar with Launch/Stop, New Task, Sync buttons, limited-mode warning badges). LaunchControl.tsx (launch/stop toggle with port display, running indicator, uptime, 5s polling). ImportProjectModal.tsx (3-step: path input -> validate -> review/configure -> import with success feedback). NewTaskModal.tsx (title + description + priority form). "Import Project" button in header. All modals have loading states and error handling. New types in types.ts (ProcessStatus, ValidationResult, ImportResult, CreateTaskResult). New API calls in api.ts (syncProject, validateProject, importProject, createTask, launchProject, stopProject, getProcessStatus). npm run build succeeds, 480 tests passing.

#### [x] T-P2-8: E2E integration + SSE events for P2 features -- 2026-03-03
- Added per-project process_status to dashboard summary endpoint. Verified SSE events (process_start/process_stop), startup orphan cleanup (SubprocessRegistry + PortRegistry + ProcessManager), and shutdown order (ProcessManager -> Scheduler -> DB). 14 new integration tests covering import-to-swimlane, task creation, process lifecycle with SSE, orphan cleanup, shutdown order, and full E2E flow. 494 total tests passing.

#### [x] T-P3-1: Fix "No CLAUDE.md" false-positive badge -- 2026-03-03
- Added claude_md_path to ProjectResponse/ProjectDetailResponse schemas. ProjectRegistry auto-detects CLAUDE.md at repo_path when not explicitly configured. Import endpoint auto-sets claude_md_path in YAML config. SwimLaneHeader badge now shows descriptive tooltip. 6 new tests, 500 total passing.

#### [x] T-P3-2: Backend directory browser + frontend picker -- 2026-03-03
- GET /api/filesystem/browse with $HOME sandbox, hidden dir filtering, project indicator flags. DirectoryPicker component with breadcrumb navigation. Integrated into ImportProjectModal as toggleable browse mode. 11 new tests, 511 total passing.

#### [x] T-P3-3: Import Project in ProjectSelector dropdown -- 2026-03-03
- Added "Import Project" button with + icon at bottom of ProjectSelector dropdown. Closes dropdown and opens ImportProjectModal. Connected via onImportClick prop.

#### [x] T-P3-4: Task card hover popover with details -- 2026-03-03
- TaskCardPopover component rendered via React portal with full task details (description, dependencies, execution state, review state, timestamps). 300ms hover delay, auto-positioning (right/left/below), hides on drag. npm run build succeeds, 511 tests passing.

#### [x] T-P3-5: Workflow clarity -- inline task creation, context menu, tooltips -- 2026-03-03
- InlineTaskCreator in Backlog column (expand-on-click title input, Enter to create, Esc to cancel). TaskContextMenu with right-click context menu (view details, move-to-column, retry for failed). Tooltips on all buttons (header, swim lane, launch, panel tabs, project selector). npm run build succeeds, 511 tests passing.

#### [x] T-P3-6a: Persistent execution log + review history -- backend -- 2026-03-02
- 2 new DB tables (execution_logs, review_history) with indexes. HistoryWriter service with DB-first writes, 2KB text cap, batch support. Wired into Scheduler (execution start/success/failure/cancel logs) and ReviewPipeline (per-round review persistence). 2 new API endpoints (GET /api/tasks/{id}/logs, GET /api/tasks/{id}/reviews) with pagination, level filtering, and total count. 31 new tests, 542 total passing.

#### [x] T-P3-6b: Persistent execution log + review history -- frontend -- 2026-03-02
- Task-focused bottom panel: ExecutionLog fetches persistent DB logs + merges live SSE entries with level badges and source tags. ReviewPanel shows conversation-style review history with verdict badges, suggestions, consensus bars. Task focus indicator in tab bar with clear button. 4 new TS interfaces, 2 new API client functions. npm run build succeeds, 542 tests passing.

#### [x] T-P0-15: Surface detailed execution error diagnostics -- 2026-03-02
- ErrorType enum (INFRA, CLI_NOT_FOUND, REPO_NOT_FOUND, NON_ZERO_EXIT, TIMEOUT, UNKNOWN) on ExecutorResult. Pre-flight checks (repo_path exists, claude CLI on PATH). Stderr capture with 4KB truncation and ANSI stripping. Exception details in SSE alerts and execution logs. MAX_CONCURRENT_EXECUTIONS=2 hard limit. 27 new tests, 569 total passing.

#### [x] T-P0-16: Per-project execution pause/resume gate -- 2026-03-02
- DB-backed execution_paused on ProjectSettingsRow (persists across restarts). Scheduler pause_project/resume_project methods; paused = skip new executions, in-flight continue. API endpoints for pause/resume. SwimLaneHeader amber Pause/Resume toggle + PAUSED badge. SSE execution_paused events for real-time UI. 27 new tests, 596 total passing.

#### [x] T-P3-7: README overhaul -- 2026-03-02
- Project-specific README with architecture diagram, features, backend/frontend module tables, API reference, task state machine, tech stack, quick start, configuration reference, and project structure tree.

#### [x] T-P3-8: Self-hosting guardrails -- design document -- 2026-03-02
- Design doc at docs/design/self-hosting-guardrails.md covering: worker isolation via git worktree branches, commit serialization with pytest validation gate, log isolation with [SELF-HOST] tags, human-triggered-only restart (no auto-restart), safety boundary classification (safe: code/tests/docs; unsafe: DB schema/config/scheduler/hooks), state diagram for self-modification lifecycle, recursive execution prevention, and 5-phase implementation plan.

#### [x] T-P3-9: AI-assisted task enrichment via Claude CLI -- 2026-03-02
- POST /api/tasks/enrich endpoint (Claude CLI, JSON schema, 503 if unavailable). NewTaskModal "Enrich with AI" button pre-fills description + priority. InlineTaskCreator Tab key expands to NewTaskModal with auto-enrich. Reuses review_pipeline JSON extraction and code_executor pre-flight patterns. 19 new tests, 615 total passing.

#### [x] T-P3-10: Done column sorting and sub-status filtering -- 2026-03-02
- Sort dropdown in DONE column header (Newest first/Oldest first/By task ID). Sub-status filter badges (DONE/FAILED/BLOCKED) with counts and click-to-toggle filtering. Both preferences persist in localStorage. Client-side only, no backend changes. npm run build succeeds, 615 tests passing.

#### [x] T-P3-11: Enhanced review observation and human interaction UX -- 2026-03-02
- Review status badges: pulsing for active review, orange for needs-human, green for auto-approved. REVIEW_NEEDS_HUMAN triggers toast + auto-switch to Review tab + auto-select task. ReviewPanel reason text area wired to ReviewDecisionRequest.reason. REVIEW column header shows pulsing needs-human count badge. Client-side only, no backend changes. npm run build succeeds, 615 tests passing.

#### [x] T-P0-17: Design analysis -- evaluate achievements and future directions -- 2026-03-02
- Root cause analysis of three issues (missing review gate, asyncio Windows crash, fixed bottom panel). Design document at docs/design/review-gate-asyncio-divider.md. Added T-P0-18 (review gate), T-P0-19 (asyncio fix), T-P3-12 (resizable divider) to TASKS.md.

#### [x] T-P0-18: Configurable review gate before execution (two-layer defense) -- 2026-03-02
- Two-layer review gate. Layer 1: review_gate_enabled column in DB, blocks BACKLOG->QUEUED in TaskManager when enabled. Layer 2: Scheduler._can_execute() checks ReviewHistoryRow for approved verdict before execution. PATCH /api/projects/{id}/review-gate endpoint. SwimLaneHeader Gate ON/OFF toggle. SSE review_gate_changed events. 22 new tests, 641 total passing.

#### [x] T-P0-19: Fix asyncio NotImplementedError on Windows with --reload -- 2026-03-02
- Added --loop none to start.ps1 uvicorn command. Split error logging in api.py lifespan (NotImplementedError vs FileNotFoundError with distinct messages). Defense-in-depth comment on ProactorEventLoopPolicy. QUICKSTART.md updated with Windows dev instructions and troubleshooting. 4 tests, 619 total passing.

#### [x] T-P0-20: Fix --loop none breaks uvicorn CLI startup -- 2026-03-02
- uvicorn CLI rejects --loop none; replaced with scripts/run_server.py calling uvicorn.run(loop="none"). Rewrote tests with behavioral mocks + upstream guards. 8 tests, 645 total passing.
- Followup: fixed sys.path bug (uvicorn.run doesn't add CWD like CLI does), updated 8 stale uvicorn references across 4 docs, added --log-level arg, added doc regression guard test. 13 tests, 650 total passing.
- Followup-3: Fixed stale DB crash (_migrate_missing_columns in init_db), added real subprocess smoke test (test_server_startup.py), embedded verification best practices in CLAUDE.md/LESSONS.md/stop hook. 655 total passing.

#### [x] T-P3-12: Resizable bottom panel divider -- 2026-03-02
- ResizableDivider.tsx with setPointerCapture, grip dots, hover/drag highlight. Min 80px, max 60% viewport, double-click reset to 224px. localStorage persistence. Wired into App.tsx replacing fixed h-56. npm run build succeeds, 641 tests passing.

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

#### [x] T-P1-88: Timeout and limit relaxation -- 2026-03-05
- Relaxed defaults: session_timeout 60->720min, inactivity_timeout 20->0 (disabled), max_total_subprocesses 5->100. Made max_budget_usd optional (None=omit flag). Removed MAX_CONCURRENT_EXECUTIONS=2 hard cap from scheduler. Removed --max-budget-usd from enrichment CLI args. Updated orchestrator_config.yaml and 4 test files. 1099 tests passing.

#### [x] T-P0-55: Execution log visual markers for review activity -- 2026-03-04
- Added purple "REVIEW" badge on review-originated log entries. Extended LogEntry with source field, SSE handlers pass source="review" for review_started/review_progress events. Uses SSE event type for origin detection.
#### [x] T-P0-123: Automated PROGRESS.md archiving hook + pytest timeout fix -- 2026-03-09
- Created `.claude/hooks/archive_check.py` SessionStart hook with hysteresis-based archival (PROGRESS.md: >80 entries keep 40, TASKS.md: >20 completed keep 5). Added `pytest-timeout>=2.2.0` with 30s per-test timeout. Updated test_check.py to exclude integration/slow tests, use `--maxfail=1`, 300s hook timeout. Added `pytest_collection_modifyitems` to integration conftest. Marked 2 pre-existing hanging tests as slow. 14 new archive tests pass, 1517 total pass.

#### [x] T-P0-125: Review MD rendering + executor feedback verification + title inline edit -- 2026-03-09
- ReviewPanel.tsx: entry.summary and suggestions now render markdown via MarkdownRenderer with maxHeight="6rem" and "8rem" respectively. TaskCardPopover.tsx: title is now click-to-edit with hover pencil icon, Enter/blur saves, Escape cancels, max 200 chars. Verified scheduler.py correctly injects reviewer feedback (lines 694-714, log "Injecting previous review feedback into prompt"). Frontend builds successfully (no TS errors in changed files).

#### [x] T-P2-133: Remove unused generate-tasks-preview endpoint -- 2026-03-09
- Removed dead `POST /api/tasks/{task_id}/generate-tasks-preview` endpoint and its `GeneratedTaskPreview`/`GenerateTasksPreviewResponse` schemas. Never called from frontend.

#### [x] T-P2-132: Fix misleading enrichment prompt text about plan context -- 2026-03-09
- Removed misleading "This prompt receives plan context when available" from `enrichment_system.md`. Updated test assertion.

#### [x] T-P2-131: Move reviewer personas from Python to config templates -- 2026-03-09
- Extracted `_REVIEWER_PARAMS` into `config/reviewer_personas.yaml` with YAML loader, caching, and fallback. New persona = YAML entry only. 4 new tests.

#### [x] T-P1-130: Parallelize review pipeline reviewer calls -- 2026-03-09
- Replaced sequential loop with `asyncio.gather()` for multi-reviewer cases. Partial failure handled via `return_exceptions=True`. 3 new tests.

#### [x] T-P1-129: Remove dead synthesis code from review pipeline -- 2026-03-09
- Removed unused `SynthesisResult`, `_SYNTHESIS_JSON_SCHEMA`, `_synthesize()`, `_parse_synthesis()` (~90 lines). Deterministic merge is the actual path.

#### [x] T-P1-128: Add pass/fail calibration example to review prompt -- 2026-03-09
- Added passing/failing calibration examples and threshold guidance to `review.md`. 1 new test.

#### [x] T-P1-127: Add specific structural check items to review prompt -- 2026-03-09
- Updated `_REVIEWER_PARAMS` with structural checks (actionable steps, AC coverage, DAG deps, hidden assumptions). Removed generic OWASP checks. 3 new tests.

#### [x] T-P1-126: Rewrite plan_system.md with phased thinking and strict output contract -- 2026-03-09
- Rewrote plan prompt with 4-phase guidance + `{{complexity_hint}}` variable. Added `_strip_markdown_fences()` fallback. 15 new tests.

#### [x] T-P1-125: Align plan and review prompt rule coverage -- 2026-03-09
- Moved Anti-Patterns from `plan_system.md` into `_shared_rules.md` so both plan and review prompts get it via include. 1 new test.

#### [x] T-P1-124: Extract shared prompt rules into includable fragment -- 2026-03-09
- Added `{{include:filename}}` directive to `render_prompt()`. Extracted shared rules into `_shared_rules.md`, used by both plan and review prompts. 4 new tests.

#### [x] T-P1-123: Pass structured plan_json to reviewers instead of formatted text -- 2026-03-08
- Added `_format_plan_json_for_review()` helper formatting steps/ACs/tasks with indexed prefixes. Injected into reviewer content with graceful fallback. 17 new tests.

#### [x] T-P0-122: Fix replan review_attempt reset to 1 instead of incrementing -- 2026-03-08
- Fixed `_run_replan()` hardcoded `review_attempt=1` to query `get_max_review_attempt()` and increment. 3 new tests.

#### [x] T-P0-121: Fix complexity parameter not passed to review pipeline -- 2026-03-08
- Fixed `_run_review_bg()` always defaulting to "S". Added `complexity` field to Task/TaskRow with auto-migration. Inference from plan structure. 10 new tests.

#### [x] T-P1-116: Unified plan review before batch task decomposition -- 2026-03-08
- Implemented plan review panel: SSE `proposed_tasks[]`, reject-plan endpoint, PlanReviewPanel.tsx with confirm/reject, Plan tab with status badges. 10 new tests.

#### [x] T-P1-115: Upgrade agent prompts to production-grade (Phase 3: quality) -- 2026-03-08
- Upgraded all 5 prompts: plan few-shot + anti-patterns, review `{blocking_issues, suggestions, pass}` schema, deterministic merge, enrichment scope prohibition, execution scope constraint. 15 eval tests.

#### [x] T-P1-118: Harden task cancel with timeout enforcement and force-kill -- 2026-03-08
- Added `timeout_seconds=30` param to `cancel_task()` with graceful/forced paths. Cancel endpoint returns `{"graceful": bool}`. Both paths guarantee FAILED status. 2 new tests (graceful, force-kill timeout). 1123 pass, ruff clean.

#### [x] T-P1-117: Audit and fix SDK invocation settings -- 2026-03-08
- Added `setting_sources=[]` to enrichment QueryOptions. Added `execution_model` config field (default `claude-sonnet-4-5`) to `OrchestratorSettings` and `orchestrator_config.yaml`. Execution agent gains `model` from config and `system_prompt` from new `config/prompts/execution_system.md`. All 4 SDK callsites have code comments explaining setting_sources choice. 6 new tests. 1453 pass, ruff clean.

#### [x] T-P1-120: Consolidate prompt templates from 9 files to 4 -- 2026-03-08
- Consolidated `config/prompts/` from 9 files to 4: inlined fragments into `plan_system.md`, merged review files into parameterized `review.md`, renamed `execution_prompt.md` to `execution.md`. `_REVIEWER_PARAMS` config dict replaces 3 separate module-level prompt vars. `enrich_task_title()` gains conditional skip for non-empty descriptions. 6 files deleted. 11 new tests. 1447 pass, ruff clean.

#### [x] T-P1-119: Add reject-to-replan loop and enrich execution prompt with plan data -- 2026-03-08
- Added `replan` decision to review decide endpoint with max 2 attempts enforcement. `generate_task_plan()` gains `review_feedback` param for structured feedback injection. `Task` model gains `replan_attempt: int = 0` field with auto-migration. `_build_prompt()` injects structured `plan_json` (Implementation Steps + Acceptance Criteria) into execution prompt with graceful fallback. Background replan auto-enqueues review pipeline on success. 29 new tests. 1436 pass, ruff clean.
