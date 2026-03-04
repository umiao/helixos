# Progress Log

> Append-only session log. Each session adds an entry at the bottom.
> Never edit previous entries.

<!-- Entry format:

## YYYY-MM-DD HH:MM -- [T-XX-N] Brief Title
- **What I did**: 1-3 sentences on concrete actions taken
- **Deliverables**: List of files created/modified
- **Sanity check result**: What I verified and the outcome
- **Status**: [DONE] Done / [PARTIAL] Partial (what remains) / [BLOCKED] Blocked (why)
- **Request**: Cross off TASK-XXX / Move TASK-XXX to In Progress / No change

-->

## 2026-03-01 19:00 -- [T-P0-1] Project scaffold (FastAPI + React + SQLite)
- **What I did**: Set up the full project scaffold. Updated pyproject.toml (name=helixos, all dependencies, asyncio_mode=auto) and requirements.txt. Created src/executors/ and src/sync/ packages. Initialized frontend/ with Vite + React + TypeScript + Tailwind CSS v4 (using @tailwindcss/vite plugin). Configured vite proxy (/api -> localhost:8000). Created orchestrator_config.yaml with full PRD Section 6.2 schema. Created contracts/.gitkeep and scripts/start.ps1. Updated .gitignore with frontend/node_modules/, frontend/dist/, *.db. Extended smoke tests to verify imports, subpackages, and project structure.
- **Deliverables**: pyproject.toml (mod), requirements.txt (mod), src/executors/__init__.py (new), src/sync/__init__.py (new), frontend/ (new tree -- Vite+React+TS+Tailwind), orchestrator_config.yaml (new), contracts/.gitkeep (new), scripts/start.ps1 (new), .gitignore (mod), tests/test_smoke.py (mod)
- **Sanity check result**: pytest tests/test_smoke.py -- 4/4 passed. ruff check src/ -- clean. npm run build -- success. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-1

## 2026-03-01 20:00 -- [T-P0-11] Unified .env loader + env injection
- **What I did**: Created EnvLoader class that reads a unified .env file via python-dotenv and provides per-project key filtering, full access, and validation. Used a Protocol (HasEnvKeys) to decouple from the not-yet-built Project model. Handles missing .env gracefully (warning, empty dict). Warns if ANTHROPIC_API_KEY is missing. All file reads use encoding="utf-8".
- **Deliverables**: src/env_loader.py (new), tests/test_env_loader.py (new)
- **Sanity check result**: pytest tests/ -- 19/19 passed (15 env_loader + 4 smoke). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-11

## 2026-03-01 21:00 -- [T-P0-2] Data model + TaskManager + database layer
- **What I did**: Created all Pydantic models (TaskStatus 9 values, ExecutorType 3 values, Project, Task, ReviewState, LLMReview, ExecutionState, Dependency) with type hints, docstrings, and model_config from_attributes=True. Built SQLAlchemy 2.0 async DB layer with ORM tables (TaskRow, DependencyRow), indexes, conversion helpers, in-memory SQLite support, and async session management. Implemented TaskManager with full CRUD (create, get, list with filters, update), state machine enforcement (VALID_TRANSITIONS dict covering all 9 states), get_ready_tasks, count_running_by_project, and mark_running_as_failed for startup recovery.
- **Deliverables**: src/models.py (new), src/db.py (new), src/task_manager.py (new), tests/test_models.py (new), tests/test_db.py (new), tests/test_task_manager.py (new), tests/conftest.py (mod -- async DB fixtures)
- **Sanity check result**: pytest tests/ -- 82/82 passed (24 models + 8 db + 32 task_manager + 15 env_loader + 4 smoke). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-2

## 2026-03-01 22:00 -- [T-P0-3] Project registry + YAML config loader
- **What I did**: Created Pydantic settings models (OrchestratorSettings, ProjectConfig, GitConfig, StagedSafetyCheck, ReviewerConfig, ReviewPipelineConfig, DependencyConfig, OrchestratorConfig) matching PRD Section 6.2 YAML schema. Built load_config(path) YAML parser with validation. Implemented ProjectRegistry with get_project, list_projects, get_project_config that converts ProjectConfig -> Project model. All Path fields auto-expand tilde via model_validator. Missing repo_path logs warning (not error). All file reads use encoding="utf-8".
- **Deliverables**: src/config.py (new), tests/test_config.py (new)
- **Sanity check result**: pytest tests/ -- 115/115 passed (33 config + 82 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-3

## 2026-03-01 23:00 -- [T-P0-4] TASKS.md parser (one-way sync)
- **What I did**: Created TasksParser class that parses TASKS.md markdown into structured ParsedTask objects. Strict regex matches only T-P\d+-\d+ task IDs. Status inferred from ## section headers (In Progress -> RUNNING, Active Tasks -> BACKLOG, Completed -> DONE, Blocked -> BLOCKED) with configurable status_sections mapping. Built sync_project_tasks async function that reads TASKS.md, parses, and upserts into DB. New BACKLOG tasks enter DB as QUEUED per PRD Section 12.3. Tasks marked done in TASKS.md are force-updated to DONE. Removed tasks stay in DB. Added status_sections field to ProjectConfig. Handles edge cases: tasks without IDs (skip+warn), duplicate IDs (last wins+warn), empty sections, tasks outside sections.
- **Deliverables**: src/sync/tasks_parser.py (new), src/config.py (mod -- status_sections field), tests/test_tasks_parser.py (new), tests/fixtures/sample_tasks.md (new), tests/fixtures/tasks_no_ids.md (new), tests/fixtures/tasks_duplicates.md (new), tests/fixtures/tasks_empty.md (new)
- **Sanity check result**: pytest tests/ -- 158/158 passed (43 parser + 115 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-4

## 2026-03-02 00:00 -- [T-P0-5] CodeExecutor (subprocess + timeout + streaming)
- **What I did**: Created ExecutorResult Pydantic model and BaseExecutor ABC with execute() and cancel() abstract methods per PRD Section 7.1. Implemented CodeExecutor that spawns `claude -p "..." --allowedTools ... --output-format json` via asyncio.create_subprocess_exec. Streams stdout line-by-line via on_log callback. Enforces session timeout with terminate -> grace wait -> kill fallback. cancel() terminates the running subprocess. _build_prompt() generates one-shot prompt per PRD 7.2. Last 100 log lines kept in result. All string decoding uses UTF-8.
- **Deliverables**: src/executors/base.py (new), src/executors/code_executor.py (new), tests/test_code_executor.py (new)
- **Sanity check result**: pytest tests/ -- 184/184 passed (26 executor + 158 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-5

## 2026-03-02 01:00 -- [T-P0-6a] Scheduler core (EventBus + tick loop + concurrency)
- **What I did**: Created EventBus pub/sub system with Event dataclass (type, task_id, data, timestamp), emit() to broadcast to all subscribers, subscribe() async generator with bounded per-subscriber queues (max 1000, drops oldest on overflow), and automatic subscriber cleanup. Implemented Scheduler with tick-based dispatch loop (5s interval via asyncio.create_task), per-project concurrency control (_project_is_busy via DB query), global concurrency limit (min(global_limit, active_projects) - running), dependency checking (_deps_fulfilled verifies all upstream DONE), executor factory (_get_executor returns CodeExecutor for MVP), and task execution handler (success -> DONE + status_change event, failure -> FAILED + alert event, exception -> FAILED + alert). Scheduler supports start/stop lifecycle.
- **Deliverables**: src/events.py (new), src/scheduler.py (new), tests/test_events.py (new), tests/test_scheduler.py (new)
- **Sanity check result**: pytest tests/ -- 219/219 passed (12 events + 23 scheduler + 184 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-6a

## 2026-03-02 02:00 -- [T-P0-6b] Scheduler hardening (retry + recovery + cancel)
- **What I did**: Extended Scheduler with _run_with_retry (exponential backoff 30s/60s/120s, max 3 retries, exhausted -> FAILED -> BLOCKED), startup_recovery (marks orphaned RUNNING tasks as FAILED, emits alert per task, logs warning with count), cancel_task (calls executor.cancel() + asyncio task cancel, updates to FAILED, full cleanup of running/executors/cancelled state), and _auto_commit_hook placeholder (no-op until T-P0-12). Added _executors dict to track active executors for cancel, _cancelled set to prevent retries on cancelled tasks. Extended tests with FailThenSucceedExecutor for retry scenarios.
- **Deliverables**: src/scheduler.py (mod), tests/test_scheduler.py (mod)
- **Sanity check result**: pytest tests/ -- 235/235 passed (39 scheduler + 196 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-6b

## 2026-03-01 03:00 -- [T-P0-12] Git auto-commit with staged safety check
- **What I did**: Created GitOps class with auto_commit (git add -A, count staged files via numstat, safety check against max_files limit, unstage+alert on abort, configurable commit message template) and check_repo_clean utility. All git subprocess calls use asyncio.create_subprocess_exec with UTF-8 decoding. Wired Scheduler._auto_commit_hook to call GitOps.auto_commit with try/except guard so git errors never affect task status. Added 8 tests using tmp_path git repos covering success, safety abort, no-changes, message format, disabled, clean/dirty repo, and no-repo-path edge case.
- **Deliverables**: src/git_ops.py (new), src/scheduler.py (mod), tests/test_git_ops.py (new)
- **Sanity check result**: pytest tests/ -- 243/243 passed (8 git_ops + 235 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-12

## 2026-03-01 04:00 -- [T-P0-7] Review pipeline (Anthropic-only, opt-in, async)
- **What I did**: Created ReviewPipeline class with review_task (1 required + 1 optional adversarial reviewer for M/L tasks), _call_reviewer (Anthropic Messages API), _build_review_prompt (focus-area system prompts for feasibility and adversarial), _parse_review (JSON response -> LLMReview with graceful fallback on parse failure), _synthesize (multi-review consensus via Claude), and _parse_synthesis (score + disagreements extraction). Scoring: single approve=1.0, reject=0.3, multi=synthesized. SynthesisResult model. Score clamping to [0.0, 1.0]. Auto-approve when no active reviewers. Configurable threshold (default 0.8), on_progress callback for SSE progress reporting.
- **Deliverables**: src/review_pipeline.py (new), tests/test_review_pipeline.py (new)
- **Sanity check result**: pytest tests/ -- 263/263 passed (20 review_pipeline + 243 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-7

## 2026-03-01 05:00 -- [T-P0-9] SSE event stream endpoint
- **What I did**: Extended src/events.py with format_sse (Event -> "data: {json}\n\n"), sse_stream async generator (subscribes to EventBus, yields SSE data frames with keepalive comments on idle via asyncio.wait_for timeout), and sse_router (FastAPI APIRouter with GET /api/events endpoint returning StreamingResponse with text/event-stream content type, Cache-Control: no-cache, X-Accel-Buffering: no headers). Cleanup on disconnect via generator finally block. Event JSON schema: {type, task_id, data, timestamp}. Event types: log, status_change, review_progress, alert. Keepalive interval: 15 seconds (configurable in sse_stream).
- **Deliverables**: src/events.py (mod), tests/test_sse.py (new)
- **Sanity check result**: pytest tests/ -- 284/284 passed (21 SSE + 263 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-9

## 2026-03-01 06:00 -- [T-P0-8a] Dashboard Kanban -- static layout + TaskCard
- **What I did**: Created the frontend dashboard with static Kanban layout. Built TypeScript interfaces (types.ts) matching all backend Pydantic models. Created API client stubs (api.ts) with mock data (5 tasks across 2 projects in all column states). Implemented TaskCard component with project ID, task ID, title, status badge (color-coded), and dependency indicator (link icon + count). Built KanbanBoard with 5 columns (BACKLOG, REVIEW, QUEUED, RUNNING, DONE) with color-coded top borders, count badges, and card list. Updated App.tsx with header (title, running count, Sync All button), filter bar (project dropdown, status dropdown, search input), and board. Updated index.css for full-height layout. Board renders mock data to verify layout.
- **Deliverables**: frontend/src/types.ts (new), frontend/src/api.ts (new), frontend/src/components/TaskCard.tsx (new), frontend/src/components/KanbanBoard.tsx (new), frontend/src/App.tsx (mod), frontend/src/index.css (mod)
- **Sanity check result**: npm run build -- success (no TS errors). pytest tests/ -- 284/284 passed. ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-8a

## 2026-03-01 07:00 -- [T-P0-10] API endpoints (CRUD + sync + execute + review + lifespan)
- **What I did**: Created the full FastAPI REST API layer. Built src/schemas.py with Pydantic request/response schemas (ProjectResponse, ProjectDetailResponse, TaskResponse, ReviewStateResponse, ExecutionStateResponse, StatusTransitionRequest, ReviewDecisionRequest, DashboardSummary, SyncResponse, SyncAllResponse, ErrorResponse). Built src/api.py with lifespan handler (init DB, load config, create all services, startup_recovery, start scheduler, shutdown cleanup), CORS middleware for localhost:5173, static mount for frontend/dist/, and all 14 PRD Section 10 endpoints: GET /api/projects, GET /api/projects/{id}, GET /api/tasks (filterable by project_id, status), GET /api/tasks/{id}, PATCH /api/tasks/{id}/status (state machine validated), POST /api/tasks/{id}/review (202, async background), POST /api/tasks/{id}/review/decide, POST /api/tasks/{id}/execute (202), POST /api/tasks/{id}/retry, POST /api/tasks/{id}/cancel, POST /api/projects/{id}/sync, POST /api/sync-all, GET /api/dashboard/summary, GET /api/events (SSE wired from T-P0-9). All endpoints delegate to TaskManager, Scheduler, ReviewPipeline, and TasksParser. Error responses use {"detail": "message"} format with 404/409/500 status codes.
- **Deliverables**: src/schemas.py (new), src/api.py (new), tests/test_api.py (new)
- **Sanity check result**: pytest tests/ -- 316/316 passed (32 new API + 284 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-10

## 2026-03-01 08:00 -- [T-P0-8b] Dashboard Kanban -- drag-drop + API integration
- **What I did**: Replaced mock API stubs with real fetch calls (fetchProjects, fetchTasks, fetchTask, updateTaskStatus, syncAll) with typed ApiError class and error parsing. Installed @dnd-kit/core + @dnd-kit/utilities. Made TaskCard draggable via useDraggable. Wrapped KanbanBoard columns in useDroppable with DndContext, DragOverlay (follows pointer with rotation effect), and drop-to-column status transitions via COLUMN_TO_STATUS mapping. On drop, calls PATCH /api/tasks/{id}/status with optimistic update and rollback on error. Sync All button calls POST /api/sync-all and refreshes board with result counts toast. Added SkeletonCard loading placeholders. Added Toast component for success/error notifications (auto-dismiss 4s). Filter bar fully functional: project dropdown, status dropdown, search input all filter in real-time.
- **Deliverables**: frontend/src/api.ts (rewritten -- real fetch), frontend/src/types.ts (mod -- COLUMN_TO_STATUS), frontend/src/components/KanbanBoard.tsx (rewritten -- dnd-kit), frontend/src/components/TaskCard.tsx (mod -- useDraggable), frontend/src/components/Toast.tsx (new), frontend/src/components/SkeletonCard.tsx (new), frontend/src/App.tsx (rewritten -- loading/sync/toast/drag-drop), frontend/package.json (mod -- @dnd-kit deps)
- **Sanity check result**: npm run build -- success. pytest tests/ -- 316/316 passed. ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-8b

## 2026-03-01 09:00 -- [T-P0-8c] Dashboard -- ExecutionLog + ReviewPanel + SSE
- **What I did**: Created useSSE hook (EventSource with auto-reconnect and exponential backoff 1s/2s/4s/max 30s, connected boolean). Created ExecutionLog component (scrollable dark log panel, task filter dropdown, auto-scroll with scroll-lock detection, timestamps, max 500 lines). Created ReviewPanel component (review progress bar, consensus score visualization, decision points list, approve/reject buttons when human_decision_needed, calls POST /api/tasks/{id}/review/decide). Wired SSE into App: status_change events auto-update card positions (+ fetch full task for execution/review state), alert events show as error toasts, log events populate ExecutionLog, review_progress events logged. Added connection status indicator (green/red dot) in header. Added elapsed time timer on running cards (ElapsedTimer component with 1s interval). Added bottom panel with tab switching between ExecutionLog and ReviewPanel. Added submitReviewDecision to api.ts. Updated types.ts to match API response schema (removed unused LLMReview, removed reviews from ReviewState). Added onClick to TaskCard/KanbanBoard for task selection.
- **Deliverables**: frontend/src/hooks/useSSE.ts (new), frontend/src/components/ExecutionLog.tsx (new), frontend/src/components/ReviewPanel.tsx (new), frontend/src/components/KanbanBoard.tsx (mod -- onSelectTask), frontend/src/components/TaskCard.tsx (mod -- elapsed timer, onClick), frontend/src/App.tsx (rewritten -- SSE, log, review, connection indicator), frontend/src/api.ts (mod -- submitReviewDecision), frontend/src/types.ts (mod -- removed unused LLMReview, cleaned ReviewState)
- **Sanity check result**: npm run build -- success. pytest tests/ -- 316/316 passed. ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-8c

## 2026-03-01 10:00 -- [T-P0-13] Integration testing (end-to-end)
- **What I did**: Created full integration test suite with 19 tests across 5 test modules covering all major backend lifecycle flows. Built shared fixtures in conftest.py: MockExecutor (configurable success/fail results with delay), MockAnthropicClient (configurable JSON responses), temp git repo, config factory, in-memory SQLite. test_sync_to_execute (4 tests): TASKS.md sync creates QUEUED tasks, scheduler tick dispatches and completes, git auto-commit runs after success, SSE events emitted. test_review_flow (4 tests): single reviewer auto-approve, reviewer reject triggers REVIEW_NEEDS_HUMAN, human reject returns to BACKLOG, multi-reviewer synthesis with consensus scoring. test_failure_retry (3 tests): fail-then-succeed on retry, max retries exhausted becomes BLOCKED with [30,60,120]s backoff, retry log events emitted. test_concurrency (3 tests): per-project limit enforced, global effective limit with 2 projects (interleaved task creation), dependency blocking prevents dispatch. test_startup_recovery (5 tests): orphaned RUNNING marked FAILED, alerts emitted per task, no-op when clean, error_summary set on recovered tasks, recovered tasks can be re-queued.
- **Deliverables**: tests/integration/__init__.py (new), tests/integration/conftest.py (new), tests/integration/test_sync_to_execute.py (new), tests/integration/test_review_flow.py (new), tests/integration/test_failure_retry.py (new), tests/integration/test_concurrency.py (new), tests/integration/test_startup_recovery.py (new)
- **Sanity check result**: pytest tests/ -- 335/335 passed (19 integration + 316 existing). ruff check -- clean. check_emoji.py -- no emoji found.
- **Status**: [DONE]
- **Request**: Cross off T-P0-13

## 2026-03-01 11:00 -- [SOP-FIX] Fix TASKS.md task lifecycle SOP
- **What I did**: Defense-in-depth fix for orphaned task specs in TASKS.md Active section causing the session context hook to surface completed tasks as pending work. Level 0: removed 5 orphaned spec blocks from Active Tasks. Level 1: updated CLAUDE.md and exit-protocol.md to explicitly require "remove from Active + add to Completed" (two-step). Level 2: added completed_ids dedup filter to session_context.py _get_active_tasks(). Level 3: created task_dedup_check.py stop hook (blocks exit on overlap) and registered it in settings.json.
- **Deliverables**: TASKS.md (mod), CLAUDE.md (mod), docs/workflow/exit-protocol.md (mod), .claude/hooks/session_context.py (mod), .claude/hooks/task_dedup_check.py (new), .claude/settings.json (mod)
- **Sanity check result**: task_dedup_check.py exit 0 on clean TASKS.md, exit 2 on synthetic overlap. session_context.py shows no orphaned tasks. pytest 335/335 passed. ruff clean. No emoji.
- **Status**: [DONE]
- **Request**: No task status change (SOP fix, not a TASKS.md task)

## 2026-03-01 12:00 -- [T-P1-1] Review pipeline refactor -- Replace Anthropic SDK with claude -p
- **What I did**: Refactored ReviewPipeline to use Claude CLI subprocess (`claude -p`) instead of the Anthropic Python SDK. Added `_call_claude_cli()` method that invokes `asyncio.create_subprocess_exec` with `--system-prompt`, `--model`, `--output-format json`, `--json-schema`, `--no-session-persistence`, `--max-budget-usd 0.50`. Removed `anthropic_client` parameter from `__init__`. Updated `_call_reviewer()` and `_synthesize()` to use the new CLI method. Adapted all 20 unit tests and 4 integration tests to mock `asyncio.create_subprocess_exec` instead of the Anthropic client. Removed `MockAnthropicClient` from integration conftest. Updated api.py lifespan to create ReviewPipeline without anthropic import.
- **Deliverables**: src/review_pipeline.py (rewritten), tests/test_review_pipeline.py (rewritten), tests/integration/test_review_flow.py (rewritten), tests/integration/conftest.py (mod -- removed MockAnthropicClient), src/api.py (mod -- removed anthropic import)
- **Sanity check result**: pytest 335/335 passed. ruff clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P1-1 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 13:00 -- [T-P1-2] API lifespan cleanup -- Remove Anthropic SDK init
- **What I did**: Added `claude --version` check at startup in api.py lifespan. If the Claude CLI is in PATH and returns exit 0, the version is logged and ReviewPipeline is created. If claude is not found (FileNotFoundError) or exits non-zero, a warning is logged and review_pipeline is set to None. Removed ANTHROPIC_API_KEY from test_api.py fixture .env file. Updated test comment to reference Claude CLI instead of Anthropic client.
- **Deliverables**: src/api.py (mod -- claude --version check in lifespan), tests/test_api.py (mod -- removed ANTHROPIC_API_KEY from test .env)
- **Sanity check result**: pytest 335/335 passed. ruff clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P1-2 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 14:00 -- [T-P1-3] Remove ANTHROPIC_API_KEY dependency from env/config
- **What I did**: Removed ANTHROPIC_API_KEY warning from env_loader.py _load(). Removed `anthropic>=0.40.0` from requirements.txt and pyproject.toml dependencies. Changed `api: "anthropic"` to `api: "claude_cli"` in orchestrator_config.yaml and ReviewerConfig default. Updated all test fixtures/assertions: removed TestAnthropicKeyWarning class and env_file_no_anthropic fixture from test_env_loader.py, replaced ANTHROPIC_API_KEY with API_KEY in test .env fixtures, updated test_config.py and test_review_pipeline.py api references.
- **Deliverables**: src/env_loader.py (mod), src/config.py (mod), requirements.txt (mod), pyproject.toml (mod), orchestrator_config.yaml (mod), tests/test_env_loader.py (mod), tests/test_config.py (mod), tests/test_review_pipeline.py (mod), tests/integration/conftest.py (mod)
- **Sanity check result**: pytest 333/333 passed (2 removed warning tests). ruff clean. No emoji. No ANTHROPIC_API_KEY in src/.
- **Status**: [DONE]
- **Request**: Move T-P1-3 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 15:00 -- [T-P1-4] Update review pipeline tests for subprocess mocking
- **What I did**: Verified that T-P1-1 already completed all subprocess mocking work (no MockAnthropicClient references in any .py files, all test files use `@patch("src.review_pipeline.asyncio.create_subprocess_exec")`, MockExecutor unchanged in integration conftest). Fixed a pre-existing race condition in test_sse.py::test_mixed_events_and_keepalive where tight timing (0.05s keepalive interval, 0.08s sleep) caused two keepalives to fire instead of one on loaded systems. Increased keepalive_interval to 0.15s and sleep to 0.25s for reliable single-keepalive timing.
- **Deliverables**: tests/test_sse.py (mod -- fixed timing race condition)
- **Sanity check result**: pytest 333/333 passed. ruff clean. No emoji. No MockAnthropicClient in any .py files.
- **Status**: [DONE]
- **Request**: Move T-P1-4 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 16:00 -- [T-P1-5] Fix orchestrator config for self-management
- **What I did**: Fixed `repo_path` in orchestrator_config.yaml from `~/projects/helixos` to `~/Desktop/Gen_AI_Proj/helixos` to match the actual project location. Added `~/.helixos/` directory auto-creation in api.py lifespan (creates parent directories for both `state_db_path` and `unified_env_path` before DB engine init, using `mkdir(parents=True, exist_ok=True)`).
- **Deliverables**: orchestrator_config.yaml (mod -- fixed repo_path), src/api.py (mod -- added data directory creation)
- **Sanity check result**: pytest 333/333 passed. ruff clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P1-5 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 17:00 -- [T-P1-6] Create root-level QUICKSTART.md
- **What I did**: Created comprehensive QUICKSTART.md at project root covering all required sections: prerequisites (Python 3.11+, Node.js 18+, Claude Code CLI), installation (venv, pip, npm), configuration (orchestrator_config.yaml with all sections: orchestrator settings, adding projects, git auto-commit, review pipeline, env vars), running (development with hot-reload, production, Windows PowerShell script), first sync walkthrough, TASKS.md format requirements (structure, task ID convention, section-to-status mapping), full API reference table (all 14 endpoints with method, path, description, and example curl commands), autonomous mode explanation, and troubleshooting section (server startup, Claude CLI, database, frontend build, sync issues, stuck tasks, SSE connection).
- **Deliverables**: QUICKSTART.md (new)
- **Sanity check result**: pytest 333/333 passed. ruff clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P1-6 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 18:00 -- [T-P1-7] E2E startup verification
- **What I did**: Ran full E2E verification of the HelixOS pipeline. Started uvicorn server (port 8000), verified all 14 API endpoints respond correctly, confirmed frontend dashboard loads from static build, verified POST /api/sync-all parses TASKS.md (20 tasks synced: 19 done, 1 running), confirmed SSE endpoint streams with correct content-type, verified review pipeline initialized with Claude CLI 2.1.63, confirmed state machine enforces valid transitions (409 on invalid). Created docs/e2e_verification.md with full checklist and evidence.
- **Deliverables**: docs/e2e_verification.md (new)
- **Sanity check result**: pytest 333/333 passed. ruff clean. No emoji. All verification checks pass.
- **Status**: [DONE]
- **Request**: Move T-P1-7 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 19:00 -- [Ad-hoc] Fix Windows PowerShell compatibility in docs
- **What I did**: Fixed bash-only shell syntax in user-facing docs that fails on Windows PowerShell 5.x. Split `&&` chains into separate lines, added labeled PowerShell alternatives for `source`, `rm -rf`, `rm ~` commands. Added a "Windows-compatible docs" rule to CLAUDE.md Code Style section to prevent regressions.
- **Deliverables**: QUICKSTART.md (4 fixes), README.md (1 fix), CLAUDE.md (1 rule added)
- **Sanity check result**: Grep confirms no `&&` remains in user-runnable code blocks. All `rm -rf` occurrences in docs now have PowerShell alternatives. Remaining matches are in CLAUDE.md rule text (mentioning what to avoid) and claude-code-workflow-guide.md (explanatory text, not user commands -- explicitly out of scope).
- **Status**: [DONE]
- **Request**: No task status change (ad-hoc fix, not a tracked task)

## 2026-03-02 20:00 -- [Ad-hoc] Fix Windows asyncio subprocess crash on startup
- **What I did**: Fixed `NotImplementedError` from `asyncio.create_subprocess_exec` on Windows. Added `import sys` and `asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())` at module level in src/api.py, guarded by `sys.platform == "win32"`. Broadened the Claude CLI check `except FileNotFoundError` to also catch `NotImplementedError` and `OSError` as defensive fallback. Added "Windows asyncio subprocess" rule to CLAUDE.md Code Style section to prevent recurrence. Added LESSONS.md entry (#7) documenting the pattern and root cause. Created regression test (tests/test_windows_asyncio.py) with 2 tests: one verifying the policy is set on import, one verifying the except clause catches the right exceptions.
- **Deliverables**: src/api.py (mod -- import sys, ProactorEventLoop policy, broadened except), CLAUDE.md (mod -- new rule), LESSONS.md (mod -- lesson #7), tests/test_windows_asyncio.py (new -- 2 tests)
- **Sanity check result**: pytest 335/335 passed (2 new + 333 existing). ruff check src/api.py -- clean. No emoji.
- **Status**: [DONE]
- **Request**: No task status change (ad-hoc bug fix, not a tracked task)

## 2026-03-02 21:00 -- [T-P2-1] Extend ProjectConfig + OrchestratorSettings for P2 features
- **What I did**: Added PortRange model with min/max port validation. Extended OrchestratorSettings with port_ranges (dict[str, PortRange], defaults: frontend 3100-3999, backend 8100-8999) and max_total_subprocesses (int, default 5, ge=1). Extended ProjectConfig with launch_command (str|None), project_type (Literal["frontend","backend","other"], default "other"), preferred_port (int|None, 1024-65535). All new fields are optional with defaults, fully backward compatible.
- **Deliverables**: src/config.py (mod -- PortRange model, new fields on OrchestratorSettings and ProjectConfig), tests/test_config.py (mod -- 24 new tests)
- **Sanity check result**: pytest 359/359 passed (24 new + 335 existing). ruff check clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P2-1 to Completed

## 2026-03-02 22:00 -- [T-P2-2] PortRegistry -- auto-assign ports, conflict detection, persistence
- **What I did**: Created src/port_registry.py with PortRegistry class. Manages per-project port assignments from configured ranges. Features: assign_port (with preferred_port and exclude_ports support), release_port, get_assignment, update_pid, list_assignments, cleanup_orphans (removes entries for dead PIDs). Persistence via atomic write (tmp + os.replace) to ~/.helixos/ports.json. Corrupted files handled gracefully (starts fresh). Parent directories auto-created.
- **Deliverables**: src/port_registry.py (new -- PortRegistry class, PortAssignment model, _is_process_alive helper), tests/test_port_registry.py (new -- 33 tests)
- **Sanity check result**: pytest 392/392 passed (33 new + 359 existing). ruff check clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P2-2 to Completed

## 2026-03-02 23:00 -- [T-P2-3] Project validation + import API + config writer (ruamel.yaml)
- **What I did**: Created project onboarding backend: config_writer.py (ruamel.yaml read-modify-write with comment preservation, atomic write via tmp + os.replace, suggest_next_project_id), project_validator.py (validate directory for .git, TASKS.md, CLAUDE.md with warnings and limited-mode reasons). Added POST /api/projects/validate and POST /api/projects/import endpoints to api.py. Validate returns validity, presence flags, suggested ID, warnings, limited-mode reasons. Import writes to YAML, reloads ProjectRegistry in-memory, auto-assigns port via PortRegistry, auto-syncs if TASKS.md present. Rejects duplicates (409), invalid paths (400). Added request/response schemas. Added ruamel.yaml to requirements.txt.
- **Deliverables**: src/config_writer.py (new), src/project_validator.py (new), src/api.py (mod -- 2 new endpoints, PortRegistry init in lifespan), src/schemas.py (mod -- 4 new schemas), requirements.txt (mod), tests/test_project_onboarding.py (new -- 29 tests)
- **Sanity check result**: pytest 421/421 passed (29 new + 392 existing). ruff check clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P2-3 to Completed

## 2026-03-02 23:30 -- [T-P2-4] TasksWriter -- create tasks by appending to TASKS.md (with filelock)
- **What I did**: Created src/tasks_writer.py with TasksWriter class. Uses filelock + threading.Lock for cross-platform concurrent write protection. Features: generate_next_task_id (scans existing IDs, computes next sequential), _find_active_section_end (locates insertion point), _build_task_block (formats markdown), .bak backup before every write, post-write validation (re-reads file, checks task ID exists, checks for corruption). Handles edge cases: empty file (creates minimal structure), no Active section (adds one), ID format variations. Added POST /api/projects/{id}/tasks endpoint to api.py with auto-sync after write. Added CreateTaskRequest/CreateTaskResponse schemas.
- **Deliverables**: src/tasks_writer.py (new), src/api.py (mod -- 1 new endpoint), src/schemas.py (mod -- 2 new schemas), requirements.txt (mod -- filelock), tests/test_tasks_writer.py (new -- 28 tests)
- **Sanity check result**: pytest 449/449 passed (28 new + 421 existing). ruff check clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P2-4 to Completed

## 2026-03-03 00:00 -- [T-P2-5] ProcessManager + SubprocessRegistry -- launch/stop project processes
- **What I did**: Created SubprocessRegistry (unified tracker for ALL subprocesses with shared global limit) and ProcessManager (launch/stop project dev servers). SubprocessRegistry tracks PID, type, project_id, start_time; enforces max_total_subprocesses limit; supports cleanup_dead for orphan removal. ProcessManager spawns launch_command via asyncio.create_subprocess_shell with PORT env var injection; graceful stop (terminate -> grace timeout -> force kill); per-project status with uptime; stop_all for shutdown; cleanup_orphans at startup. Windows compatible (CREATE_NEW_PROCESS_GROUP + CTRL_BREAK_EVENT). Added 3 API endpoints: POST /launch, POST /stop, GET /process-status. Wired shutdown order: ProcessManager.stop_all -> Scheduler.stop -> DB. Added orphan cleanup for subprocesses, ports, and dev servers at startup.
- **Deliverables**: src/subprocess_registry.py (new), src/process_manager.py (new), src/api.py (mod -- 3 new endpoints, SubprocessRegistry + ProcessManager in lifespan, shutdown order), src/schemas.py (mod -- ProcessStatusResponse), tests/test_process_manager.py (new -- 31 tests)
- **Sanity check result**: pytest 480/480 passed (31 new + 449 existing). ruff check clean. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P2-5 to Completed

## 2026-03-03 01:00 -- [T-P2-6] Frontend -- ProjectSelector + SwimLane + KanbanBoard refactor
- **What I did**: Transformed flat Kanban into per-project swim lanes. Created ProjectSelector.tsx (multi-select checkbox dropdown with Select all/Clear, click-outside-to-close, shows project name + ID). Created SwimLane.tsx (wrapper rendering project header bar + KanbanBoard per project; solo mode takes full height, multi-lane mode uses fixed 320px height). Refactored App.tsx: replaced single-select project filter with ProjectSelector; renders one SwimLane per selected project with visible dividers between lanes; groups tasks by project_id; localStorage persistence via loadSelectedProjects/saveSelectedProjects; new projects auto-selected; global status filter + search apply across all swim lanes. Each SwimLane has its own DndContext (via KanbanBoard) so drag-drop is scoped per project with no cross-project dragging.
- **Deliverables**: frontend/src/components/ProjectSelector.tsx (new), frontend/src/components/SwimLane.tsx (new), frontend/src/App.tsx (mod -- swim lane layout, ProjectSelector integration, localStorage persistence)
- **Sanity check result**: npm run build succeeds. pytest 480/480 passed. ruff check clean.
- **Status**: [DONE]
- **Request**: Move T-P2-6 to Completed

## 2026-03-03 02:00 -- [T-P2-7] Frontend -- SwimLaneHeader + ImportModal + NewTaskModal + LaunchControl
- **What I did**: Built all frontend UI components for the operations portal. Created SwimLaneHeader.tsx (per-project action bar with Launch/Stop, New Task, Sync buttons, limited-mode warning badges). Created LaunchControl.tsx (launch/stop toggle with port display, running indicator with green pulse dot, uptime display, 5s status polling when running). Created ImportProjectModal.tsx (3-step flow: path input -> validate -> review with name/type/port/command overrides -> import with success summary and warnings). Created NewTaskModal.tsx (title + description + priority form with validation, loading states, error display). Added "Import Project" button in main header. Updated types.ts with ProcessStatus, ValidationResult, ImportResult, CreateTaskResult, SyncResult. Updated api.ts with 7 new API calls (syncProject, validateProject, importProject, createTask, launchProject, stopProject, getProcessStatus). Refactored SwimLane.tsx to use SwimLaneHeader. Updated App.tsx with per-project sync state tracking, modal state management, and data reload after import/create.
- **Deliverables**: frontend/src/components/SwimLaneHeader.tsx (new), frontend/src/components/LaunchControl.tsx (new), frontend/src/components/ImportProjectModal.tsx (new), frontend/src/components/NewTaskModal.tsx (new), frontend/src/types.ts (mod -- 5 new types), frontend/src/api.ts (mod -- 7 new API functions), frontend/src/components/SwimLane.tsx (mod -- uses SwimLaneHeader), frontend/src/App.tsx (mod -- Import button, modals, per-project sync)
- **Sanity check result**: npm run build succeeds. pytest 480/480 passed. ruff check clean.
- **Status**: [DONE]
- **Request**: Move T-P2-7 to Completed

## 2026-03-03 03:00 -- [T-P2-8] E2E integration + SSE events for P2 features
- **What I did**: Wired together all P2 features end-to-end. Added ProjectProcessStatus schema and process_status dict to DashboardSummary (per-project running/pid/port/uptime). Updated dashboard_summary endpoint to query ProcessManager for each project. Added mock ProcessManager to test_api.py fixtures so existing API tests pass with the new field. Verified SSE events (process_start/process_stop already emitted by ProcessManager from T-P2-5), startup orphan cleanup (SubprocessRegistry, PortRegistry, ProcessManager already in lifespan from T-P2-5), and shutdown order (ProcessManager -> Scheduler -> DB already in lifespan). Wrote 14 integration tests covering: import-to-swimlane flow, idempotent resync, task creation via TasksWriter + sync, backup creation, process launch/stop SSE events, full launch-status-stop cycle with registry tracking, dashboard process status, startup orphan cleanup (3 registries), shutdown stops all processes, shutdown order enforcement, full E2E flow (import -> create task -> launch -> SSE events -> stop).
- **Deliverables**: src/schemas.py (mod -- ProjectProcessStatus, DashboardSummary.process_status), src/api.py (mod -- dashboard_summary uses ProcessManager, imports ProjectProcessStatus), tests/test_api.py (mod -- mock ProcessManager in test app), tests/integration/test_e2e_p2.py (new -- 14 integration tests)
- **Sanity check result**: pytest 494/494 passed (14 new + 480 existing). ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P2-8 to Completed

## 2026-03-03 04:00 -- [CI-FIX] Fix CI ruff lint failures + add pre-commit hook
- **What I did**: Fixed 3 ruff lint errors caused by CI using latest ruff (which promoted UP041/UP042 to stable) while local had ruff 0.1.14. Changed `asyncio.TimeoutError` to `TimeoutError` in events.py and process_manager.py (UP041). Changed `(str, Enum)` to `StrEnum` in models.py (UP042). Upgraded local ruff to 0.15.4 and pinned it exactly in requirements.txt (`ruff==0.15.4`). Updated CI to use `pip install -r requirements.txt` instead of `pip install ruff`. Created scripts/pre-commit (ruff check on staged .py files) and scripts/install-hooks.sh (copies hook to .git/hooks/). Added lesson #8 to LESSONS.md about pinning linter versions.
- **Deliverables**: src/events.py (mod), src/process_manager.py (mod), src/models.py (mod), requirements.txt (mod), .github/workflows/ci.yml (mod), scripts/pre-commit (new), scripts/install-hooks.sh (new), LESSONS.md (mod)
- **Sanity check result**: ruff check src/ tests/ -- 0 errors. pytest 494/494 passed. npm run build succeeds.
- **Status**: [DONE]
- **Request**: No task status change (CI fix, not a tracked task)

## 2026-03-03 05:00 -- [T-P3-1] Fix "No CLAUDE.md" false-positive badge
- **What I did**: Fixed the false-positive "No CLAUDE.md" badge in SwimLaneHeader. The root cause was that `ProjectResponse` schema was missing the `claude_md_path` field, so the frontend always received `null`. Added `claude_md_path` to `ProjectResponse` and `ProjectDetailResponse` schemas. Updated `_project_to_response()` and `get_project()` to include the field. Added auto-detection in `ProjectRegistry._build()` that checks for `repo_path/CLAUDE.md` when `claude_md_path` is not explicitly set in config. Updated import endpoint to auto-write `claude_md_path` to YAML config when CLAUDE.md exists. Updated SwimLaneHeader to use descriptive tooltips (e.g., "No CLAUDE.md found in project root -- Claude agent lacks project-specific context and conventions").
- **Deliverables**: src/schemas.py (mod), src/api.py (mod), src/config.py (mod), frontend/src/components/SwimLaneHeader.tsx (mod), tests/test_config.py (mod -- 3 new tests), tests/test_api.py (mod -- 1 updated test), tests/test_project_onboarding.py (mod -- 3 new tests)
- **Sanity check result**: pytest 500/500 passed (6 new + 494 existing). ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-1 to Completed

## 2026-03-03 06:00 -- [T-P3-2] Backend directory browser + frontend picker
- **What I did**: Implemented GET /api/filesystem/browse endpoint with $HOME sandbox. The endpoint lists subdirectories within a given path (defaulting to $HOME), filters hidden directories, and includes project indicator flags (has_git, has_tasks_md, has_claude_md). Created a DirectoryPicker React component that navigates directories with breadcrumb, parent navigation, project indicator badges, and per-entry select buttons. Integrated the picker into ImportProjectModal as a toggleable "Browse..." option alongside the existing text input.
- **Deliverables**: src/api.py (mod -- browse_directory endpoint), src/schemas.py (mod -- BrowseEntry, BrowseResponse), frontend/src/api.ts (mod -- browseDirectory), frontend/src/types.ts (mod -- BrowseEntry, BrowseResult), frontend/src/components/DirectoryPicker.tsx (new), frontend/src/components/ImportProjectModal.tsx (mod -- browse toggle), tests/test_browse.py (new -- 11 tests)
- **Sanity check result**: pytest 511/511 passed (11 new + 500 existing). ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-2 to Completed

## 2026-03-03 07:00 -- [T-P3-3] Import Project in ProjectSelector dropdown
- **What I did**: Added an "Import Project" button at the bottom of the ProjectSelector dropdown menu. The button has a + icon, is separated by a divider, closes the dropdown, and opens the existing ImportProjectModal. Wired up via a new optional `onImportClick` prop on ProjectSelector, connected in App.tsx.
- **Deliverables**: frontend/src/components/ProjectSelector.tsx (mod -- onImportClick prop + Import button in dropdown), frontend/src/App.tsx (mod -- pass onImportClick to ProjectSelector)
- **Sanity check result**: pytest 511/511 passed. ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-3 to Completed

## 2026-03-03 08:00 -- [T-P3-4] Task card hover popover with details
- **What I did**: Created TaskCardPopover component rendered via React portal showing full task details (description, dependencies, execution state with log tail, review state with consensus score, timestamps). Added 300ms hover delay to TaskCard with auto-positioning (right/left/below card), combined dnd-kit + local refs, and auto-hide on drag start.
- **Deliverables**: frontend/src/components/TaskCardPopover.tsx (new), frontend/src/components/TaskCard.tsx (mod -- hover logic + popover integration)
- **Sanity check result**: pytest 511/511 passed. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-4 to Completed

## 2026-03-03 09:00 -- [T-P3-5] Workflow clarity -- inline task creation, context menu, tooltips
- **What I did**: Created InlineTaskCreator component at bottom of Backlog column (click to expand, type title, Enter to create via API, Esc to cancel). Created TaskContextMenu component rendered via portal at right-click position with view details, move-to-column, and retry actions. Added title tooltips to all interactive buttons across the UI (header, swim lane header, launch/stop, panel tabs, project selector).
- **Deliverables**: frontend/src/components/InlineTaskCreator.tsx (new), frontend/src/components/TaskContextMenu.tsx (new), frontend/src/components/KanbanBoard.tsx (mod -- inline creator + context menu integration), frontend/src/components/TaskCard.tsx (mod -- onContextMenu prop), frontend/src/components/SwimLane.tsx (mod -- onTaskCreated prop), frontend/src/App.tsx (mod -- onTaskCreated wiring + tooltips), frontend/src/components/SwimLaneHeader.tsx (mod -- tooltips), frontend/src/components/LaunchControl.tsx (mod -- tooltips), frontend/src/components/ProjectSelector.tsx (mod -- tooltip)
- **Sanity check result**: pytest 511/511 passed. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-5 to Completed

## 2026-03-02 10:00 -- [T-P3-6a] Persistent execution log + review history -- backend
- **What I did**: Added 2 new SQLAlchemy ORM tables (ExecutionLogRow, ReviewHistoryRow) with composite indexes on (task_id, timestamp). Created HistoryWriter service with DB-first write_log (single + batch), write_review, write_review_decision, and paginated get_logs/get_reviews with count helpers. All text fields enforce 2KB cap via _truncate. Wired HistoryWriter into Scheduler (logs execution start, success, failure, cancel events) and ReviewPipeline (persists each reviewer round with consensus score on final round). Added human decision persistence in review/decide endpoint. Created 2 new API endpoints: GET /api/tasks/{id}/logs (paginated, level-filterable) and GET /api/tasks/{id}/reviews (paginated). Added response schemas (ExecutionLogEntry, ExecutionLogsResponse, ReviewHistoryEntry, ReviewHistoryResponse).
- **Deliverables**: src/db.py (mod -- ExecutionLogRow + ReviewHistoryRow), src/history_writer.py (new -- HistoryWriter service), src/scheduler.py (mod -- history_writer param + DB-first log writes), src/review_pipeline.py (mod -- history_writer param + DB-first review writes), src/api.py (mod -- HistoryWriter in lifespan, 2 new endpoints, review decide persistence), src/schemas.py (mod -- 4 new response schemas), tests/test_history_writer.py (new -- 22 tests), tests/test_api.py (mod -- history_writer fixture + 9 new endpoint tests)
- **Sanity check result**: pytest 542/542 passed (31 new + 511 existing). ruff check clean. npm run build succeeds. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P3-6a to Completed

## 2026-03-02 11:00 -- [T-P3-6b] Persistent execution log + review history -- frontend
- **What I did**: Refactored the bottom panel to be task-focused. ExecutionLog now has two modes: all-tasks (existing SSE-only behavior) and task-focused (fetches persistent logs from DB via GET /api/tasks/{id}/logs, merges with live SSE entries, shows level badges and source tags, polls every 5s). ReviewPanel now fetches review history from GET /api/tasks/{id}/reviews and displays each round as a conversation-style card with reviewer focus/model, verdict badge (approve=green, reject=red, revise=yellow), summary, suggestions list, consensus score bar, and human decision inline. Added task focus indicator in bottom panel tab bar with clear (x) button. Added 4 new TypeScript interfaces and 2 new API client functions.
- **Deliverables**: frontend/src/types.ts (mod -- ExecutionLogEntry, ExecutionLogsResponse, ReviewHistoryEntry, ReviewHistoryResponse), frontend/src/api.ts (mod -- fetchExecutionLogs, fetchReviewHistory), frontend/src/components/ExecutionLog.tsx (rewrite -- task-focused mode with DB fetch + SSE merge, level coloring, source tags, poll interval), frontend/src/components/ReviewPanel.tsx (rewrite -- conversation-style history timeline, verdict badges, suggestions, consensus bar), frontend/src/App.tsx (mod -- selectedTaskId prop to ExecutionLog, focus indicator chip with clear button)
- **Sanity check result**: pytest 542/542 passed. ruff check clean. npm run build succeeds. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P3-6b to Completed

## 2026-03-02 12:00 -- [T-P3-7] README overhaul
- **What I did**: Replaced the generic "Claude Code Project Template" README with a project-specific HelixOS README. New content includes: project description, feature list (6 key features), ASCII architecture diagram showing component relationships and data flow, task state machine diagram, tech stack table, quick start with cross-platform instructions, configuration reference, backend modules table (20 modules), frontend components table (16 components), API reference table (14 key endpoints), TASKS.md format documentation, testing commands, project structure tree, and development workflow section covering hooks and autonomous mode.
- **Deliverables**: README.md (rewrite -- from generic template to project-specific documentation)
- **Sanity check result**: pytest 542/542 passed. ruff check clean. npm run build succeeds. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P3-7 to Completed

## 2026-03-02 13:00 -- [T-P0-15] Surface detailed execution error diagnostics
- **What I did**: Added structured error classification to execution results. Created ErrorType enum (INFRA, CLI_NOT_FOUND, REPO_NOT_FOUND, NON_ZERO_EXIT, TIMEOUT, UNKNOWN) on ExecutorResult. Added pre-flight checks in CodeExecutor (os.path.isdir for repo_path, shutil.which for claude CLI) that return typed errors before subprocess spawn. Implemented stderr capture with 4KB truncation and ANSI escape sequence stripping. Updated scheduler catch-all to include exception type+message in SSE alerts and execution log (was "Unhandled execution error", now "Unhandled execution error: ValueError: missing config key"). Added MAX_CONCURRENT_EXECUTIONS=2 hard limit in scheduler. Updated ExecutionState model and API schemas with error_type field.
- **Deliverables**: src/executors/base.py (mod -- ErrorType enum, error_type + stderr_output fields), src/executors/code_executor.py (mod -- pre-flight checks, stderr capture, ANSI stripping, error classification), src/scheduler.py (mod -- MAX_CONCURRENT_EXECUTIONS, error_type in alerts, exception details in catch-all), src/models.py (mod -- error_type field on ExecutionState), src/schemas.py (mod -- error_type on ExecutionStateResponse), tests/test_code_executor.py (mod -- 20 new tests), tests/test_scheduler.py (mod -- 7 new tests)
- **Sanity check result**: pytest 569/569 passed (27 new). ruff check clean. npm run build succeeds. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P0-15 to Completed

## 2026-03-02 14:00 -- [T-P0-16] Per-project execution pause/resume gate
- **What I did**: Added DB-backed per-project execution pause/resume gate. Created ProjectSettingsRow DB table (project_id PK, execution_paused bool) and ProjectSettingsStore service for async get/set. Scheduler now has pause_project(), resume_project(), is_project_paused() methods with DB persistence -- paused state survives server restarts. tick() skips QUEUED tasks for paused projects; in-flight tasks continue. Two new API endpoints (POST /api/projects/{id}/pause-execution, POST /api/projects/{id}/resume-execution). ProjectResponse and ProjectDetailResponse schemas include execution_paused field. Pause/resume emits execution_paused SSE event for real-time UI update. SwimLaneHeader has amber Pause/Resume toggle button with PAUSED badge and descriptive tooltips. Frontend handles SSE execution_paused events to update project state.
- **Deliverables**: src/db.py (mod -- ProjectSettingsRow table), src/project_settings.py (new -- ProjectSettingsStore), src/scheduler.py (mod -- pause/resume methods, tick skip paused, DB persistence load), src/api.py (mod -- pause/resume endpoints, settings_store wiring, execution_paused in project responses), src/schemas.py (mod -- execution_paused on ProjectResponse/ProjectDetailResponse), frontend/src/types.ts (mod -- execution_paused on Project), frontend/src/api.ts (mod -- pauseExecution, resumeExecution), frontend/src/components/SwimLaneHeader.tsx (mod -- Pause/Resume toggle button, PAUSED badge), frontend/src/components/SwimLane.tsx (mod -- onPauseToggle prop), frontend/src/App.tsx (mod -- SSE handler, onPauseToggle prop), tests/test_project_settings.py (new -- 11 tests), tests/test_scheduler.py (mod -- 10 new tests), tests/test_api.py (mod -- 6 new tests)
- **Sanity check result**: pytest 596/596 passed (27 new). ruff check clean. npm run build succeeds. No emoji.
- **Status**: [DONE]
- **Request**: Move T-P0-16 to Completed

## 2026-03-02 15:00 -- [T-P3-8] Self-hosting guardrails -- design document
- **What I did**: Created comprehensive design document for self-hosting guardrails at docs/design/self-hosting-guardrails.md. Covers all 6 AC areas: worker isolation via git worktree branches (agent works in .worktrees/<task-id> instead of live codebase), commit serialization with pytest+ruff validation gate before fast-forward merge to main, log isolation with [SELF-HOST] source tagging, human-triggered-only restart mechanism (no auto-restart to prevent crash loops), safety boundary classification (code/tests/docs=SAFE; DB schema/config/scheduler/hooks=UNSAFE requiring human gate), and ASCII state diagram for the full self-modification lifecycle. Also addresses recursive execution prevention, security considerations, and a 5-phase implementation plan.
- **Deliverables**: docs/design/self-hosting-guardrails.md (new -- design document)
- **Sanity check result**: pytest 596/596 passed. ruff check clean. npm run build succeeds. No emoji in document.
- **Status**: [DONE]
- **Request**: Move T-P3-8 to Completed

## 2026-03-02 16:30 -- [T-P3-9] AI-assisted task enrichment via Claude CLI
- **What I did**: Implemented full AI-assisted task enrichment feature. Backend: created src/enrichment.py with Claude CLI integration (reuses review_pipeline JSON extraction pattern and code_executor pre-flight check pattern), added EnrichTaskRequest/EnrichTaskResponse schemas, added POST /api/tasks/enrich endpoint (returns 503 if Claude CLI unavailable). Frontend: added enrichTask() API client function, "Enrich with AI" button in NewTaskModal that pre-fills description and priority (editable before submit), InlineTaskCreator Tab key expands to NewTaskModal with auto-enrich triggered, loading states and error handling throughout.
- **Deliverables**: src/enrichment.py (new), src/schemas.py (updated), src/api.py (updated), frontend/src/types.ts (updated), frontend/src/api.ts (updated), frontend/src/components/NewTaskModal.tsx (updated), frontend/src/components/InlineTaskCreator.tsx (updated), frontend/src/components/KanbanBoard.tsx (updated), frontend/src/components/SwimLane.tsx (updated), frontend/src/App.tsx (updated), tests/test_enrichment.py (new -- 19 tests)
- **Sanity check result**: pytest 615/615 passed. ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-9 to Completed

## 2026-03-02 17:00 -- [T-P3-10] Done column sorting and sub-status filtering
- **What I did**: Added sort dropdown and sub-status filter badges to the DONE column header in KanbanBoard. Sort options: "Newest first" (default, by completed_at/updated_at desc), "Oldest first", "By task ID". Sub-status badges (DONE/FAILED/BLOCKED) show counts and toggle filtering on click. Both preferences persist in localStorage. Client-side only, no backend changes.
- **Deliverables**: frontend/src/components/KanbanBoard.tsx (updated)
- **Sanity check result**: pytest 615/615 passed. ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-10 to Completed

## 2026-03-02 18:00 -- [T-P3-11] Enhanced review observation and human interaction UX
- **What I did**: Implemented 4 UX improvements for the review pipeline: (1) Task cards in REVIEW column show pulsing badge when review is active, orange for needs-human, green for auto-approved (badges already existed, added pulse animation). (2) REVIEW_NEEDS_HUMAN SSE status_change triggers toast notification, auto-switches bottom panel to Review tab, and auto-selects the task. (3) ReviewPanel now includes a reason text area above approve/reject buttons, wired to the existing ReviewDecisionRequest.reason field. (4) REVIEW column header shows a pulsing orange "N needs human" badge when tasks require human attention. Client-side only, no backend changes.
- **Deliverables**: frontend/src/components/TaskCard.tsx, frontend/src/components/KanbanBoard.tsx, frontend/src/components/ReviewPanel.tsx, frontend/src/App.tsx (all updated)
- **Sanity check result**: pytest 615/615 passed. ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-11 to Completed

## 2026-03-02 19:00 -- [MAINT] Clean up failed task + rename project keys to descriptive slugs
- **What I did**: (1) Preserved T-P0-14 text in TASKS.md Tech Debt section before deletion. (2) Deleted T-P0-14 from DB (tasks, execution_logs). (3) Renamed project keys in orchestrator_config.yaml: P0->helixos, P1->homestead. (4) Migrated all DB tables (tasks, execution_logs, review_history, dependencies, project_settings) to use new project_id values. (5) Rewrote suggest_next_project_id() from P-number sequential to slug-based (derives from project name, e.g. "My App" -> "my-app" with collision suffixes). (6) Updated API call sites to pass project_name. (7) Updated all onboarding tests for new slug behavior (34 tests, up from 29).
- **Deliverables**: TASKS.md (mod), orchestrator_config.yaml (mod), src/config_writer.py (mod), src/api.py (mod), tests/test_project_onboarding.py (mod), ~/.helixos/state.db (migrated)
- **Sanity check result**: pytest 617/617 passed. ruff check clean. npm run build succeeds. DB: project_id values are helixos/homestead, 0 failed tasks.
- **Status**: [DONE]
- **Request**: No task status change (maintenance, not a tracked task)

## 2026-03-02 20:00 -- [T-P0-17] Design analysis -- evaluate achievements and future directions
- **What I did**: Root cause analysis of three issues that surfaced during T-P0-17 task creation: (1) no review gate before execution (BACKLOG->QUEUED allowed without review), (2) asyncio NotImplementedError on Windows with --reload (uvicorn forces SelectorEventLoop), (3) fixed-height bottom panel with no resize capability. Created design document at docs/design/review-gate-asyncio-divider.md with root causes, proposed fixes (two-layer review gate defense, --loop none for uvicorn, ResizableDivider component), files to modify, and verification plans. Added three new tasks to TASKS.md: T-P0-18 (review gate, M), T-P0-19 (asyncio fix, S), T-P3-12 (resizable divider, M).
- **Deliverables**: docs/design/review-gate-asyncio-divider.md (new), TASKS.md (mod -- added T-P0-18, T-P0-19, T-P3-12; moved T-P0-17 to Completed)
- **Sanity check result**: Design doc reviewed for accuracy against source code. TASKS.md structure valid.
- **Status**: [DONE]
- **Request**: Move T-P0-17 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 21:00 -- [T-P0-19] Fix asyncio NotImplementedError on Windows with --reload
- **What I did**: Added `--loop none` to uvicorn command in scripts/start.ps1 to prevent uvicorn from forcing SelectorEventLoop on Windows with --reload. Split the except clause in src/api.py lifespan to distinguish NotImplementedError (wrong event loop, suggests --loop none fix) from FileNotFoundError (missing Claude CLI). Added defense-in-depth comment explaining why the ProactorEventLoopPolicy at module level is kept. Updated QUICKSTART.md with Windows-specific dev mode instructions and new troubleshooting section for the asyncio issue.
- **Deliverables**: scripts/start.ps1 (mod), src/api.py (mod), QUICKSTART.md (mod), tests/test_windows_asyncio.py (mod -- 4 tests, up from 2)
- **Sanity check result**: pytest 619/619 passed. ruff check clean.
- **Status**: [DONE]
- **Request**: Move T-P0-19 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 22:30 -- [T-P0-18] Configurable review gate before execution (two-layer defense)
- **What I did**: Implemented two-layer review gate per design doc. Layer 1: added `review_gate_enabled` column to ProjectSettingsRow, get/set methods in ProjectSettingsStore, and a `review_gate_enabled` parameter to TaskManager.update_status() that blocks BACKLOG->QUEUED when enabled. Layer 2: added `_can_execute()` method in Scheduler that queries ReviewHistoryRow for an approved verdict before allowing execution. Added `has_approved_review()` to HistoryWriter. Added Scheduler review gate toggle methods with SSE events and DB persistence. Added PATCH /api/projects/{id}/review-gate API endpoint. Updated ProjectResponse/ProjectDetailResponse schemas. Frontend: added `review_gate_enabled` to Project type, `setReviewGate()` API function, "Gate ON/OFF" toggle in SwimLaneHeader, and SSE handler for `review_gate_changed` events in App.tsx.
- **Deliverables**: src/db.py (mod), src/project_settings.py (mod), src/task_manager.py (mod), src/scheduler.py (mod), src/history_writer.py (mod), src/api.py (mod), src/schemas.py (mod), frontend/src/types.ts (mod), frontend/src/api.ts (mod), frontend/src/components/SwimLaneHeader.tsx (mod), frontend/src/App.tsx (mod), tests/test_review_gate.py (new -- 22 tests), tests/test_api.py (mod -- updated mock scheduler for review gate)
- **Sanity check result**: pytest 641/641 passed. ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P0-18 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 23:00 -- [T-P3-12] Resizable bottom panel divider
- **What I did**: Created ResizableDivider.tsx component with native pointer events and setPointerCapture for reliable drag handling. Divider is a 6px tall bar with grip dots that highlights on hover/drag. Dragging up increases bottom panel height, dragging down decreases it. Bounds enforced: min 80px, max 60% viewport. Double-click resets to default 224px. Height persists to localStorage (key: helixos_panel_height). Wired into App.tsx: replaced fixed h-56 class with dynamic inline style driven by bottomPanelHeight state. No conflict with @dnd-kit since divider uses native pointer capture outside DndContext.
- **Deliverables**: frontend/src/components/ResizableDivider.tsx (new), frontend/src/App.tsx (mod)
- **Sanity check result**: pytest 641/641 passed. ruff check clean. npm run build succeeds.
- **Status**: [DONE]
- **Request**: Move T-P3-12 to Completed (REMOVE spec block from Active, ADD summary line to Completed Tasks)

## 2026-03-02 23:30 -- [T-P0-20] Fix --loop none breaks uvicorn CLI startup
- **What I did**: uvicorn 0.27.0 CLI rejects `--loop none` (click.Choice excludes it), crashing `start.ps1` on startup. Created `scripts/run_server.py` that calls `uvicorn.run(loop="none")` programmatically (the Python API accepts it). Updated `start.ps1` to call `run_server.py`. Updated error messages in `src/api.py` to reference `run_server.py`. Updated QUICKSTART.md Windows sections and troubleshooting. Updated design doc with post-implementation note. Rewrote `tests/test_windows_asyncio.py` with behavioral tests (mock uvicorn.run, assert kwargs) plus upstream guard tests (assert "none" in LOOP_SETUPS, assert "none" not in CLI choices).
- **Deliverables**: scripts/run_server.py (new), scripts/start.ps1 (mod), src/api.py (mod), tests/test_windows_asyncio.py (rewrite -- 8 tests), QUICKSTART.md (mod), docs/design/review-gate-asyncio-divider.md (mod), LESSONS.md (append)
- **Sanity check result**: pytest 645/645 passed. ruff check clean.
- **Status**: [DONE]
- **Request**: Move T-P0-20 to Completed (ADD summary line to Completed Tasks)
## 2026-03-02 23:55 -- [T-P0-20-followup] Fix all stale uvicorn references post T-P0-20
- **What I did**: T-P0-20 only updated 2 of ~8 uvicorn command references. Audited ALL .md files for bare uvicorn commands missing Windows alternatives. Added --log-level arg to run_server.py for feature parity. Fixed 6 spots in QUICKSTART.md (production mode, Windows quick start description, autonomous mode, port-in-use troubleshooting, QUEUED troubleshooting, project structure tree). Fixed 2 spots in README.md (quick start, project structure tree). Fixed 1 spot in self-hosting-guardrails.md (PowerShell restart block). Added 3 new tests: --log-level behavioral test, default log level test, and doc regression guard that scans all PowerShell code blocks for bare uvicorn commands.
- **Deliverables**: scripts/run_server.py (mod), QUICKSTART.md (mod), README.md (mod), docs/design/self-hosting-guardrails.md (mod), tests/test_windows_asyncio.py (mod -- 11 tests), LESSONS.md (append 9b)
- **Sanity check result**: pytest 648/648 passed. ruff check clean.
- **Status**: [DONE]
- **Request**: No TASKS.md change needed (followup fix, not a separate task)
## 2026-03-03 00:10 -- [T-P0-20-followup-2] Fix ModuleNotFoundError in run_server.py
- **What I did**: User reported `python scripts/run_server.py --no-reload` crashes with `ModuleNotFoundError: No module named 'src'`. Root cause: uvicorn CLI does `sys.path.insert(0, ".")` in its main(), but `uvicorn.run()` does NOT. When running `python scripts/run_server.py`, Python adds `scripts/` to sys.path[0], not the project root. All 648 tests passed because they mocked `uvicorn.run()` and never tested the real import path. Fixed by adding `sys.path.insert(0, project_root)` in run_server.py. Added 2 smoke tests: one verifying project root is on sys.path, one verifying `src` is importable after main() runs. Added lesson 10 about mock-only test blindness. Updated TASKS.md T-P0-20 entry with followup note.
- **Deliverables**: scripts/run_server.py (mod), tests/test_windows_asyncio.py (mod -- 13 tests), LESSONS.md (append 10), TASKS.md (mod)
- **Sanity check result**: pytest 650/650 passed. ruff check clean. Manual test: server starts without ModuleNotFoundError.
- **Status**: [DONE]
- **Request**: No TASKS.md change needed (T-P0-20 already updated with followup note)
## 2026-03-03 01:00 -- [T-P0-20-followup-3] Fix stale DB crash + real smoke tests + best practices
- **What I did**: Fixed stale DB crash where `state.db` created before T-P0-18 is missing `review_gate_enabled` column (create_all only creates missing TABLES, not COLUMNS). Added `_migrate_missing_columns()` to `init_db()` that introspects existing tables and ALTERs to add any missing columns with correct defaults. Added real subprocess-based server smoke test that starts run_server.py, waits for "Application startup complete", and hits GET /api/projects. Added run_server.py importability check to test_smoke.py. Embedded verification best practices in CLAUDE.md (verification requirements section, schema change rule), LESSONS.md (lesson 11), and tightened stop hook rule 2 for script/server verification.
- **Deliverables**: src/db.py (mod -- _migrate_missing_columns + init_db update), tests/test_db.py (mod -- 3 migration tests), tests/test_server_startup.py (new -- 1 real smoke test, @pytest.mark.slow), tests/test_smoke.py (mod -- importability check), CLAUDE.md (mod -- verification rules + schema change rule), LESSONS.md (mod -- lesson 11), .claude/settings.json (mod -- tightened stop hook), TASKS.md (mod -- T-P0-20 followup note)
- **Sanity check result**: pytest 655/655 passed (654 fast + 1 slow). ruff check clean. Real smoke test: server starts on random port, HTTP 200 on /api/projects, clean shutdown.
- **Status**: [DONE]
- **Request**: No TASKS.md change needed (T-P0-20 already updated with followup-3 note)

## 2026-03-02 23:45 -- [TASK-PLANNING] Add 4 new P0 tasks from approved plan
- **What I did**: Added 4 new P0 tasks (T-P0-21 through T-P0-24) to TASKS.md based on the approved plan analysis. T-P0-21: fix review gate bypass (5 vulnerable paths). T-P0-22: soft-delete tasks via context menu + API. T-P0-23: bidirectional state transitions + concurrency control. T-P0-24: review gate UX modal. Updated dependency graph with new task relationships.
- **Deliverables**: TASKS.md (mod -- 4 new task specs under Active P0, dependency graph updated)
- **Sanity check result**: TASKS.md has sequential IDs (T-P0-21 through T-P0-24), dependency graph shows T-P0-21 -> T-P0-23 -> T-P0-24 chain and T-P0-22 independent. No code files modified.
- **Status**: [DONE]
- **Request**: No further changes needed

## 2026-03-02 -- [T-P0-21] Fix review gate bypass -- 5 vulnerable paths
- **What I did**: Fixed all 5 code paths that bypassed the review gate. (1) Removed BACKLOG->QUEUED auto-promotion in sync_project_tasks(). (2) PATCH /status now returns 428 with gate_action hint when gate blocks. (3) POST /execute passes review_gate_enabled. (4) POST /retry passes review_gate_enabled. (5) POST /review/decide passes review_gate_enabled for defense-in-depth. Added ReviewGateBlockedError custom exception to TaskManager so API can distinguish gate blocks (428) from invalid transitions (409).
- **Deliverables**: src/sync/tasks_parser.py (mod), src/task_manager.py (mod -- ReviewGateBlockedError), src/api.py (mod -- 4 endpoints), tests/test_review_gate_bypass.py (new -- 15 tests), tests/test_review_gate.py (mod), tests/test_tasks_parser.py (mod), tests/test_api.py (mod), tests/integration/test_sync_to_execute.py (mod), tests/integration/test_e2e_p2.py (mod)
- **Sanity check result**: 670 tests passing (up from 655). Ruff clean. All 5 bypass paths regression-tested.
- **Status**: [DONE]
- **Request**: Move T-P0-21 to Completed

## 2026-03-02 -- [T-P0-22] Soft-delete tasks via context menu + API
- **What I did**: Implemented full soft-delete capability. Added is_deleted column to TaskRow with auto-migration. TaskManager.delete_task() enforces: no deleting RUNNING tasks (409), no deleting tasks with active dependents (409 unless force=True). All query methods (get_task, list_tasks, get_ready_tasks, count_running_by_project, mark_running_as_failed) filter out soft-deleted tasks. DELETE /api/tasks/{task_id}?force= endpoint returns 204/404/409 (with dependents list). Frontend: deleteTask() API function, red Delete option in TaskContextMenu with confirmation dialog and force-delete flow for dependent tasks.
- **Deliverables**: src/db.py (mod -- is_deleted column), src/task_manager.py (mod -- delete_task, get_dependents, is_deleted filtering in all queries), src/api.py (mod -- DELETE endpoint), frontend/src/api.ts (mod -- deleteTask), frontend/src/components/TaskContextMenu.tsx (mod -- Delete with confirmation), frontend/src/components/KanbanBoard.tsx (mod -- onTaskDeleted prop), frontend/src/components/SwimLane.tsx (mod -- onTaskDeleted prop), frontend/src/App.tsx (mod -- handleTaskDeleted), tests/test_soft_delete.py (new -- 22 tests)
- **Sanity check result**: 692 tests passing (up from 670). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-22 to Completed

## 2026-03-03 -- [T-P0-23] Bidirectional state transitions + concurrency control
- **What I did**: Implemented full bidirectional state machine. Updated VALID_TRANSITIONS to allow backward drags (REVIEW->BACKLOG, QUEUED->BACKLOG/REVIEW, DONE->BACKLOG/QUEUED, FAILED->BACKLOG). RUNNING remains strict (DONE/FAILED only) with clear error messages. Added timestamp cleanup matrix (_cleanup_on_backward) that clears completed_at and execution_json on backward transitions. Added OptimisticLockError with updated_at comparison (normalizes Z vs +00:00). StatusTransitionRequest now accepts optional reason and expected_updated_at fields. API returns 409 with conflict=true on optimistic lock mismatch. Frontend: KanbanBoard detects backward drags and shows prompt for reason, App.tsx sends expected_updated_at with every transition, auto-refreshes task on conflict. Updated 3 existing tests for new error message format and transition table.
- **Deliverables**: src/task_manager.py (mod -- VALID_TRANSITIONS, OptimisticLockError, _build_transition_error, _cleanup_on_backward, update_status with reason/expected_updated_at), src/schemas.py (mod -- reason + expected_updated_at on StatusTransitionRequest), src/api.py (mod -- OptimisticLockError import, 409 conflict response), frontend/src/api.ts (mod -- updateTaskStatus with opts), frontend/src/App.tsx (mod -- handleMoveTask with reason/conflict handling), frontend/src/components/KanbanBoard.tsx (mod -- backward drag detection + prompt), frontend/src/components/SwimLane.tsx (mod -- onMoveTask type), frontend/src/components/TaskContextMenu.tsx (mod -- onMoveTask type), tests/test_bidirectional_transitions.py (new -- 52 tests), tests/test_task_manager.py (mod -- 3 tests updated), tests/test_api.py (mod -- 1 test updated), tests/test_review_gate_bypass.py (mod -- 1 test updated)
- **Sanity check result**: 744 tests passing (up from 692). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-23 to Completed

## 2026-03-03 -- [T-P0-24] Review gate UX -- edit modal + preview before review submission
- **What I did**: Implemented the review gate UX flow. Added PATCH /api/tasks/{id} endpoint for updating title/description (with UpdateTaskRequest schema). Updated frontend api.ts: added updateTask() for PATCH fields, updated updateTaskStatus() to detect 428 responses with gate_action/task_id. Completed ReviewSubmitModal component with edit fields (title/description), live preview, edit indicator, PATCH-if-changed then BACKLOG->REVIEW transition. Updated App.tsx: detects 428 gate_action="review_required" and opens ReviewSubmitModal, handleReviewSubmitted auto-focuses task in ReviewPanel. Added "Send to Review" option in TaskContextMenu for BACKLOG/QUEUED tasks (opens modal directly). Threaded onSendToReview through SwimLane -> KanbanBoard -> TaskContextMenu.
- **Deliverables**: src/api.py (mod -- PATCH /api/tasks/{id} endpoint, UpdateTaskRequest import), src/schemas.py (mod -- UpdateTaskRequest), frontend/src/api.ts (mod -- updateTask, 428 handling in updateTaskStatus), frontend/src/components/ReviewSubmitModal.tsx (mod -- complete with edit+preview+PATCH), frontend/src/App.tsx (mod -- reviewSubmitTask state, 428 detection, handleReviewSubmitted, handleSendToReview, modal rendering), frontend/src/components/TaskContextMenu.tsx (mod -- onSendToReview prop, Send to Review button), frontend/src/components/KanbanBoard.tsx (mod -- onSendToReview prop passthrough), frontend/src/components/SwimLane.tsx (mod -- onSendToReview prop passthrough), tests/test_review_gate_ux.py (new -- 15 tests)
- **Sanity check result**: 759 tests passing (up from 744). Ruff clean. Frontend builds clean. TypeScript type check passes.
- **Status**: [DONE]
- **Request**: Move T-P0-24 to Completed

## 2026-03-03 -- [T-P0-26] Fix drag-to-REVIEW workflow -- transition-driven pipeline + review_status
- **What I did**: Implemented the transition-driven review pipeline. Added review_status column to TaskRow (idle/running/done/failed) with auto-migration. Backend: status transition handler now auto-enqueues review pipeline when a task enters REVIEW status (sets review_status=running, fires SSE review_started). Pipeline success sets review_status=done and transitions to REVIEW_AUTO_APPROVED or REVIEW_NEEDS_HUMAN. Pipeline failure sets review_status=failed, emits SSE review_failed, task stays in REVIEW. Pipeline unavailable (no Claude CLI) fails immediately. Backward transitions (REVIEW->BACKLOG) reset review_status to idle. Repurposed POST /api/tasks/{id}/review as retry-only (409 if running, works for failed/idle). Frontend: Task interface gains review_status field. App.tsx auto-focuses ReviewPanel on drag-to-REVIEW and handles review_started/review_failed SSE events. ReviewPanel renders based on review_status (idle=no review, running=spinner, done=results, failed=error+retry button). Added retryReview API function.
- **Deliverables**: src/db.py (mod -- review_status column, task_row_to_dict, task_dict_to_row_kwargs), src/models.py (mod -- review_status on Task), src/schemas.py (mod -- review_status on TaskResponse), src/task_manager.py (mod -- review_status lifecycle in update_status, _cleanup_on_backward, new set_review_status method), src/api.py (mod -- _enqueue_review_pipeline helper, _set_review_failed, transition-driven trigger in update_task_status, retry_review endpoint replacing trigger_review, _task_to_response with review_status), frontend/src/types.ts (mod -- ReviewStatus type, review_status on Task), frontend/src/api.ts (mod -- retryReview function), frontend/src/App.tsx (mod -- auto-focus on REVIEW drag, SSE review_started/review_failed handlers), frontend/src/components/ReviewPanel.tsx (mod -- review_status-based rendering with idle/running/done/failed states, retry button), tests/test_drag_to_review.py (new -- 25 tests), tests/test_api.py (mod -- TestRetryReview replacing TestTriggerReview)
- **Sanity check result**: 784 tests passing (up from 759). Ruff clean. Frontend builds clean. TypeScript type check passes.
- **Status**: [DONE]
- **Request**: Move T-P0-26 to Completed

## 2026-03-03 -- [T-P0-27] Add planning quality rules to CLAUDE.md + LESSONS.md postmortem
- **What I did**: Added 6 actionable rules to CLAUDE.md to prevent T-P0-24-class planning bugs. Task Planning Rules section (5 rules): scenario matrix for conditional UX tasks, journey-first ACs, cross-boundary integration checks, "other case" gate, manual smoke test AC. State Machine Rules section (1 rule): document states/triggers/side-effects, backend owns transition side-effects. Added LESSONS.md entry #12 with T-P0-24 root cause analysis covering 4 failure modes (missing scenario matrix, no journey-first AC, cross-boundary gap, no manual smoke test).
- **Deliverables**: CLAUDE.md (mod -- Task Planning Rules section, State Machine Rules section), LESSONS.md (mod -- entry #12 T-P0-24 postmortem)
- **Sanity check result**: 784 tests passing (no code changes, doc-only task). Rules are checkable and specific, not aspirational.
- **Status**: [DONE]
- **Request**: Move T-P0-27 to Completed

## 2026-03-03 00:50 -- [T-P0-28] Store full reviewer raw_response + surface in ReviewPanel
- **What I did**: Added raw_response storage and display across the full stack. Backend: added raw_response TEXT column to ReviewHistoryRow (auto-migrated), raw_response field to LLMReview model, _truncate_raw_response() helper (200KB limit with [TRUNCATED at 200KB] marker) in review_pipeline.py, raw_response capture after _call_claude_cli returns, persistence in HistoryWriter.write_review() and retrieval in get_reviews(), raw_response field on ReviewHistoryEntry API schema. Frontend: raw_response field on ReviewHistoryEntry type, collapsible "Show Full Response (debug)" section in ReviewPanel with triangle toggle, amber warning banner inside expanded section, max-h-64 scrollable pre block, auto-collapse on task switch. Legacy/empty raw_response hides section entirely.
- **Deliverables**: src/db.py (mod -- raw_response column on ReviewHistoryRow), src/models.py (mod -- raw_response on LLMReview), src/review_pipeline.py (mod -- _truncate_raw_response, raw_response capture in _call_reviewer), src/history_writer.py (mod -- raw_response in write_review + get_reviews), src/schemas.py (mod -- raw_response on ReviewHistoryEntry), frontend/src/types.ts (mod -- raw_response on ReviewHistoryEntry), frontend/src/components/ReviewPanel.tsx (mod -- collapsible raw response section with warning banner)
- **Sanity check result**: 792 tests passing (8 new: 5 review_pipeline + 3 history_writer). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-28 to Completed

## 2026-03-03 01:30 -- [T-P0-29] Upgrade primary reviewer to Opus + per-reviewer budget config + cost tracking
- **What I did**: Upgraded primary reviewer from claude-sonnet-4-5 to claude-opus-4-6 with per-reviewer budget config. Backend: added max_budget_usd field to ReviewerConfig (default 0.50), _call_claude_cli now reads reviewer.max_budget_usd instead of hardcoded "0.50" and returns full CLI output dict (for usage extraction), _extract_cost_usd() computes approximate cost from CLI usage data (input/output tokens with model-specific pricing table), cost_usd nullable FLOAT column on ReviewHistoryRow (auto-migrated), cost_usd persisted in HistoryWriter.write_review() and returned by get_reviews(), cost_usd field on LLMReview model and ReviewHistoryEntry API schema. Frontend: cost_usd field on ReviewHistoryEntry type, ~$X.XX cost badge per review entry (hidden when NULL). orchestrator_config.yaml: primary uses claude-opus-4-6/max_budget_usd:2.00, adversarial stays claude-sonnet-4-5/max_budget_usd:0.50.
- **Deliverables**: orchestrator_config.yaml (mod), src/config.py (mod -- max_budget_usd on ReviewerConfig), src/models.py (mod -- cost_usd on LLMReview), src/review_pipeline.py (mod -- _extract_cost_usd, _call_claude_cli returns dict, max_budget_usd param, _MODEL_PRICING), src/db.py (mod -- cost_usd column on ReviewHistoryRow), src/history_writer.py (mod -- cost_usd in write_review + get_reviews), src/schemas.py (mod -- cost_usd on ReviewHistoryEntry), frontend/src/types.ts (mod -- cost_usd on ReviewHistoryEntry), frontend/src/components/ReviewPanel.tsx (mod -- cost badge)
- **Sanity check result**: 807 tests passing (15 new: 11 review_pipeline + 2 config + 3 history_writer). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-29 to Completed

## 2026-03-03 02:00 -- [T-P0-30] Subprocess inactivity timeout + process group cleanup for execution pipeline
- **What I did**: Added process group isolation and inactivity timeout detection to CodeExecutor. Process group: subprocess created with start_new_session=True (Unix) / CREATE_NEW_PROCESS_GROUP (Windows), matching ProcessManager pattern. On timeout/cancel, entire process group killed via os.killpg(SIGTERM) (Unix) / CTRL_BREAK_EVENT (Windows), with SIGKILL fallback after grace period. Inactivity timeout: replaced async-for stdout iteration with per-line asyncio.wait_for(readline(), timeout=inactivity_seconds). No output for inactivity_timeout_minutes (default 20, 0=disabled) -> process group terminated with ErrorType.INACTIVITY_TIMEOUT. Config: added inactivity_timeout_minutes to OrchestratorSettings (ge=0, default 20). ErrorType: added INACTIVITY_TIMEOUT enum value.
- **Deliverables**: src/executors/base.py (mod -- INACTIVITY_TIMEOUT enum), src/config.py (mod -- inactivity_timeout_minutes field), src/executors/code_executor.py (rewrite -- process group flags, _terminate_process_group, _kill_process_group, readline-based inactivity detection), orchestrator_config.yaml (mod -- inactivity_timeout_minutes: 20), tests/test_code_executor.py (rewrite -- readline-based mocks, process group tests, inactivity scenarios)
- **Sanity check result**: 820 tests passing (13 new: 6 inactivity timeout + 3 process group helpers + 4 config). Ruff clean.
- **Status**: [DONE]
- **Request**: Move T-P0-30 to Completed

## 2026-03-03 03:00 -- [T-P0-31] Apply timeout to review pipeline subprocess calls
- **What I did**: Added process group isolation and timeout to review pipeline's `_call_claude_cli()`. Process group: same pattern as CodeExecutor (start_new_session on Unix, CREATE_NEW_PROCESS_GROUP on Windows). Timeout: `proc.communicate()` wrapped with `asyncio.wait_for(timeout=review_timeout_minutes*60)`. On timeout: SIGTERM to process group, 5s grace, SIGKILL fallback, then RuntimeError raised (caught by `_run_review_bg` -> review_status=failed + SSE alert + Retry button). Config: `review_timeout_minutes: int = 10` on ReviewPipelineConfig. Retry semantics: added `review_attempt` column to ReviewHistoryRow (auto-migrated, default 1). Each retry increments attempt via `get_max_review_attempt()`. History shows all attempts for audit. API: `retry_review` computes next attempt number from DB, passes to pipeline.
- **Deliverables**: src/config.py (mod -- review_timeout_minutes on ReviewPipelineConfig), src/db.py (mod -- review_attempt column on ReviewHistoryRow), src/review_pipeline.py (mod -- process group helpers, timeout in _call_claude_cli, review_attempt param), src/history_writer.py (mod -- review_attempt param + get_max_review_attempt), src/api.py (mod -- review_attempt wiring in _enqueue_review_pipeline + retry_review), orchestrator_config.yaml (mod -- review_timeout_minutes: 10), tests/test_review_pipeline.py (mod -- 17 new tests), tests/test_history_writer.py (mod -- 7 new tests)
- **Sanity check result**: 843 tests passing (23 new). Ruff clean.
- **Status**: [DONE]
- **Request**: Move T-P0-31 to Completed

## 2026-03-03 04:00 -- [T-P0-32] Review + execution progress phase reporting via SSE
- **What I did**: Extended review pipeline on_progress callback to `(completed, total, phase)` with phase strings "Starting {focus} review...", "Completed {focus} review", "Synthesizing...". API forwards phase in SSE review_progress events. CodeExecutor spawns a background _progress_reporter task that emits `[PROGRESS]` log entries every 60s with elapsed time, line count, and seconds since last output. Frontend: ReviewPanel shows live phase label when review_status=running (replaces generic "Review in progress..."). ExecutionLog shows live elapsed counter (M:SS) in header when selected task is RUNNING. SSE task_id guard: review_progress and review_started events only update reviewPhase state if event.task_id matches selected task. Phase cleared on task switch/deselect.
- **Deliverables**: src/review_pipeline.py (mod -- on_progress phase param, phase strings before/after each reviewer + synthesis), src/api.py (mod -- phase in on_progress + SSE review_progress), src/executors/code_executor.py (mod -- PROGRESS_LOG_INTERVAL_SECONDS, _format_elapsed, _progress_reporter background task), frontend/src/components/ReviewPanel.tsx (mod -- reviewPhase prop, phase label display), frontend/src/components/ExecutionLog.tsx (mod -- elapsed counter state/effect/display), frontend/src/App.tsx (mod -- reviewPhase state, selectedTaskRef, task_id guard in SSE handlers, props wiring), tests/test_review_pipeline.py (mod -- 4 new phase tests + updated all callbacks to 3-arg), tests/test_code_executor.py (mod -- 7 new tests: _format_elapsed + progress log), tests/integration/test_review_flow.py (mod -- updated callbacks)
- **Sanity check result**: 854 tests passing (11 new). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-32 to Completed

## 2026-03-03 05:00 -- [T-P0-33] Fix review panel data bugs (T-P0-28 regressions)
- **What I did**: Fixed 3 data-path bugs in ReviewPanel. AC1: raw_response now stores explicit CLI fields (model, usage, result, session_id) as structured JSON instead of just the parsed result text -- decouples DB schema from CLI contract and provides metadata not shown in summary/suggestions. AC2: Added collapsible "Plan Under Review" section at top of ReviewPanel showing task.description; when empty shows "(No plan content provided to reviewer)". AC3: Added human_reason TEXT NULL column to ReviewHistoryRow (auto-migrated), updated write_review_decision to accept+persist reason, wired through API endpoint, added human_reason to ReviewHistoryEntry schema and frontend types, frontend displays reason below "Human decision:" label.
- **Deliverables**: src/review_pipeline.py (mod -- structured raw_response), src/db.py (mod -- human_reason column), src/history_writer.py (mod -- reason param in write_review_decision, human_reason in get_reviews), src/api.py (mod -- pass body.reason to write_review_decision), src/schemas.py (mod -- human_reason on ReviewHistoryEntry), frontend/src/types.ts (mod -- human_reason on ReviewHistoryEntry), frontend/src/components/ReviewPanel.tsx (mod -- Plan Under Review section, human_reason display), tests/test_review_pipeline.py (mod -- 4 new/updated raw_response tests), tests/test_history_writer.py (mod -- 4 new human_reason tests)
- **Sanity check result**: 860 tests passing (6 new). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-33 to Completed

## 2026-03-03 06:00 -- [T-P0-34] Request Changes decision + human feedback loop
- **What I did**: Added "request_changes" as a third decision type alongside approve/reject. Backend: request_changes requires non-empty reason (400 if empty), transitions REVIEW_NEEDS_HUMAN -> REVIEW with review_status=idle. Added REVIEW_NEEDS_HUMAN -> REVIEW to VALID_TRANSITIONS state machine. Added get_human_feedback() to HistoryWriter to retrieve all previous human feedback entries. On re-review (retry_review endpoint), fetches all previous human feedback and injects into reviewer prompts as "Previous human feedback" section. Frontend: replaced 2-button decision area with 3-button selection (Approve green, Request Changes amber, Reject red), amber border on textarea when Request Changes selected, submit disabled when reason empty for request_changes. Added "Re-review" button shown after request_changes when review_status=idle. Decision buttons disabled with tooltip when review is running. Added review_attempt display in history entries.
- **Deliverables**: src/task_manager.py (mod -- REVIEW_NEEDS_HUMAN -> REVIEW transition), src/api.py (mod -- request_changes handling in submit_review_decision, human feedback fetch+pass in retry_review, _enqueue_review_pipeline human_feedback param), src/schemas.py (mod -- decision description, review_attempt on ReviewHistoryEntry), src/review_pipeline.py (mod -- human_feedback param on review_task and _call_reviewer, feedback injection into prompt), src/history_writer.py (mod -- get_human_feedback method), frontend/src/types.ts (mod -- review_attempt on ReviewHistoryEntry), frontend/src/components/ReviewPanel.tsx (mod -- 3-button decision area, Re-review button, running-state disabled buttons), tests/test_api.py (mod -- 4 new request_changes tests), tests/test_history_writer.py (mod -- 5 new get_human_feedback tests), tests/test_review_pipeline.py (mod -- 4 new human feedback injection tests), tests/test_bidirectional_transitions.py (mod -- updated REVIEW_NEEDS_HUMAN targets)
- **Sanity check result**: 873 tests passing (13 new). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-34 to Completed

## 2026-03-03 07:30 -- [T-P0-35] Inline plan editing + versioned review history
- **What I did**: Added plan_snapshot column to ReviewHistoryRow (auto-migrated, TEXT NULL). Review pipeline stores immutable snapshot of task.description at pipeline start (first round only). Created PlanDiffView component with LCS-based line diff. ReviewPanel now groups history entries by review_attempt with "Attempt N" headers. Added inline plan editor (Edit Plan -> textarea with Save/Cancel) using existing PATCH endpoint. Plan diff banner shown between attempt groups when plan changed. App.tsx wires onTaskUpdated to refresh task state after inline edit.
- **Deliverables**: src/db.py (mod -- plan_snapshot column), src/history_writer.py (mod -- plan_snapshot param on write_review, returned in get_reviews), src/review_pipeline.py (mod -- snapshot capture, plan_snapshot passed to write_review), src/schemas.py (mod -- plan_snapshot on ReviewHistoryEntry), frontend/src/types.ts (mod -- plan_snapshot on ReviewHistoryEntry), frontend/src/components/ReviewPanel.tsx (mod -- inline editor, attempt grouping, PlanDiffView integration), frontend/src/components/PlanDiffView.tsx (new -- unified text diff), frontend/src/App.tsx (mod -- onTaskUpdated prop), tests/test_history_writer.py (mod -- 5 new plan_snapshot tests), tests/test_review_pipeline.py (mod -- 4 new plan_snapshot tests)
- **Sanity check result**: 882 tests passing (9 new). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-35 to Completed

## 2026-03-03 10:00 -- [T-P0-37] Fix sync crash on soft-deleted tasks + task creation feedback
- **What I did**: Added `UpsertResult` StrEnum and `upsert_task()` method to TaskManager that handles all sync scenarios (create, resurrect soft-deleted, update changed, no-op unchanged) without exceptions. Simplified `sync_project_tasks()` to use single `upsert_task()` call per parsed task, removing `existing_map` query and if/else create-or-update branches. Added `sync_error` field to `CreateTaskResponse` schema. Frontend: `onCreated` callbacks now pass `synced` boolean, `App.tsx` shows warning toast when sync fails. Added `*.md.bak` to `.gitignore`.
- **Deliverables**: src/task_manager.py (mod -- UpsertResult enum, upsert_task method), src/sync/tasks_parser.py (mod -- simplified sync loop using upsert_task), src/schemas.py (mod -- sync_error field), src/api.py (mod -- capture sync_error), frontend/src/types.ts (mod -- sync_error field), frontend/src/components/NewTaskModal.tsx (mod -- pass synced to onCreated), frontend/src/components/InlineTaskCreator.tsx (mod -- pass synced to onCreated), frontend/src/components/SwimLane.tsx (mod -- callback type), frontend/src/components/KanbanBoard.tsx (mod -- callback type), frontend/src/App.tsx (mod -- warning toast on sync failure), .gitignore (mod -- *.md.bak), tests/test_task_manager.py (mod -- 4 upsert tests), tests/test_tasks_parser.py (mod -- 2 sync resilience tests)
- **Sanity check result**: 906 tests passing (6 new). Ruff clean. Frontend TypeScript check clean.
- **Status**: [DONE]
- **Request**: Move T-P0-37 to Completed

## 2026-03-03 08:30 -- [T-P0-36] Structured plan generation via Claude CLI
- **What I did**: Feasibility assessment found no `--plan` CLI flag exists. Implemented structured plan generation using stable CLI features: `claude -p` + `--system-prompt` + `--json-schema` + `--add-dir` (codebase context) + `--permission-mode plan` (read-only). Added `generate_task_plan()` and `format_plan_as_text()` to enrichment.py with JSON schema for structured output (plan summary, implementation steps with file lists, acceptance criteria). POST `/api/tasks/{id}/generate-plan` endpoint auto-saves formatted plan to task.description. Frontend: "Generate Plan" button next to "Edit Plan" in ReviewPanel with indigo styling and loading state. Graceful degradation: CLI unavailable returns 503, parse failures fall back to raw text.
- **Deliverables**: src/enrichment.py (mod -- plan generation functions, JSON schema, system prompt, format_plan_as_text), src/schemas.py (mod -- GeneratePlanResponse), src/api.py (mod -- generate-plan endpoint with repo_path lookup), frontend/src/types.ts (mod -- GeneratePlanResult), frontend/src/api.ts (mod -- generatePlan function), frontend/src/components/ReviewPanel.tsx (mod -- Generate Plan button, handleGeneratePlan, generating state), tests/test_enrichment.py (mod -- 18 new plan generation tests)
- **Sanity check result**: 900 tests passing (18 new). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-36 to Completed

## 2026-03-03 10:00 -- [T-P0-40] Define Canonical ReviewLifecycleState enum in backend
- **What I did**: Created `ReviewLifecycleState(StrEnum)` with 7 values (NOT_STARTED, RUNNING, PARTIAL, FAILED, REJECTED_SINGLE, REJECTED_CONSENSUS, APPROVED) and `REVIEW_LIFECYCLE_TRANSITIONS` state machine map. Added `lifecycle_state` column to ReviewHistoryRow and `review_lifecycle_state` column to TaskRow (both auto-migrated via init_db). Exposed lifecycle state in API schemas (TaskResponse.review_lifecycle_state, ReviewHistoryEntry.lifecycle_state). Added `set_review_lifecycle_state()` method to TaskManager. Updated HistoryWriter.write_review() and get_reviews() to accept/return lifecycle_state. Updated frontend types (ReviewLifecycleState type, Task and ReviewHistoryEntry interfaces). Documented full state machine diagram and transition invariants in code comments.
- **Deliverables**: src/models.py (mod -- ReviewLifecycleState enum, REVIEW_LIFECYCLE_TRANSITIONS dict, Task.review_lifecycle_state field), src/db.py (mod -- lifecycle_state on ReviewHistoryRow, review_lifecycle_state on TaskRow, conversion helpers), src/schemas.py (mod -- lifecycle_state on ReviewHistoryEntry, review_lifecycle_state on TaskResponse), src/history_writer.py (mod -- lifecycle_state param on write_review, included in get_reviews), src/task_manager.py (mod -- set_review_lifecycle_state method), src/api.py (mod -- review_lifecycle_state in _task_to_response), frontend/src/types.ts (mod -- ReviewLifecycleState type, Task/ReviewHistoryEntry interfaces), tests/test_review_lifecycle_state.py (new -- 24 tests)
- **Sanity check result**: 930 tests passing (24 new). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-40 to Completed

## 2026-03-03 11:30 -- [T-P0-41] Refactor review_pipeline to emit ReviewLifecycleState
- **What I did**: Refactored `review_pipeline.py` to compute and emit `ReviewLifecycleState` at every stage. Added `_compute_lifecycle_state()` static method that maps review outcomes to terminal lifecycle states (APPROVED, REJECTED_SINGLE, REJECTED_CONSENSUS, PARTIAL). Changed single-reviewer rejection score from misleading `0.3` to clear `0.0`. Pipeline now passes lifecycle_state to `HistoryWriter.write_review()` for each entry (RUNNING for non-final, terminal state for final). Added `lifecycle_state` field to `ReviewState` model. Updated `_enqueue_review_pipeline` in api.py to set RUNNING at pipeline start and terminal state on completion. Updated `_set_review_failed` to set FAILED lifecycle state. Audited and corrected `_extract_cost_usd()` pricing: Opus 4.6 from $15/$75 to $5/$25, Haiku 4.5 from $0.80/$4 to $1/$5, added Opus 4.5 and 4.1 entries.
- **Deliverables**: src/review_pipeline.py (mod -- lifecycle state computation, pricing fix, score fix), src/models.py (mod -- lifecycle_state on ReviewState), src/api.py (mod -- lifecycle state in pipeline orchestration), tests/test_review_pipeline.py (mod -- 14 new tests, updated score assertions), tests/integration/test_review_flow.py (mod -- updated score assertion)
- **Sanity check result**: 944 tests passing (14 new). Ruff clean.
- **Status**: [DONE]
- **Request**: Move T-P0-41 to Completed

## 2026-03-03 12:30 -- [T-P0-42] Make ReviewPanel purely state-driven (no field-guessing)
- **What I did**: Refactored `ReviewPanel.tsx` to drive all display logic from `task.review_lifecycle_state` (backend ReviewLifecycleState) instead of guessing state from `review_status` + field combinations. Replaced `reviewStatus` variable with `lifecycleState` from `task.review_lifecycle_state`. Updated header badge (`lifecycleBadge()`) to show lifecycle-specific states (approved/rejected/partial/failed/reviewing) instead of generic done/running/failed. Changed idle display from "No review requested" to "Review not started" when lifecycle is `not_started`. Consensus bar now only renders on terminal-state entries (approved, rejected_consensus) -- intermediate (running) entries no longer show misleading percentages. Added "Single reviewer rejected" contextual label for `rejected_single` entries instead of raw 0% consensus. Entry verdict badge shows "not reviewed" when entry lifecycle is `not_started`. Cost display suppressed when `cost_usd` is 0. Re-review button logic driven by terminal lifecycle states instead of `review_status === "idle"`. Polling condition updated to use `review_lifecycle_state === "running"`.
- **Deliverables**: frontend/src/components/ReviewPanel.tsx (mod -- all display logic driven by ReviewLifecycleState, removed reviewStatus field-guessing)
- **Sanity check result**: 944 tests passing. Ruff clean. TypeScript compiles clean.
- **Status**: [DONE]
- **Request**: Move T-P0-42 to Completed

## 2026-03-03 13:00 -- [T-P0-43] Fix soft-delete sync with deleted_source tracking
- **What I did**: Added `deleted_source` column to TaskRow (`"user"` | `"sync"` | NULL) to distinguish how a task was deleted. `delete_task()` API sets `deleted_source="user"`. `upsert_task()` now returns `SKIPPED_DELETED` for user-deleted tasks (sync cannot resurrect them) but still allows resurrection for sync-deleted or legacy (NULL) tasks. Added `sync_mark_removed()` to TaskManager: marks tasks removed from TASKS.md as sync-deleted (`deleted_source="sync"`). Updated `sync_project_tasks()` to call `sync_mark_removed()` and count skipped tasks. Added `skipped` field to `SyncResult` and `SyncResponse`. All 4 SyncResponse constructions in api.py updated. Updated 3 existing tests to match new behavior.
- **Deliverables**: src/db.py (mod -- deleted_source column), src/task_manager.py (mod -- SKIPPED_DELETED, upsert_task source check, delete_task sets source, sync_mark_removed), src/sync/tasks_parser.py (mod -- skipped field, sync-delete call), src/schemas.py (mod -- skipped in SyncResponse), src/api.py (mod -- skipped passthrough), tests/test_deleted_source.py (new -- 13 tests), tests/test_task_manager.py (mod -- updated upsert tests), tests/test_tasks_parser.py (mod -- updated sync tests)
- **Sanity check result**: 958 tests passing (14 new). Ruff clean.
- **Status**: [DONE]
- **Request**: Move T-P0-43 to Completed

## 2026-03-03 14:00 -- [T-P0-44] Define plan validity model + enforce in review gate
- **What I did**: Added `is_plan_valid()` function and `PlanInvalidError` exception to TaskManager. Plan must be non-empty, non-whitespace, and >= 20 characters (after stripping). `update_status()` now enforces plan validity on BACKLOG->REVIEW transition when `review_gate_enabled=True`. API returns 428 with `gate_action: "plan_invalid"`. Frontend handles `plan_invalid` same as `review_required` (opens ReviewSubmitModal). ReviewSubmitModal now shows plan validity warning banner, character counter, and disables submit when plan is too short. Updated 3 existing tests with valid plan descriptions.
- **Deliverables**: src/task_manager.py (mod -- PlanInvalidError, is_plan_valid, MIN_PLAN_LENGTH, Layer 2 check), src/api.py (mod -- PlanInvalidError handler), frontend/src/App.tsx (mod -- plan_invalid gate_action handling), frontend/src/components/ReviewSubmitModal.tsx (mod -- plan validity UI), tests/test_plan_validity.py (new -- 20 tests), tests/test_review_gate.py (mod -- valid plan in 2 tests), tests/test_review_gate_bypass.py (mod -- valid plan in 1 test), tests/test_review_gate_ux.py (mod -- valid plan descriptions in 2 tests)
- **Sanity check result**: 978 tests passing (20 new). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-44 to Completed

## 2026-03-03 15:00 -- [T-P0-45] Generic default project selection via is_primary field
- **What I did**: Added `is_primary: bool` field (default False) to ProjectConfig, Project model, and API response schemas (ProjectResponse, ProjectDetailResponse). ProjectRegistry propagates is_primary from config to domain model. API `_project_to_response` and `get_project` detail endpoint include the field. Frontend `Project` interface updated. `loadData()` in App.tsx now defaults to primary project(s) on first load (no localStorage), falling back to first project if none marked primary. Set `is_primary: true` on helixos in orchestrator_config.yaml.
- **Deliverables**: src/config.py (mod -- is_primary on ProjectConfig, propagated in _build), src/models.py (mod -- is_primary on Project), src/schemas.py (mod -- is_primary on ProjectResponse, ProjectDetailResponse), src/api.py (mod -- is_primary in _project_to_response and get_project), frontend/src/types.ts (mod -- is_primary on Project), frontend/src/App.tsx (mod -- loadData default selection logic), orchestrator_config.yaml (mod -- is_primary: true on helixos), tests/test_is_primary.py (new -- 14 tests)
- **Sanity check result**: 992 tests passing (14 new). Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-45 to Completed

## 2026-03-03 16:00 -- [T-P0-38] Backward-drag confirmation dialog redesign
- **What I did**: Replaced browser `window.prompt()` with a styled BackwardDragModal component for backward-drag operations. Modal displays task title, task ID, source/target column with arrow visualization, consequence description, and optional reason input. Styled consistently with ReviewSubmitModal (same overlay, rounded container, header/body/footer layout). Amber color scheme signals caution. Enter key confirms, Escape cancels. Forward drags remain unaffected (no confirmation).
- **Deliverables**: frontend/src/components/BackwardDragModal.tsx (new -- styled modal component), frontend/src/components/KanbanBoard.tsx (mod -- replaced window.prompt with modal state)
- **Sanity check result**: 992 tests passing. Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-38 to Completed

## 2026-03-03 17:00 -- [T-P0-46] Unified MarkdownRenderer abstraction layer
- **What I did**: Created MarkdownRenderer.tsx component using react-markdown for unified markdown rendering across the app. Replaces raw `<pre>` text display with formatted markdown (headings, lists, code blocks, bold, italic, links, tables, blockquotes). Applied to: plan content in ReviewPanel (view mode), reviewer raw output (debug section), and edit-preview mode (new Edit/Preview tabs in inline plan editor). Font size toggle (S/M/L) with localStorage persistence. Unified scroll container with max-height and overflow. PlanDiffView kept as-is since it has specialized diff rendering that markdown doesn't apply to.
- **Deliverables**: frontend/src/components/MarkdownRenderer.tsx (new), frontend/src/components/ReviewPanel.tsx (mod -- uses MarkdownRenderer for plan and raw output, added edit-preview tabs), frontend/package.json (mod -- added react-markdown dependency)
- **Sanity check result**: 992 tests passing. Ruff clean. Frontend builds clean.
- **Status**: [DONE]
- **Request**: Move T-P0-46 to Completed

## 2026-03-03 18:00 -- [T-P0-47] No Plan badges + visual guidance in swim lanes
- **What I did**: Added amber "No Plan" badge on TaskCard when task.description is empty/whitespace. Added planless task count indicator ("X no plan") in BACKLOG and REVIEW column headers. Made Generate Plan button a prominent CTA (indigo-600, larger, shadow) for planless tasks; subtle styling for tasks that already have plans. Plan section auto-expands after successful generate-plan call so user sees new content immediately.
- **Deliverables**: frontend/src/components/TaskCard.tsx (mod -- No Plan badge), frontend/src/components/KanbanBoard.tsx (mod -- planless count in column headers), frontend/src/components/ReviewPanel.tsx (mod -- prominent Generate Plan button, auto-expand after generate)
- **Sanity check result**: 992 tests passing. Ruff clean. Frontend builds clean. TypeScript compiles clean.
- **Status**: [DONE]
- **Request**: Move T-P0-47 to Completed

## 2026-03-03 19:00 -- [T-P0-48] Running Jobs Panel -- click top-right "Running" to see active job list
- **What I did**: Created RunningJobsPanel component that displays all currently running tasks across projects. Each entry shows task ID, title, project name, elapsed timer (h:m:s), execution phase, and retry count. Made the "Running: N" header indicator clickable to toggle the panel (highlighted when active). Added "Running" as a third bottom panel tab alongside Execution Log and Review, showing running count badge. Panel auto-updates in real-time via existing SSE-driven task state (no polling needed). Empty state with descriptive message when no jobs running. Entries appear/disappear in real-time as tasks start/complete. Clicking a running task focuses it in the bottom panel.
- **Deliverables**: frontend/src/components/RunningJobsPanel.tsx (new -- panel component), frontend/src/App.tsx (mod -- clickable Running indicator, third bottom panel tab, RunningJobsPanel wiring)
- **Sanity check result**: 992 tests passing. Ruff clean. Frontend builds clean. TypeScript compiles clean.
- **Status**: [DONE]
- **Request**: Move T-P0-48 to Completed

## 2026-03-03 20:00 -- [T-P0-49] Fix inactivity timeout race condition -- kill vs. successful completion
- **What I did**: Fixed race condition where inactivity timeout fires but process already exited with returncode 0. In code_executor.py, added guard after kill sequence: if returncode == 0, override timeout/inactivity flags so result is reported as success with a warning log. In scheduler.py, added idempotent guard in success path (re-fetch task status before RUNNING->DONE transition; skip if already DONE) and state guard in failure path (verify task is still RUNNING before RUNNING->FAILED transition; skip if already transitioned). No FAILED->DONE transition added to state machine.
- **Deliverables**: src/executors/code_executor.py (mod -- returncode 0 override after timeout kill), src/scheduler.py (mod -- idempotent DONE guard + RUNNING state guard before FAILED), tests/test_code_executor.py (mod -- 2 regression tests: timeout+rc0=success, genuine timeout=failure), tests/test_scheduler.py (mod -- 2 regression tests: duplicate DONE idempotent, failure skips when not RUNNING)
- **Sanity check result**: 996 tests passing. Ruff clean.
- **Status**: [DONE]
- **Request**: Move T-P0-49 to Completed

## 2026-03-03 21:00 -- [T-P0-52] Immediate next-task dispatch after task completion
- **What I did**: Added immediate tick dispatch after task completion so the scheduler no longer waits up to 5s (TICK_INTERVAL) before picking the next QUEUED task. Added `asyncio.Lock` (`_tick_lock`) to `tick()` for re-entrancy safety so concurrent tick calls (periodic + immediate post-completion) do not race. In `_execute_task()` finally block, added `asyncio.create_task(self.tick())` to trigger immediate dispatch after cleanup.
- **Deliverables**: src/scheduler.py (mod -- _tick_lock in __init__, async with _tick_lock in tick(), asyncio.create_task(self.tick()) in _execute_task finally), tests/test_scheduler.py (mod -- 4 regression tests: immediate dispatch <1s, slot-freed dispatch, concurrent completions no duplicate, tick exception releases lock)
- **Sanity check result**: 1000 tests passing. Ruff clean.
- **Status**: [DONE]
- **Request**: Move T-P0-52 to Completed

## 2026-03-04 00:00 -- [BUGFIX] Fix blocking/hanging tests + scheduler teardown race
- **What I did**: Fixed two test hangs on Windows and a scheduler teardown race. (1) `test_timeout_kill_after_grace`: changed `grace_seconds=0` to `1` to avoid `asyncio.wait_for(coro, timeout=0)` edge case. (2) `test_inactivity_force_kill_after_grace`: replaced broken `wait_calls` counter with `proc.returncode` check. (3) Scheduler teardown race: fire-and-forget `asyncio.create_task(self.tick())` in `_execute_task` finally block races with DB disposal in test cleanup. Added `_background_ticks` set and `_stopped` flag to track/cancel background ticks. Added `_safe_tick()` wrapper to suppress exceptions after stop. Updated `stop()` to cancel all background ticks. Added `await scheduler.stop()` to 3 concurrency integration tests.
- **Deliverables**: src/scheduler.py (mod -- _background_ticks, _stopped, _safe_tick(), updated stop/start/_execute_task), tests/test_code_executor.py (mod -- 2 test fixes), tests/integration/test_concurrency.py (mod -- scheduler.stop() in 3 tests)
- **Sanity check result**: 1000 tests passing. Ruff clean. No hangs.
- **Status**: [DONE]
- **Request**: No TASKS.md change (bugfix, not a tracked task)

## 2026-03-04 01:00 -- [T-P0-53] Active process pulsing badges on task cards
- **What I did**: Formalized T-P0-53 completion. Code was implemented in previous session (commit 0fc66a7) as partial work: centralized `isActive` check (`status === "running" || review_status === "running"`) drives `animate-pulse` on TaskCard status badge. RUNNING cards now pulse like review cards. Pulse stops when task exits active state.
- **Deliverables**: frontend/src/components/TaskCard.tsx (mod -- isActive check + animate-pulse, done in prior commit)
- **Sanity check result**: Frontend builds clean. 1000 tests passing.
- **Status**: [DONE]
- **Request**: Move T-P0-53 to Completed

## 2026-03-04 02:00 -- [T-P0-50] Right-click context menu Edit (inline title/description editing)
- **What I did**: Added "Edit" option to TaskContextMenu. Created EditTaskModal component with title and description fields, auto-focus, Escape to close, backdrop click to close. Wired onEditTask callback through KanbanBoard -> SwimLane -> App.tsx. Save calls PATCH /api/tasks/{id} via existing updateTask() API. Updated task in local state + selectedTask on save. Toast notification on success.
- **Deliverables**: frontend/src/components/EditTaskModal.tsx (new), frontend/src/components/TaskContextMenu.tsx (mod), frontend/src/components/KanbanBoard.tsx (mod), frontend/src/components/SwimLane.tsx (mod), frontend/src/App.tsx (mod)
- **Sanity check result**: Frontend builds clean. 1000 tests passing.
- **Status**: [DONE]
- **Request**: Move T-P0-50 to Completed
