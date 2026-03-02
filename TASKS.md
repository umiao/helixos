# Task Backlog

> **Convention**: Pick tasks from top of Active (highest priority first).
> Move to In Progress when starting. Move to Completed when done.
> PRD reference: helixos_prd_v0.3.md (single source of truth for architecture)

## In Progress
<!-- Only ONE task here at a time. Focus. -->

## Active Tasks

### P0 -- Must Have (core functionality)
<!-- All 13 P0 tasks completed. See Completed Tasks below. -->

### P1 -- Should Have (important features)
<!-- All 7 P1 tasks completed. See Completed Tasks below. -->

### P2 -- Nice to Have (polish, optimization)
<!-- All 8 P2 tasks completed. See Completed Tasks below. -->

### P3 -- Phase 3: UX + Polish

#### T-P3-6a: Persistent execution log + review history -- backend [M]
- **AC**: 2 new DB tables, DB-first writes, 2KB cap, 2 API endpoints
- **Depends on**: None

#### T-P3-6b: Persistent execution log + review history -- frontend [M]
- **AC**: Task-focused bottom panel, persistent history + live SSE merge, conversation-style reviews
- **Depends on**: T-P3-6a

#### T-P3-7: README overhaul [S]
- **AC**: Project-specific README with architecture diagram, features, modules
- **Depends on**: T-P3-6a, T-P3-6b

#### T-P3-8: Self-hosting guardrails -- design document [S]
- **AC**: Design doc covering worker isolation, commit serialization, log isolation
- **Depends on**: T-P3-6a

---

## Dependency Graph

```
T-P0-1 [S] Scaffold
  |
  +---> T-P0-2 [M] Models+DB+TaskManager
  |       |
  |       +---> T-P0-3 [S] Config ---> T-P0-4 [S] Parser ----+
  |       |                                                     |
  |       +---> T-P0-5 [M] Executor (also needs T-P0-11) -----+
  |       |       |                                             |
  |       |       +---> T-P0-6a [M] Scheduler core (also needs T-P0-4)
  |       |               |
  |       |               +---> T-P0-6b [M] Scheduler hardening
  |       |               |       |
  |       |               |       +---> T-P0-12 [S] Git auto-commit
  |       |               |
  |       |               +---> T-P0-9 [S] SSE endpoint
  |       |
  |       +---> T-P0-7 [M] Review pipeline
  |
  +---> T-P0-11 [S] Env loader
  |
  +---> T-P0-8a [S] Dashboard static
          |
          +---> T-P0-8b [M] Drag-drop+API (also needs T-P0-10)
          |
          +---> T-P0-8c [M] Log+Review+SSE (also needs T-P0-9)

T-P0-10 [L] API (needs T-P0-6b + T-P0-7 + T-P0-4)
T-P0-13 [M] Integration tests (needs T-P0-10 + T-P0-12)

--- P1 ---

T-P1-1 [M] Review pipeline refactor (no deps)
  |
  +---> T-P1-2 [S] API lifespan cleanup
  |
  +---> T-P1-3 [S] Remove API key deps
  |
  +---> T-P1-4 [M] Update tests

T-P1-5 [S] Fix config (no deps)
  |
  +---> T-P1-6 [M] QUICKSTART.md

T-P1-7 [S] E2E verification (needs T-P1-4 through T-P1-6)

--- P2 ---

T-P2-1 [S] Config extension (no deps)
  |
  +---> T-P2-2 [M] PortRegistry
  |       |
  |       +---> T-P2-3 [M] Validate/Import API ----------+
  |       |                                                |
  |       +---> T-P2-5 [M] ProcessManager [DONE] ----------+
  |                                                        |
  +---> T-P2-4 [M] TasksWriter [DONE] --------------------+
                                                           |
T-P2-6 [M] Frontend Swim Lanes [DONE] ------------------+
                                                           |
                                                    T-P2-7 [M] Frontend Operations UI [DONE]
                                                           |
                                                    T-P2-8 [S] E2E Integration
```

---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks
<!-- Move finished tasks here with [x] and completion date -->

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
