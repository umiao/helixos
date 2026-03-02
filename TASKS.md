# Task Backlog

> **Convention**: Pick tasks from top of Active (highest priority first).
> Move to In Progress when starting. Move to Completed when done.
> PRD reference: helixos_prd_v0.3.md (single source of truth for architecture)

## In Progress
<!-- Only ONE task here at a time. Focus. -->

## Active Tasks

### P0 -- Must Have (core functionality)

#### T-P0-1: Project scaffold (FastAPI + React + SQLite)
- **Priority**: P0
- **Complexity**: S (1 session)
- **Depends on**: Nothing
- **Acceptance Criteria**:
  - [ ] `pyproject.toml` updated: name="helixos", dependencies added (fastapi, uvicorn[standard], sqlalchemy[asyncio], aiosqlite, pydantic>=2.0, anthropic, python-dotenv, pyyaml)
  - [ ] `requirements.txt` updated with all deps including dev (ruff, pytest, pytest-asyncio, httpx, mypy)
  - [ ] `pyproject.toml` adds `asyncio_mode = "auto"` under `[tool.pytest.ini_options]`
  - [ ] `src/executors/__init__.py` created (empty package)
  - [ ] `src/sync/__init__.py` created (empty package)
  - [ ] `frontend/` initialized: Vite + React + TypeScript + Tailwind CSS (react-ts template)
  - [ ] `frontend/vite.config.ts` configured with proxy: `/api` -> `http://localhost:8000`
  - [ ] `frontend/tailwind.config.js` configured for `./src/**/*.{ts,tsx}`
  - [ ] `.gitignore` updated: add `frontend/node_modules/`, `frontend/dist/`, `*.db`
  - [ ] `orchestrator_config.yaml` created with full schema from PRD Section 6.2 (placeholder paths)
  - [ ] `contracts/` directory created with `.gitkeep`
  - [ ] `scripts/start.ps1` created (Windows: build frontend + launch uvicorn)
  - [ ] Smoke test passes: `pytest tests/test_smoke.py`
  - [ ] `ruff check src/` passes clean
  - [ ] No emoji anywhere
- **Files**: `pyproject.toml` (mod), `requirements.txt` (mod), `src/executors/__init__.py` (new), `src/sync/__init__.py` (new), `frontend/` (new tree), `orchestrator_config.yaml` (new), `contracts/.gitkeep` (new), `scripts/start.ps1` (new), `.gitignore` (mod)
- **Scope boundary**:
  - IN: Directory structure, dependency declarations, Vite scaffold, config skeleton, dev proxy
  - OUT: No Python module implementations. No React components beyond Vite defaults. No .env file.

---

#### T-P0-11: Unified .env loader + env injection
- **Priority**: P0
- **Complexity**: S (1 session)
- **Depends on**: T-P0-1
- **Acceptance Criteria**:
  - [ ] `src/env_loader.py`: `class EnvLoader` with `__init__(env_path: Path)` loading .env via python-dotenv
  - [ ] `get_project_env(project: Project) -> dict[str, str]` returns only keys listed in `project.env_keys`
  - [ ] `get_all() -> dict[str, str]` returns all loaded vars
  - [ ] `validate_project_keys(project: Project) -> list[str]` returns missing key names
  - [ ] If .env file missing: log warning, return empty dict (no crash)
  - [ ] Warn if `ANTHROPIC_API_KEY` is missing (needed for review pipeline)
  - [ ] All file reads use `encoding="utf-8"`
  - [ ] No hardcoded secrets in code or tests
  - [ ] `tests/test_env_loader.py` with temp .env files, missing keys, missing file
  - [ ] ruff clean, no emoji
- **Files**: `src/env_loader.py` (new), `tests/test_env_loader.py` (new)
- **Scope boundary**:
  - IN: .env file loading, per-project key filtering, key validation
  - OUT: No .env file creation. No os.environ mutation (returns dicts only). No executor integration.

---

#### T-P0-2: Data model + TaskManager + database layer
- **Priority**: P0
- **Complexity**: M (2 sessions)
- **Depends on**: T-P0-1
- **Acceptance Criteria**:
  - [ ] `src/models.py`: All Pydantic models from PRD Section 6.1: `TaskStatus` (8 values), `ExecutorType` (3 values), `Project`, `Task`, `ReviewState`, `LLMReview`, `ExecutionState`, `Dependency`
  - [ ] Every model has type hints, docstring, `model_config` with `from_attributes = True`
  - [ ] `src/db.py`: SQLAlchemy 2.0 async engine + session factory (aiosqlite)
  - [ ] `src/db.py`: ORM table models with column types, foreign keys, indexes
  - [ ] `src/db.py`: `async def init_db()` creates all tables via `metadata.create_all()`
  - [ ] `src/db.py`: `async def get_session()` async context manager yielding `AsyncSession`
  - [ ] DB path configurable (default `~/.helixos/state.db`), parent dir auto-created
  - [ ] `src/task_manager.py`: `TaskManager` class with CRUD + state machine:
    - `create_task`, `get_task`, `list_tasks` (filterable by project/status)
    - `update_status` (validates transitions per PRD Section 5.3 state machine)
    - `get_ready_tasks` (queued + deps met + project not busy)
    - `count_running_by_project`
    - `mark_running_as_failed` (startup recovery helper)
  - [ ] `update_status()` raises `ValueError` on illegal state transitions
  - [ ] All file I/O uses `encoding="utf-8"`
  - [ ] `tests/test_models.py`: Pydantic validation, serialization round-trip
  - [ ] `tests/test_db.py`: DB init, CRUD operations (in-memory SQLite)
  - [ ] `tests/test_task_manager.py`: state machine transitions, edge cases
  - [ ] ruff clean, no emoji
- **Files**: `src/models.py` (new), `src/db.py` (new), `src/task_manager.py` (new), `tests/test_models.py` (new), `tests/test_db.py` (new), `tests/test_task_manager.py` (new), `tests/conftest.py` (mod -- add async DB fixture)
- **Scope boundary**:
  - IN: Pydantic models, SQLAlchemy ORM, async DB, TaskManager CRUD + state machine
  - OUT: No API layer. No YAML config loading. No TASKS.md parsing.

---

#### T-P0-3: Project registry + YAML config loader
- **Priority**: P0
- **Complexity**: S (1 session)
- **Depends on**: T-P0-2
- **Acceptance Criteria**:
  - [ ] `src/config.py`: Pydantic settings models: `OrchestratorConfig`, `ProjectConfig`, `GitConfig`, `ReviewerConfig`, `DependencyConfig` -- matching PRD Section 6.2
  - [ ] `load_config(path: Path) -> OrchestratorConfig` parses YAML with validation
  - [ ] `class ProjectRegistry`: `get_project(id)`, `list_projects()`, `get_project_config(id)`
  - [ ] Converts `ProjectConfig` (YAML) -> `Project` (Pydantic model from T-P0-2)
  - [ ] Path fields expand `~` via `Path.expanduser()`
  - [ ] Missing repo_path: warning (not error -- repos may not be cloned)
  - [ ] All file reads use `encoding="utf-8"`
  - [ ] `tests/test_config.py`: YAML loading, validation, path expansion, bad YAML
  - [ ] ruff clean, no emoji
- **Files**: `src/config.py` (new), `tests/test_config.py` (new), `orchestrator_config.yaml` (mod)
- **Scope boundary**:
  - IN: YAML parsing, config validation, ProjectRegistry
  - OUT: No .env loading. No TASKS.md parsing. No runtime state.

---

#### T-P0-4: TASKS.md parser (one-way sync)
- **Priority**: P0
- **Complexity**: S (1 session)
- **Depends on**: T-P0-3
- **Acceptance Criteria**:
  - [ ] `src/sync/tasks_parser.py`: `class TasksParser` with `parse(content, project_id) -> list[ParsedTask]`
  - [ ] `ParsedTask` dataclass: `local_task_id`, `title`, `status`, `description` (opaque blob)
  - [ ] Strict regex: only matches `T-P\d+-\d+` pattern (other projects must adopt this convention)
  - [ ] Status inferred from section headers: "In Progress" -> RUNNING, "Active Tasks" -> BACKLOG, "Completed"/"Done" -> DONE, "Blocked" -> BLOCKED
  - [ ] Section header mapping configurable via `status_sections` in project config (with defaults)
  - [ ] Edge cases: tasks without IDs (skip+warn), duplicate IDs (last wins+warn), empty sections
  - [ ] `sync_project_tasks(project_id, task_manager, registry) -> SyncResult`: reads TASKS.md, parses, upserts DB
  - [ ] `SyncResult`: `added`, `updated`, `unchanged`, `warnings`
  - [ ] Synced tasks enter DB as QUEUED (not BACKLOG) per PRD Section 12.3 review-skip rule
  - [ ] Tasks done in TASKS.md -> DB updated to DONE. Tasks removed from TASKS.md -> stay in DB.
  - [ ] All file reads use `encoding="utf-8"`
  - [ ] `tests/test_tasks_parser.py` with sample TASKS.md fixtures
  - [ ] ruff clean, no emoji
- **Files**: `src/sync/tasks_parser.py` (new), `tests/test_tasks_parser.py` (new), `tests/fixtures/` (new -- sample TASKS.md files)
- **Scope boundary**:
  - IN: Markdown parsing, regex extraction, section-to-status mapping, DB upsert
  - OUT: No API endpoint (T-P0-10). No cross-project deps. Parser never writes back to TASKS.md.

---

#### T-P0-8a: Dashboard Kanban -- static layout + TaskCard
- **Priority**: P0
- **Complexity**: S (1 session)
- **Depends on**: T-P0-1
- **Acceptance Criteria**:
  - [ ] `frontend/src/App.tsx`: Layout with header bar (title, "Sync All" placeholder, running count placeholder)
  - [ ] `frontend/src/components/KanbanBoard.tsx`: 5 columns: BACKLOG, REVIEW, QUEUED, RUNNING, DONE
  - [ ] `frontend/src/components/TaskCard.tsx`: project ID, task ID, title, status badge, dependency indicator
  - [ ] `frontend/src/types.ts`: TypeScript interfaces matching backend Pydantic models
  - [ ] `frontend/src/api.ts`: API client stub with typed functions (mock data for now)
  - [ ] Board renders mock data (3-5 tasks) to verify layout
  - [ ] Filter bar UI: project dropdown, status dropdown, search input (UI only, no logic)
  - [ ] Tailwind CSS: clean cards, column headers with counts, responsive
  - [ ] `npm run build` succeeds with no TypeScript errors
  - [ ] No emoji in code or displayed text
- **Files**: `frontend/src/App.tsx` (mod), `frontend/src/components/KanbanBoard.tsx` (new), `frontend/src/components/TaskCard.tsx` (new), `frontend/src/types.ts` (new), `frontend/src/api.ts` (new)
- **Scope boundary**:
  - IN: Static layout, component structure, TypeScript types, mock data, Tailwind styling
  - OUT: No drag-drop (T-P0-8b). No real API calls (T-P0-8b). No ExecutionLog/ReviewPanel (T-P0-8c).

---

#### T-P0-5: CodeExecutor (subprocess + timeout + streaming)
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-2, T-P0-11
- **Acceptance Criteria**:
  - [ ] `src/executors/base.py`: `ExecutorResult` model + `BaseExecutor` ABC per PRD Section 7.1
  - [ ] `BaseExecutor.execute(task, project, env, on_log) -> ExecutorResult`
  - [ ] `BaseExecutor.cancel() -> None`
  - [ ] `src/executors/code_executor.py`: `CodeExecutor(BaseExecutor)` per PRD Section 7.2
  - [ ] Spawns `claude -p "..." --allowedTools ... --output-format json` via `asyncio.create_subprocess_exec`
  - [ ] `cwd=project.repo_path`, env merged with `os.environ` + injected env dict
  - [ ] stdout streamed line-by-line via `on_log` callback
  - [ ] Timeout: `asyncio.timeout(session_timeout_minutes * 60)`
  - [ ] Windows timeout: `proc.terminate()` only (= `TerminateProcess`, immediate). Grace wait still attempted but process is already dead.
  - [ ] `cancel()` calls `proc.terminate()` on stored subprocess reference
  - [ ] `_build_prompt(task)` generates one-shot prompt per PRD Section 7.2
  - [ ] Last 100 log lines kept in `ExecutorResult.log_lines`
  - [ ] All string decoding uses `encoding="utf-8"`
  - [ ] `tests/test_code_executor.py`: mock subprocess for success, failure, timeout, cancel, log streaming
  - [ ] ruff clean, no emoji
- **Files**: `src/executors/base.py` (new), `src/executors/code_executor.py` (new), `tests/test_code_executor.py` (new)
- **Scope boundary**:
  - IN: CodeExecutor, subprocess management, timeout/kill, log streaming, prompt building
  - OUT: No AgentExecutor (Phase 2). No ScheduledExecutor (Phase 2). No scheduler integration. No EventBus.

---

#### T-P0-7: Review pipeline (Anthropic-only, opt-in, async)
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-2
- **Acceptance Criteria**:
  - [ ] `src/review_pipeline.py`: `class ReviewPipeline` per PRD Section 9
  - [ ] `__init__(config, anthropic_client)` -- client injected, not created here
  - [ ] `review_task(task, plan_content, on_progress) -> ReviewState`
  - [ ] 1 required reviewer (claude-sonnet-4-5, feasibility) + 1 optional adversarial (M/L tasks only)
  - [ ] `_call_reviewer` uses Anthropic Messages API
  - [ ] `_build_review_prompt(focus)` generates focus-area system prompt
  - [ ] `_parse_review(response, reviewer) -> LLMReview`
  - [ ] `_synthesize(reviews, plan) -> SynthesisResult` (only when >1 review)
  - [ ] Scoring: single approve=1.0, single reject=0.3, multi=synthesized
  - [ ] `human_decision_needed = True` when score < 0.8 threshold
  - [ ] `on_progress: Callable[[int, int], None]` -- (completed, total) rounds
  - [ ] `tests/test_review_pipeline.py`: mock Anthropic client -- approve, reject, disagree, progress callback
  - [ ] ruff clean, no emoji, no hardcoded API keys
- **Files**: `src/review_pipeline.py` (new), `tests/test_review_pipeline.py` (new)
- **Scope boundary**:
  - IN: Anthropic review calls, prompt engineering, synthesis, consensus scoring
  - OUT: No HTTP endpoint (T-P0-10). No EventBus wiring (T-P0-10 does that). No multi-LLM (Phase 2).

---

#### T-P0-6a: Scheduler core (EventBus + tick loop + concurrency)
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-5, T-P0-4
- **Acceptance Criteria**:
  - [ ] `src/events.py`: `class EventBus` with:
    - `emit(event_type, task_id, data)` -- stores event, notifies subscribers
    - `subscribe() -> AsyncGenerator[Event, None]` -- yields events (for SSE)
    - `Event` dataclass: `type`, `task_id`, `data`, `timestamp`
    - `asyncio.Queue` per subscriber, bounded (max 1000, drop oldest)
  - [ ] `src/scheduler.py`: `class Scheduler` with:
    - `__init__(config, task_manager, registry, env_loader, event_bus)`
    - `tick()` -- main scheduling loop iteration (called every 5s)
    - `_project_is_busy(project_id) -> bool` -- per-project concurrency
    - `available_slots` property: `min(global_limit, active_projects) - len(running)`
    - `_deps_fulfilled(task) -> bool` -- all upstream deps DONE
    - `_get_executor(executor_type) -> BaseExecutor` (returns CodeExecutor for MVP)
    - `self.running: dict[str, asyncio.Task]` tracking active executions
  - [ ] On task success: status -> DONE, emit `status_change` event
  - [ ] On task failure: status -> FAILED, emit `alert` event
  - [ ] Events emitted: `log`, `status_change`, `alert`
  - [ ] Scheduler tick loop launched via `asyncio.create_task` with 5s interval
  - [ ] `tests/test_events.py`: EventBus emit, subscribe, bounded queue, multi-subscriber
  - [ ] `tests/test_scheduler.py`: mock executor -- tick dispatch, concurrency limits, dep blocking
  - [ ] ruff clean, no emoji
- **Files**: `src/events.py` (new), `src/scheduler.py` (new), `tests/test_events.py` (new), `tests/test_scheduler.py` (new)
- **Scope boundary**:
  - IN: EventBus, scheduling loop, concurrency control, dependency checking, basic success/fail handling
  - OUT: No retry/backoff (T-P0-6b). No startup recovery (T-P0-6b). No cancel (T-P0-6b). No git commit (T-P0-12). No SSE endpoint (T-P0-9).

---

#### T-P0-6b: Scheduler hardening (retry + recovery + cancel)
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-6a
- **Acceptance Criteria**:
  - [ ] `src/scheduler.py` extended with `_run_with_retry(executor, task, project)`:
    - Exponential backoff: 30s, 60s, 120s (max 3 retries) per PRD Section 8
    - On max retries exhausted: status -> BLOCKED, emit `alert`
    - Emits `log` event for each retry attempt
  - [ ] `startup_recovery()`: marks all RUNNING tasks as FAILED on boot (PRD H1)
    - Emits `alert` for each recovered task
    - Logs warning with count of orphaned tasks
  - [ ] `cancel_task(task_id) -> bool`: cancels running execution
    - Calls executor.cancel() on the running task
    - Updates status to FAILED
    - Removes from self.running
  - [ ] `_auto_commit_hook` placeholder: called after successful execution (no-op until T-P0-12 wires it)
  - [ ] `tests/test_scheduler.py` extended: retry+backoff (mock sleep), max retries->BLOCKED, startup recovery, cancel
  - [ ] ruff clean, no emoji
- **Files**: `src/scheduler.py` (mod), `tests/test_scheduler.py` (mod)
- **Scope boundary**:
  - IN: Retry logic, exponential backoff, startup crash recovery, task cancellation, auto-commit hook
  - OUT: No git operations (T-P0-12). No new EventBus features. No API endpoints.

---

#### T-P0-12: Git auto-commit with staged safety check
- **Priority**: P0
- **Complexity**: S (1 session)
- **Depends on**: T-P0-6b
- **Acceptance Criteria**:
  - [ ] `src/git_ops.py`: `class GitOps` with:
    - `auto_commit(project, task, config, event_bus) -> bool`
    - Stages all changes: `git add -A` in project repo
    - Safety: count staged files via `git diff --cached --numstat`
    - If staged > `config.max_files` (default 50): unstage, emit alert, return False
    - Commit with message from `config.commit_message_template`
    - No changes staged: return True silently
    - `check_repo_clean(repo_path) -> bool` utility
  - [ ] All subprocess calls decode with `encoding="utf-8"`
  - [ ] Wire into Scheduler: `_auto_commit_hook` calls `GitOps.auto_commit()`
  - [ ] `tests/test_git_ops.py`: temp git repo -- commit, safety abort, no-changes, message format
  - [ ] ruff clean, no emoji
- **Files**: `src/git_ops.py` (new), `src/scheduler.py` (mod -- wire hook), `tests/test_git_ops.py` (new)
- **Scope boundary**:
  - IN: Git stage, safety check, commit, scheduler integration
  - OUT: No push. No branch management. No .gitignore editing.

---

#### T-P0-9: SSE event stream endpoint
- **Priority**: P0
- **Complexity**: S (1 session)
- **Depends on**: T-P0-6a
- **Acceptance Criteria**:
  - [ ] `src/events.py` extended: `format_sse(event) -> str` formats as `data: {json}\n\n`
  - [ ] SSE endpoint: `GET /api/events` returns `StreamingResponse(media_type="text/event-stream")`
  - [ ] Subscribes to EventBus, yields formatted SSE events
  - [ ] Keepalive: `: keepalive\n\n` every 15 seconds
  - [ ] Client disconnect: unsubscribe from EventBus gracefully
  - [ ] Event types: `log`, `status_change`, `review_progress`, `alert`
  - [ ] Each event JSON: `{type, task_id, data, timestamp}`
  - [ ] `tests/test_sse.py`: httpx AsyncClient stream -- event delivery, keepalive, disconnect cleanup
  - [ ] ruff clean, no emoji
- **Files**: `src/events.py` (mod), `tests/test_sse.py` (new)
- **Scope boundary**:
  - IN: SSE HTTP endpoint, event formatting, keepalive, disconnect handling
  - OUT: No EventBus creation (T-P0-6a). No frontend consumer (T-P0-8c).

---

#### T-P0-10: API endpoints (CRUD + sync + execute + review + lifespan)
- **Priority**: P0
- **Complexity**: L (2-3 sessions)
- **Depends on**: T-P0-6b, T-P0-7, T-P0-4
- **Acceptance Criteria**:
  - [ ] `src/api.py`: `app = FastAPI(title="HelixOS", version="0.1.0")`
  - [ ] Lifespan handler: init DB, load config, create all service objects, startup_recovery, start scheduler tick, shutdown cleanup
  - [ ] Static mount: `frontend/dist/` served at `/` (after API routes)
  - [ ] CORS middleware for `localhost:5173` (Vite dev)
  - [ ] All PRD Section 10 endpoints:
    - `GET /api/projects` -- list projects
    - `GET /api/projects/{id}` -- project + tasks
    - `GET /api/tasks` -- all tasks (filterable: project_id, status)
    - `GET /api/tasks/{id}` -- task detail
    - `PATCH /api/tasks/{id}/status` -- transition (validates state machine)
    - `POST /api/tasks/{id}/review` -- trigger review (202, async)
    - `POST /api/tasks/{id}/review/decide` -- submit human decision
    - `POST /api/tasks/{id}/execute` -- force-execute
    - `POST /api/tasks/{id}/retry` -- reset retry count, move to QUEUED
    - `POST /api/tasks/{id}/cancel` -- cancel running
    - `POST /api/projects/{id}/sync` -- re-parse TASKS.md
    - `POST /api/sync-all` -- re-parse all
    - `GET /api/dashboard/summary` -- aggregate stats
    - `GET /api/events` -- SSE (wired from T-P0-9)
  - [ ] Pydantic request/response schemas for all endpoints
  - [ ] HTTP status codes: 200, 201, 202, 400, 404, 409, 500
  - [ ] Error responses: `{"detail": "message"}`
  - [ ] `tests/test_api.py`: httpx AsyncClient -- each endpoint happy path, bad transitions, 404s, 202 review
  - [ ] ruff clean, no emoji
- **Files**: `src/api.py` (new), `src/schemas.py` (new, optional), `tests/test_api.py` (new), `tests/conftest.py` (mod)
- **Scope boundary**:
  - IN: All HTTP endpoints, app creation, lifespan wiring, CORS, static mount, validation
  - OUT: No frontend. No business logic (delegates to TaskManager, Scheduler, ReviewPipeline).

---

#### T-P0-8b: Dashboard Kanban -- drag-drop + API integration
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-8a, T-P0-10
- **Acceptance Criteria**:
  - [ ] Install `@dnd-kit/core` for drag-drop
  - [ ] Cards draggable between columns; on drop calls `PATCH /api/tasks/{id}/status`
  - [ ] Invalid transitions: show error toast
  - [ ] `frontend/src/api.ts` updated: real fetch calls (remove mock data)
  - [ ] Filter bar functional: project, status, search
  - [ ] "Sync All" button calls `POST /api/sync-all`, refreshes board
  - [ ] Error handling: toast on API errors
  - [ ] Loading states: skeleton cards while fetching
  - [ ] `npm run build` succeeds
  - [ ] No emoji
- **Files**: `frontend/src/components/KanbanBoard.tsx` (mod), `frontend/src/components/TaskCard.tsx` (mod), `frontend/src/api.ts` (mod), `frontend/package.json` (mod)
- **Scope boundary**:
  - IN: Drag-drop, real API calls, filtering, sync buttons, error/loading states
  - OUT: No ExecutionLog (T-P0-8c). No ReviewPanel (T-P0-8c). No SSE.

---

#### T-P0-8c: Dashboard -- ExecutionLog + ReviewPanel + SSE
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-8a, T-P0-9
- **Acceptance Criteria**:
  - [ ] `frontend/src/hooks/useSSE.ts`: connects to `GET /api/events`, auto-reconnects (backoff: 1s, 2s, 4s, max 30s), provides `connected` boolean
  - [ ] `frontend/src/components/ExecutionLog.tsx`: scrollable log panel, filterable by task, auto-scroll with scroll-lock, timestamps, max 500 lines
  - [ ] `frontend/src/components/ReviewPanel.tsx`: review progress, verdicts, consensus score, decision buttons when `human_decision_needed`
  - [ ] SSE `status_change` events auto-update card positions (no refresh needed)
  - [ ] SSE `alert` events show as toast notifications
  - [ ] Running cards show elapsed time (client-side timer)
  - [ ] Connection status indicator in header (connected/disconnected)
  - [ ] `npm run build` succeeds
  - [ ] No emoji
- **Files**: `frontend/src/hooks/useSSE.ts` (new), `frontend/src/components/ExecutionLog.tsx` (new), `frontend/src/components/ReviewPanel.tsx` (new), `frontend/src/components/KanbanBoard.tsx` (mod), `frontend/src/App.tsx` (mod)
- **Scope boundary**:
  - IN: SSE hook, ExecutionLog, ReviewPanel, real-time updates, review decision UI
  - OUT: No task detail expansion panel. No settings page.

---

#### T-P0-13: Integration testing (end-to-end)
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-10, T-P0-12
- **Acceptance Criteria**:
  - [ ] `tests/integration/conftest.py`: fixtures -- temp project repo, temp config, temp .env, in-memory SQLite, full app instance
  - [ ] `tests/integration/test_sync_to_execute.py`: sync -> QUEUED -> RUNNING -> DONE -> git commit (mock claude CLI)
  - [ ] `tests/integration/test_review_flow.py`: BACKLOG -> review -> REVIEW_NEEDS_HUMAN -> decide -> QUEUED (mock Anthropic API)
  - [ ] `tests/integration/test_failure_retry.py`: fail -> retry with backoff -> BLOCKED after max retries (mock sleep)
  - [ ] `tests/integration/test_concurrency.py`: 5 tasks across 2 projects, verify per-project + global limits
  - [ ] `tests/integration/test_startup_recovery.py`: insert RUNNING tasks -> startup_recovery -> FAILED
  - [ ] All tests marked `@pytest.mark.integration`
  - [ ] `pytest tests/integration/ -v` passes
  - [ ] ruff clean, no emoji
- **Files**: `tests/integration/__init__.py` (new), `tests/integration/conftest.py` (new), `tests/integration/test_*.py` (5 new)
- **Scope boundary**:
  - IN: Full backend lifecycle with mocked externals (claude CLI, Anthropic API)
  - OUT: No frontend testing (Cypress/Playwright is Phase 2). No real API calls.

### P1 -- Should Have (important features)

<!-- Phase 2 backlog: AgentExecutor, ScheduledExecutor, multi-LLM review, failure auto-diagnosis -->

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
```

---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks
<!-- Move finished tasks here with [x] and completion date -->
