# HelixOS

An autonomous multi-project orchestrator that manages software development tasks
across multiple repositories. HelixOS parses TASKS.md backlogs, schedules execution
via the Claude CLI, runs LLM-based code reviews, and serves a real-time Kanban
dashboard -- all from a single server.

---

## Features

- **Multi-project management** -- register multiple git repos, each with its own
  TASKS.md backlog, concurrency limits, and configuration
- **Autonomous task execution** -- scheduler picks QUEUED tasks, spawns Claude CLI
  sessions, handles retries with exponential backoff, and auto-commits on success
- **LLM review pipeline** -- multi-reviewer code review via Claude CLI with
  configurable focus areas (feasibility, adversarial), consensus scoring, and
  optional human override
- **Real-time Kanban dashboard** -- React + TypeScript UI with drag-drop task cards,
  per-project swim lanes, SSE live updates, execution logs, and review history
- **Dev server management** -- launch/stop project dev servers with automatic port
  assignment, process tracking, and orphan cleanup
- **Project import** -- add new projects via the dashboard with directory browsing,
  git/TASKS.md validation, and auto-configuration

---

## Architecture

```
+---------------------------------------------------------+
|                    Dashboard (React)                     |
|  Kanban | Swim Lanes | Exec Logs | Reviews | Import     |
+--------------------------+------------------------------+
                           | REST + SSE
+--------------------------v------------------------------+
|                    FastAPI Server                        |
|                                                         |
|  +------------+  +-----------+  +-------------------+   |
|  | Scheduler  |  | Review    |  | Process Manager   |   |
|  | tick loop  |  | Pipeline  |  | launch/stop devs  |   |
|  | retry +    |  | multi-LLM |  | port registry     |   |
|  | concurrency|  | consensus |  | orphan cleanup    |   |
|  +-----+------+  +-----+-----+  +-------------------+   |
|        |               |                                 |
|  +-----v------+  +-----v-----+  +-------------------+   |
|  | Executor   |  | Claude CLI|  | TASKS.md Parser   |   |
|  | subprocess |  | -p flag   |  | one-way sync      |   |
|  | streaming  |  | JSON out  |  | to SQLite DB      |   |
|  +------------+  +-----------+  +-------------------+   |
|                                                         |
|  +------------+  +-----------+  +-------------------+   |
|  | EventBus   |  | History   |  | Git Ops           |   |
|  | pub/sub    |  | Writer    |  | auto-commit       |   |
|  | SSE stream |  | exec logs |  | staged safety     |   |
|  +------------+  | reviews   |  +-------------------+   |
|                  +-----------+                           |
+---------------------------------------------------------+
                           |
              +------------v-----------+
              |   SQLite (async)       |
              |   tasks, deps, logs,   |
              |   reviews, ports       |
              +------------------------+
```

### Data flow

1. **Sync**: TASKS.md files are parsed and upserted into SQLite (one-way, TASKS.md
   is the source of truth)
2. **Schedule**: The tick loop (5s interval) finds QUEUED tasks with met dependencies
   and available concurrency slots, then dispatches them to executors
3. **Execute**: CodeExecutor spawns `claude -p "..."` as a subprocess with stdout
   streaming, timeout enforcement, and cancellation support
4. **Review**: Completed tasks optionally go through multi-reviewer LLM review with
   configurable consensus threshold and human decision points
5. **Commit**: On success, GitOps runs `git add -A` + commit with staged file count
   safety checks
6. **Stream**: Every state change emits events through the EventBus, which are
   pushed to the dashboard via SSE

### Task state machine

```
BACKLOG --> QUEUED --> RUNNING --> REVIEW --> DONE
              ^         |           |
              |         v           v
              +------ FAILED --> BLOCKED
```

Tasks transition through states with server-side validation. The scheduler manages
QUEUED-to-RUNNING transitions. Retries follow exponential backoff (30s, 60s, 120s)
before marking a task as BLOCKED.

---

## Tech Stack

| Layer    | Technology                                          |
|----------|-----------------------------------------------------|
| Backend  | Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), aiosqlite |
| Frontend | React 19, TypeScript 5.9, Vite 7, Tailwind CSS 4   |
| Database | SQLite (async via aiosqlite)                        |
| Executor | Claude Code CLI (`claude -p`)                       |
| Drag-drop| @dnd-kit/core                                       |
| Linting  | ruff, ESLint                                        |
| Testing  | pytest (542 tests), pytest-asyncio                  |

---

## Quick Start

See [QUICKSTART.md](QUICKSTART.md) for detailed installation and configuration.
The essentials:

```bash
# 1. Clone and set up Python
git clone <repo-url> helixos
cd helixos
python -m venv .venv
```

Activate the virtual environment:

**bash:**
```bash
source .venv/bin/activate
```

**PowerShell:**
```powershell
.venv\Scripts\Activate.ps1
```

Then install and build:

```bash
pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
```

Start the server:

```bash
uvicorn src.api:app --host 127.0.0.1 --port 8000
```

Open http://localhost:8000 to see the dashboard. Sync tasks with:

```bash
curl -X POST http://localhost:8000/api/sync-all
```

---

## Configuration

All configuration lives in `orchestrator_config.yaml`:

```yaml
orchestrator:
  global_concurrency_limit: 3        # max concurrent tasks across all projects
  per_project_concurrency: 1         # max concurrent tasks per project
  review_consensus_threshold: 0.8    # score needed for auto-approve
  session_timeout_minutes: 60        # max runtime per task
  state_db_path: "~/.helixos/state.db"
  unified_env_path: "~/.helixos/.env"

projects:
  P0:
    name: "My Project"
    repo_path: "~/path/to/project"
    executor_type: "code"
    tasks_file: "TASKS.md"
    max_concurrency: 1

git:
  auto_commit: true
  commit_message_template: "[helixos] {project}: {task_id} {task_title}"
  staged_safety_check:
    max_files: 50

review_pipeline:
  reviewers:
    - model: "claude-sonnet-4-5"
      focus: "feasibility_and_edge_cases"
      required: true
    - model: "claude-sonnet-4-5"
      focus: "adversarial_red_team"
      required: false
```

Projects can also be added via the dashboard's Import Project modal, which validates
the directory, detects git repos, and writes the YAML config automatically.

---

## Backend Modules

| Module | File | Purpose |
|--------|------|---------|
| **API** | `src/api.py` | FastAPI app with 20+ endpoints, CORS, static serving, lifespan |
| **Scheduler** | `src/scheduler.py` | Tick loop, concurrency control, dependency resolution, retry logic |
| **Task Manager** | `src/task_manager.py` | CRUD + state machine with validated transitions |
| **Executor** | `src/executors/code_executor.py` | Claude CLI subprocess with streaming, timeout, cancel |
| **Review Pipeline** | `src/review_pipeline.py` | Multi-reviewer LLM review, consensus scoring, human override |
| **History Writer** | `src/history_writer.py` | Persistent execution logs and review history (DB-first) |
| **EventBus + SSE** | `src/events.py` | Pub/sub event system with SSE streaming endpoint |
| **TASKS.md Parser** | `src/sync/tasks_parser.py` | Regex parser, section-to-status mapping, DB upsert |
| **Tasks Writer** | `src/tasks_writer.py` | Append tasks to TASKS.md with filelock and backup |
| **Config** | `src/config.py` | YAML loader, Pydantic settings, ProjectRegistry |
| **Config Writer** | `src/config_writer.py` | Add projects to YAML config (ruamel.yaml, comment-preserving) |
| **Database** | `src/db.py` | SQLAlchemy 2.0 async ORM (tasks, deps, logs, reviews) |
| **Models** | `src/models.py` | Pydantic domain models (TaskStatus, Project, Task, etc.) |
| **Schemas** | `src/schemas.py` | API request/response Pydantic schemas |
| **Process Manager** | `src/process_manager.py` | Dev server launch/stop, process groups, Windows compat |
| **Port Registry** | `src/port_registry.py` | Auto-assign ports, conflict detection, atomic persistence |
| **Subprocess Registry** | `src/subprocess_registry.py` | Track running subprocesses with PID storage |
| **Project Validator** | `src/project_validator.py` | Validate project directories (git, TASKS.md, CLAUDE.md) |
| **Git Ops** | `src/git_ops.py` | Auto-commit with staged file safety check |
| **Env Loader** | `src/env_loader.py` | Unified .env loading with per-project key filtering |

---

## Frontend Components

| Component | File | Purpose |
|-----------|------|---------|
| **App** | `App.tsx` | Main layout: header, filters, swim lanes, bottom panel |
| **KanbanBoard** | `KanbanBoard.tsx` | Status columns with drag-drop via @dnd-kit |
| **SwimLane** | `SwimLane.tsx` | Per-project lane wrapping a KanbanBoard |
| **SwimLaneHeader** | `SwimLaneHeader.tsx` | Project action bar: launch/stop, sync, new task |
| **TaskCard** | `TaskCard.tsx` | Draggable card with status badge and elapsed timer |
| **TaskCardPopover** | `TaskCardPopover.tsx` | Hover popover with full task details |
| **TaskContextMenu** | `TaskContextMenu.tsx` | Right-click menu: view, move, retry |
| **InlineTaskCreator** | `InlineTaskCreator.tsx` | Click-to-create in Backlog column |
| **ExecutionLog** | `ExecutionLog.tsx` | Real-time log viewer (SSE + DB history, level badges) |
| **ReviewPanel** | `ReviewPanel.tsx` | Review history timeline with verdict badges |
| **LaunchControl** | `LaunchControl.tsx` | Dev server toggle with port display and uptime |
| **ProjectSelector** | `ProjectSelector.tsx` | Multi-select project filter with localStorage |
| **ImportProjectModal** | `ImportProjectModal.tsx` | 3-step import: browse, validate, configure |
| **DirectoryPicker** | `DirectoryPicker.tsx` | File system browser for path selection |
| **NewTaskModal** | `NewTaskModal.tsx` | Task creation form (title, description, priority) |
| **useSSE** | `hooks/useSSE.ts` | SSE hook with auto-reconnect and exponential backoff |

---

## API Reference

All endpoints are served at `http://localhost:8000`. See [QUICKSTART.md](QUICKSTART.md)
for the full endpoint table and example curl commands.

**Key endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects` | List all registered projects |
| GET | `/api/tasks` | List tasks (filter: `?project_id=`, `?status=`) |
| PATCH | `/api/tasks/{id}/status` | Transition task status (validated) |
| POST | `/api/tasks/{id}/execute` | Force-execute a task (202 Accepted) |
| POST | `/api/tasks/{id}/review` | Trigger async LLM review |
| POST | `/api/tasks/{id}/review/decide` | Submit human review decision |
| POST | `/api/sync-all` | Re-parse TASKS.md for all projects |
| GET | `/api/tasks/{id}/logs` | Fetch persistent execution logs |
| GET | `/api/tasks/{id}/reviews` | Fetch review history |
| GET | `/api/events` | SSE event stream |
| GET | `/api/dashboard/summary` | Aggregate stats |
| POST | `/api/projects/validate` | Validate a project directory |
| POST | `/api/projects/import` | Import a new project |
| GET | `/api/filesystem/browse` | Browse directories for import |

---

## TASKS.md Format

HelixOS parses `TASKS.md` from each registered project. Task IDs must match the
pattern `T-P{N}-{N}` (e.g., `T-P0-1`, `T-P1-3`).

```markdown
## In Progress
#### T-P0-1: Task currently running

## Active Tasks
#### T-P0-2: Task ready for scheduling
- **Deps**: T-P0-1

## Blocked
#### T-P0-3: Blocked task with reason

## Completed Tasks
#### [x] T-P0-1: Finished task -- 2026-03-01
```

| Section | Mapped status |
|---------|---------------|
| In Progress | RUNNING |
| Active Tasks / Active | BACKLOG (new tasks become QUEUED) |
| Blocked | BLOCKED |
| Completed Tasks / Done | DONE |

---

## Testing

```bash
# Run the full test suite (542 tests)
pytest

# Run only unit tests
pytest tests/ --ignore=tests/integration

# Run integration tests
pytest tests/integration -m integration

# Lint
ruff check src/ tests/
```

---

## Project Structure

```
helixos/
  orchestrator_config.yaml    # Main configuration
  pyproject.toml              # Python project config (ruff, mypy, pytest)
  requirements.txt            # Python dependencies
  QUICKSTART.md               # Detailed setup and API reference
  src/
    api.py                    # FastAPI app + endpoints
    scheduler.py              # Task scheduling loop
    task_manager.py           # Task CRUD + state machine
    review_pipeline.py        # LLM review via Claude CLI
    history_writer.py         # Persistent execution logs + review history
    events.py                 # EventBus + SSE streaming
    db.py                     # SQLAlchemy async database
    models.py                 # Pydantic domain models
    schemas.py                # API request/response schemas
    config.py                 # YAML config loader + ProjectRegistry
    config_writer.py          # Add projects to YAML config
    process_manager.py        # Dev server launch/stop
    port_registry.py          # Port auto-assignment
    subprocess_registry.py    # Running subprocess tracker
    project_validator.py      # Project directory validation
    tasks_writer.py           # TASKS.md append with filelock
    git_ops.py                # Git auto-commit
    env_loader.py             # Unified .env loader
    executors/
      base.py                 # BaseExecutor ABC
      code_executor.py        # Claude CLI subprocess executor
    sync/
      tasks_parser.py         # TASKS.md parser + DB sync
  frontend/
    src/
      App.tsx                 # Main dashboard layout
      api.ts                  # Typed API client
      types.ts                # TypeScript interfaces
      components/             # React components (15 modules)
      hooks/useSSE.ts         # SSE connection hook
    dist/                     # Built assets (after npm run build)
  tests/
    integration/              # End-to-end integration tests
  scripts/
    start.ps1                 # Windows quick-start script
    autonomous_run.sh         # Autonomous multi-session runner
  docs/                       # Extended documentation
  .claude/
    hooks/                    # Claude Code enforcement hooks
    settings.json             # Hook wiring and lifecycle config
```

---

## Development Workflow

HelixOS uses Claude Code hooks for self-enforcing quality gates:

- **PreToolUse**: Block dangerous shell commands, reject hardcoded secrets
- **PostToolUse**: Lint changed Python files (ruff), validate YAML, run tests
- **SessionStart**: Load task context, progress history, and lessons
- **Stop**: Enforce exit protocol (progress logged, tasks updated, tests pass)

### Autonomous mode

The autonomous runner loops Claude Code sessions until all tasks are done:

```bash
bash scripts/autonomous_run.sh
```

Each session picks one unblocked task, completes it, commits, and exits. The next
session picks the next task. Failed tasks retry up to 2 times before being marked
BLOCKED.

---

## License

See [LICENSE](LICENSE) for details.
