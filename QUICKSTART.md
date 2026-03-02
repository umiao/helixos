# HelixOS Quickstart Guide

A step-by-step guide to install, configure, and run the HelixOS autonomous
multi-project orchestrator.

---

## Prerequisites

| Requirement       | Version   | Check command              |
|-------------------|-----------|----------------------------|
| Python            | 3.11+     | `python --version`         |
| Node.js           | 18+       | `node --version`           |
| npm               | 9+        | `npm --version`            |
| Claude Code CLI   | latest    | `claude --version`         |
| Git               | 2.30+     | `git --version`            |

> **Note**: The Claude Code CLI must be installed and authenticated separately.
> See [Claude Code documentation](https://docs.anthropic.com/en/docs/claude-code)
> for setup instructions.

---

## Installation

### 1. Clone the repository

```bash
git clone <repo-url> helixos
cd helixos
```

### 2. Create a Python virtual environment

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Install frontend dependencies and build

```bash
cd frontend
npm install
npm run build
cd ..
```

After this step, `frontend/dist/` should contain the built dashboard assets.

---

## Configuration

All configuration lives in `orchestrator_config.yaml` at the project root.

### Orchestrator settings

```yaml
orchestrator:
  global_concurrency_limit: 3        # max concurrent tasks across all projects
  per_project_concurrency: 1         # max concurrent tasks per project
  review_consensus_threshold: 0.8    # review score needed for auto-approve
  session_timeout_minutes: 60        # max runtime per task session
  subprocess_terminate_grace_seconds: 5
  unified_env_path: "~/.helixos/.env"  # shared env file for secrets
  state_db_path: "~/.helixos/state.db" # SQLite database path
```

### Adding a project

Each project gets an entry under `projects:`. The key (e.g. `P0`) becomes the
project ID used in API calls and TASKS.md task IDs.

```yaml
projects:
  P0:
    name: "My Project"
    repo_path: "~/path/to/project"
    executor_type: "code"       # "code", "agent", or "scheduled"
    tasks_file: "TASKS.md"      # relative to repo_path
    max_concurrency: 1
```

You can add multiple projects:

```yaml
projects:
  P0:
    name: "Backend Service"
    repo_path: "~/projects/backend"
    executor_type: "code"
    tasks_file: "TASKS.md"
    max_concurrency: 1

  P1:
    name: "Frontend App"
    repo_path: "~/projects/frontend"
    executor_type: "code"
    tasks_file: "TASKS.md"
    max_concurrency: 1
```

### Git auto-commit settings

```yaml
git:
  auto_commit: true
  commit_message_template: "[helixos] {project}: {task_id} {task_title}"
  staged_safety_check:
    max_files: 50            # abort commit if more files staged
    max_total_size_mb: 10
```

### Review pipeline (optional)

```yaml
review_pipeline:
  reviewers:
    - model: "claude-sonnet-4-5"
      focus: "feasibility_and_edge_cases"
      api: "claude_cli"
      required: true
    - model: "claude-sonnet-4-5"
      focus: "adversarial_red_team"
      api: "claude_cli"
      required: false         # only runs for M/L complexity tasks
```

### Environment variables

Create `~/.helixos/.env` (or the path specified in `unified_env_path`) for any
secrets your projects need:

```bash
# Example ~/.helixos/.env
MY_API_KEY=sk-...
DATABASE_URL=postgres://...
```

The env loader injects only the keys listed in each project's `env_keys` field.

---

## Running HelixOS

### Development mode (with hot-reload)

Start the backend and frontend dev servers separately:

```bash
# Terminal 1: Backend API server
uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2: Frontend dev server (with proxy to backend)
cd frontend
npm run dev
```

- Backend API: http://localhost:8000
- Frontend dev server: http://localhost:5173 (proxied API calls to :8000)

### Production mode

Build the frontend and serve everything from the backend:

```bash
# Build frontend
cd frontend && npm run build && cd ..

# Start server (serves both API and dashboard)
uvicorn src.api:app --host 127.0.0.1 --port 8000
```

- Dashboard + API: http://localhost:8000

### Windows quick start (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

This builds the frontend and starts uvicorn in one step.

---

## First sync

Once the server is running, sync your project's TASKS.md into the database:

1. **Sync all projects**:
   ```bash
   curl -X POST http://localhost:8000/api/sync-all
   ```

2. **Open the dashboard** at http://localhost:8000 (production) or
   http://localhost:5173 (dev mode).

3. Task cards should appear on the Kanban board organized by status columns:
   BACKLOG, REVIEW, QUEUED, RUNNING, DONE.

4. **Drag cards** between columns to change task status, or use the API
   endpoints below.

---

## TASKS.md Format

HelixOS parses `TASKS.md` from each project to discover tasks. The parser
expects a specific format.

### Required structure

```markdown
# Task Backlog

## In Progress
<!-- Tasks here are mapped to RUNNING status -->

#### T-P0-1: Task title here
- Description and notes

## Active Tasks
<!-- Tasks here are mapped to BACKLOG -> QUEUED on sync -->

#### T-P0-2: Another task
- **Deps**: T-P0-1
- **Complexity**: M

## Blocked
<!-- Tasks here are mapped to BLOCKED status -->

## Completed Tasks
<!-- Tasks here are mapped to DONE status -->

#### [x] T-P0-1: Task title -- 2026-03-01
- Completion notes
```

### Task ID convention

All task IDs must match the pattern `T-P{priority}-{number}`, for example:
- `T-P0-1` (priority 0, task 1)
- `T-P1-3` (priority 1, task 3)

The priority number should correspond to the project ID in
`orchestrator_config.yaml` (e.g. project `P0` uses tasks `T-P0-*`).

### Section-to-status mapping

| Section header    | Task status |
|-------------------|-------------|
| In Progress       | RUNNING     |
| Active Tasks      | BACKLOG     |
| Active            | BACKLOG     |
| Completed Tasks   | DONE        |
| Completed         | DONE        |
| Done              | DONE        |
| Blocked           | BLOCKED     |

Tasks under "Active Tasks" are synced as QUEUED (ready for execution) if they
are new to the database.

---

## API Reference

All endpoints are served at `http://localhost:8000`.

### Project endpoints

| Method | Path                          | Description                          |
|--------|-------------------------------|--------------------------------------|
| GET    | `/api/projects`               | List all registered projects         |
| GET    | `/api/projects/{project_id}`  | Get project details with its tasks   |

### Task endpoints

| Method | Path                                  | Description                                      |
|--------|---------------------------------------|--------------------------------------------------|
| GET    | `/api/tasks`                          | List tasks (filter: `?project_id=`, `?status=`)  |
| GET    | `/api/tasks/{task_id}`                | Get a single task by ID                          |
| PATCH  | `/api/tasks/{task_id}/status`         | Transition task status (state machine validated)  |
| POST   | `/api/tasks/{task_id}/review`         | Trigger async review (202 Accepted)              |
| POST   | `/api/tasks/{task_id}/review/decide`  | Submit human review decision (approve/reject)    |
| POST   | `/api/tasks/{task_id}/execute`        | Force-execute a task (queues it, 202 Accepted)   |
| POST   | `/api/tasks/{task_id}/retry`          | Reset retries and re-queue a failed task         |
| POST   | `/api/tasks/{task_id}/cancel`         | Cancel a running task                            |

### Sync endpoints

| Method | Path                              | Description                        |
|--------|-----------------------------------|------------------------------------|
| POST   | `/api/projects/{project_id}/sync` | Re-parse TASKS.md for one project  |
| POST   | `/api/sync-all`                   | Re-parse TASKS.md for all projects |

### Dashboard and events

| Method | Path                      | Description                               |
|--------|---------------------------|-------------------------------------------|
| GET    | `/api/dashboard/summary`  | Aggregate stats (total, by status, etc.)  |
| GET    | `/api/events`             | SSE event stream (status, logs, alerts)   |

### Example API calls

```bash
# List all projects
curl http://localhost:8000/api/projects

# Sync all projects
curl -X POST http://localhost:8000/api/sync-all

# List tasks filtered by project
curl "http://localhost:8000/api/tasks?project_id=P0"

# Transition a task status
curl -X PATCH http://localhost:8000/api/tasks/TASK_ID/status \
  -H "Content-Type: application/json" \
  -d '{"status": "queued"}'

# Trigger a review
curl -X POST http://localhost:8000/api/tasks/TASK_ID/review

# Get dashboard summary
curl http://localhost:8000/api/dashboard/summary

# Listen to SSE events
curl -N http://localhost:8000/api/events
```

---

## Autonomous Mode

HelixOS can run tasks autonomously using the Claude Code CLI.

### How it works

1. The scheduler polls the database every 5 seconds for QUEUED tasks.
2. When a task is ready (dependencies met, concurrency slots available), it
   spawns `claude -p "..."` with the task prompt.
3. On success, the task moves to DONE and git auto-commit runs (if enabled).
4. On failure, the task retries with exponential backoff (30s, 60s, 120s).
5. After max retries (3), the task moves to BLOCKED.

### Running the autonomous loop

Start the server normally. The scheduler runs as part of the API server:

```bash
uvicorn src.api:app --host 127.0.0.1 --port 8000
```

Then sync tasks and watch them execute:

```bash
# Sync TASKS.md into the database
curl -X POST http://localhost:8000/api/sync-all

# Monitor via SSE
curl -N http://localhost:8000/api/events

# Or open the dashboard at http://localhost:8000
```

---

## Troubleshooting

### Server fails to start

**"Module not found" errors**:
```bash
# Ensure you are in the project root and venv is activated
pip install -r requirements.txt
```

**Port already in use**:
```bash
# Use a different port
uvicorn src.api:app --port 8001
```

### Claude CLI not found

If the server logs `Claude CLI not found in PATH -- review pipeline disabled`:

1. Verify Claude Code CLI is installed: `claude --version`
2. Ensure it is on your PATH
3. The review pipeline requires Claude CLI. Without it, manual review
   and autonomous execution are unavailable. All other features work normally.

### Database errors

The state database is stored at `~/.helixos/state.db` by default. The server
auto-creates the `~/.helixos/` directory on startup.

**Reset the database**:
```bash
rm ~/.helixos/state.db
# Restart the server -- tables are recreated automatically
```

### Frontend build failures

```bash
cd frontend
rm -rf node_modules
npm install
npm run build
```

Requires Node.js 18+ and npm 9+.

### Sync returns empty results

- Verify `repo_path` in `orchestrator_config.yaml` points to the correct
  directory (supports `~` expansion).
- Verify the project has a `TASKS.md` file (or whatever `tasks_file` is set to).
- Verify task IDs follow the `T-P{N}-{N}` pattern.
- Check that tasks are under recognized section headers (see the section
  mapping table above).

### Tasks stuck in QUEUED

- Check the Claude CLI is available: `claude --version`
- Check concurrency limits in config (tasks wait if all slots are occupied).
- Check task dependencies -- a task with unmet `depends_on` will not execute.
- Review server logs for errors: run uvicorn with `--log-level debug`.

### SSE connection drops

The dashboard auto-reconnects with exponential backoff (1s, 2s, 4s, max 30s).
The green/red connection indicator in the header shows the current SSE status.

If events are not appearing:
- Verify the server is running
- Check browser console for connection errors
- In dev mode, ensure the Vite proxy is configured (default setup handles this)

---

## Project structure

```
helixos/
  orchestrator_config.yaml   # Main configuration
  pyproject.toml             # Python project metadata
  requirements.txt           # Python dependencies
  TASKS.md                   # Task backlog (parsed by orchestrator)
  src/
    api.py                   # FastAPI app + all endpoints
    config.py                # YAML config loader + ProjectRegistry
    db.py                    # SQLAlchemy async database layer
    env_loader.py            # Unified .env loader
    events.py                # EventBus + SSE streaming
    git_ops.py               # Git auto-commit
    models.py                # Pydantic domain models
    review_pipeline.py       # LLM review via Claude CLI
    scheduler.py             # Task scheduler (tick loop + retry)
    schemas.py               # API request/response schemas
    task_manager.py          # Task CRUD + state machine
    executors/
      base.py                # BaseExecutor ABC
      code_executor.py       # Claude CLI subprocess executor
    sync/
      tasks_parser.py        # TASKS.md parser + sync
  frontend/
    src/                     # React + TypeScript + Tailwind
    dist/                    # Built dashboard (after npm run build)
  tests/                     # pytest test suite
    integration/             # End-to-end integration tests
  scripts/
    start.ps1                # Windows quick-start script
  config/                    # Additional config files
  contracts/                 # Cross-project contract specs
```
