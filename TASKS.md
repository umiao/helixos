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

#### T-P1-6: Create root-level QUICKSTART.md [M]
- **Files**: `QUICKSTART.md` (new, root level)
- **What**: Comprehensive guide covering: prerequisites (Python 3.11+, Node.js 18+, Claude Code CLI), installation, configuration (orchestrator_config.yaml, adding projects), running (uvicorn, Vite dev, production, autonomous), API reference table (14 endpoints), debugging and troubleshooting, TASKS.md format requirements.
- **AC**:
  1. New user can follow QUICKSTART.md to install, configure, start server, see dashboard, and sync a project
  2. All 14 API endpoints documented with method, path, and description
  3. Troubleshooting section covers common errors
- **Complexity**: M
- **Deps**: T-P1-5

#### T-P1-7: E2E startup verification [S]
- **Files**: None (verification only)
- **What**: Verify full pipeline: server starts, dashboard loads at localhost:8000, `POST /api/sync-all` syncs TASKS.md, task cards appear on Kanban, review trigger spawns `claude -p`.
- **AC**:
  1. Documented verification checklist passes
  2. Screenshot or log evidence captured
  3. All prior P1 tasks completed and integrated
- **Complexity**: S
- **Deps**: T-P1-5, T-P1-6

### P2 -- Nice to Have (polish, optimization)
<!-- Phase 2+: frontend E2E tests, TypeScript codegen from Pydantic, cross-platform -->

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
