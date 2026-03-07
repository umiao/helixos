# HelixOS — AI Project Orchestrator PRD

> **Status**: Draft v0.3 -- Hardening review incorporated
> **Author**: Auto-generated from design session
> **Last Updated**: 2026-03-01

---

## 1. Problem Statement

A solo developer maintains a growing portfolio of AI-augmented personal projects (currently 8+),
each with its own git repo, task backlog, and execution requirements. Today, orchestration is
entirely manual: the developer copies task plans between LLM chat windows for review, manually
triggers Claude Code sessions per project, and tracks progress across separate TASKS.md files.

**Pain points:**
- No unified view of cross-project task status
- LLM plan review requires manual copy-paste across multiple chat UIs
- No automated dependency enforcement between projects
- No way to batch-trigger or limit concurrent autonomous sessions
- Heterogeneous task types (code, research, media processing) lack a common execution model

## 2. Vision

A local-first orchestration system with a Kanban-style dashboard that:
- Provides a single pane of glass across all managed projects
- Automates the plan-review-execute lifecycle with minimal human intervention
- Enforces cross-project dependencies via simple linear chains
- Caps concurrent autonomous sessions conservatively (default 3, per-project max 1)
- Supports heterogeneous executors (code agents, research agents, file processors)
- Prioritizes **stability over features** -- this is a personal workflow kernel, not a platform

## 3. User Profile

- **Single user**, running on local Mac/PC
- Technical (comfortable with CLI, git, Python, YAML config)
- Wants maximum autonomy for the system, intervening only for high-stakes decisions
- Existing workflow: Claude Code with autonomous_runner, TASKS.md-driven, git-based

---

## 3.1 Anti-Goals (Explicit "Do Not Build")

These are tempting features that would pull HelixOS toward becoming a general-purpose
platform. They are explicitly out of scope **permanently**, not just deferred:

- **DAG execution graph** -- linear dependencies only, forever. Complex DAGs belong in Airflow.
- **Kubernetes-style service management** -- HelixOS is not a process supervisor.
- **Multi-host / distributed execution** -- single Mac, single user, always.
- **Automatic resource scaling** -- no CPU/memory governors. User adjusts concurrency manually.
- **Auto branch management per task** -- orchestrator commits to current branch only.
- **Bidirectional TASKS.md sync** -- TASKS.md is source of truth; DB is read-only mirror for display.
- **Subprocess tree monitoring** -- no `psutil` recursive process scanning; rely on timeout kill.
- **Contract schema validation** -- dependency = "upstream task done", not "file matches schema".

If a future feature request conflicts with this list, the answer is no.

---

## 4. Project Portfolio (Task Taxonomy)

### 4.1 Managed Projects

| ID  | Project                    | Type              | Executor         | Status     |
|-----|----------------------------|-------------------|------------------|------------|
| P0  | **HelixOS (this system)**   | Meta-system       | CodeExecutor     | This PRD   |
| P1  | **Job Hunter**             | Software          | CodeExecutor     | In progress|
| P2  | **Blog Reorganization**    | Pipeline + App    | AgentExecutor    | Backlog    |
| P3  | Learning Recommender       | Interactive App   | CodeExecutor     | Backlog    |
| P4  | Social Media News Feed     | Scheduled Agent   | ScheduledExecutor| Backlog    |
| P5  | Language Learning          | Interactive App   | CodeExecutor     | Backlog    |
| P6  | Photo/Media Organizer      | Pipeline          | AgentExecutor    | Backlog    |
| P7  | Contemplation Recommender  | Interactive App   | CodeExecutor     | Backlog    |
| P8  | Email Manager              | Scheduled Agent   | ScheduledExecutor| Backlog    |

### 4.2 Task Types (Executor Classification)

All tasks share a unified data model. The **executor** is what differs:

```
AbstractExecutor
  |
  +-- CodeExecutor         # cd <repo> && claude -p "..." --allowedTools ...
  |                        # For: software projects with git repos
  |
  +-- AgentExecutor        # claude -p "..." in a workspace directory
  |                        # For: research, document authoring, file processing
  |                        # No git repo required; works in a scratch workspace
  |
  +-- ScheduledExecutor    # cron-triggered AgentExecutor with credential injection
                           # For: periodic scraping, email processing, feed updates
```

**Key insight**: A "task" is always `(context, prompt, executor, constraints)` regardless of
whether it produces code, a document, or a side effect. The orchestrator does not care what
the agent does -- only that it reports success/failure and produces declared outputs.

### 4.3 Cross-Project Dependencies

Simple linear chains only (no DAG). Expressed as:

```yaml
# In global config
dependencies:
  - upstream: "P2:T-blog-structured-output"
    downstream: "P3:T-import-blog-corpus"
    contract: "contracts/blog_corpus_schema.json"
```

**Resolution rule**: A task cannot enter `queued` status if any upstream dependency is not `done`.

---

## 5. Architecture

### 5.1 System Overview

```
+------------------------------------------------------+
|                   Dashboard (React)                   |
|  Kanban Board | Execution Log | Review Panel          |
|  SSE <-----+                                          |
+------------|------------------------------------------+
             |
+------------|------------------------------------------+
|            v                                          |
|  Orchestrator Backend (Python / FastAPI)               |
|                                                       |
|  +-------------+  +-------------+  +---------------+  |
|  | TaskManager |  | Scheduler   |  | ReviewPipeline|  |
|  | (CRUD,      |  | (queue,     |  | (multi-LLM    |  |
|  |  state      |  |  concurrency|  |  auto-review) |  |
|  |  machine)   |  |  control)   |  |               |  |
|  +------+------+  +------+------+  +-------+-------+  |
|         |                |                  |          |
|  +------v---------+------v---------+--------v------+   |
|  | ExecutorRouter |                                |   |
|  |  CodeExecutor | AgentExecutor | ScheduledExec  |   |
|  +----------------+----------------+--------------+   |
|                                                       |
|  +--------------------------------------------------+ |
|  | ProjectRegistry                                   | |
|  | - project configs (repo path, env, task file)     | |
|  | - credential references (unified .env)            | |
|  | - contract definitions                            | |
|  +--------------------------------------------------+ |
|                                                       |
|  +--------------------------------------------------+ |
|  | StateStore (SQLite)                               | |
|  | - tasks, executions, reviews, logs                | |
|  +--------------------------------------------------+ |
+-------------------------------------------------------+
```

### 5.2 Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Runtime | Local Mac, long-running process | Single user, needs access to local git repos and files |
| Backend | Python + FastAPI | Consistent with existing ecosystem; async for concurrency |
| Frontend | React SPA (Kanban), served by FastAPI | Single process, single port; `npm run build` -> FastAPI static mount |
| State | SQLite | Single user, no need for Postgres; portable |
| Real-time | SSE (Server-Sent Events) | Simpler than WebSocket; sufficient for log streaming |
| Credentials | Unified .env at orchestrator root, projects reference via env injection | Single source of truth for all secrets |
| Task format | Each project keeps its own TASKS.md (free-form) | Backward compatible; parser only extracts ID/title/status |
| Concurrency | Max 1 per project (configurable to 0); **global default 3** | Conservative; 3 claude processes is practical on 16GB Mac |
| Git commits | Orchestrator auto-commits with staged safety check | Standardized; abort if >50 files staged to prevent accidental commits |
| Review | Opt-in; 1+1 reviewers (Anthropic only); async background | TASKS.md tasks skip review; non-blocking 202 + SSE |
| Crash recovery | On startup: RUNNING -> FAILED | Simple; no complex journaling or checkpointing |
| Process lifecycle | Timeout -> SIGTERM -> grace -> SIGKILL | Prevents zombie subprocesses from blocking project slots |
| Stability | **Stability > features** | Personal workflow kernel, not a platform (see Anti-Goals) |

### 5.3 State Machine (Task Lifecycle)

```
                                     +---> [review_auto_approved] ---+
                                     |                               |
[backlog] --+---> [review] ----------+                               +--> [queued] --> [running] --> [done]
            |                        |                               |        |            |
            |                        +---> [review_needs_human] -----+        |            +---> [failed]
            |                                    |                            |                    |
            +--- (from TASKS.md sync) ---------->+    (human picks option)    |                    v
                 (skip review)                                                +--- [blocked] <-----+
                                                                                   (dep not met
                                                                                    or 3x retry fail)
```

**Transitions triggered by:**
- `backlog -> review`: User clicks "Start Review" (for tasks needing design review)
- `backlog -> queued`: **Direct path** -- tasks synced from TASKS.md are already reviewed
- `review -> queued`: Auto-approved (consensus >= threshold) or human approved
- `queued -> running`: Scheduler picks from queue, project has no other running task, deps met
- `running -> done`: Executor reports success
- `running -> failed`: Executor reports failure
- `failed -> queued`: Auto-retry (up to 3x) or manual retry
- `failed -> blocked`: 3x retry exhausted; needs human attention
- `* -> blocked`: Upstream dependency not met

### 5.4 Execution Controls: Pause, Review Gate, and Start All Planned

Three per-project controls govern task flow through the pipeline. They are
independent and composable.

#### Pause (`execution_paused`)

**Scope**: Scheduler dispatch only (QUEUED -> RUNNING).

| Aspect | Behavior |
|--------|----------|
| What it blocks | The scheduler skips paused projects when picking QUEUED tasks to run. No new executions are dispatched. |
| What it does NOT block | Review pipeline submissions (BACKLOG -> REVIEW), review pipeline execution, manual status transitions (drag-to-QUEUED, drag-to-REVIEW), and Start All Planned. |
| In-flight tasks | Continue to completion. Pause does not cancel or interrupt RUNNING tasks. |
| Persistence | DB-backed (`ProjectSettingsRow.execution_paused`). Survives restarts. |
| UI | Amber Pause/Resume toggle in SwimLaneHeader. PAUSED badge when active. |
| SSE event | `execution_paused` with `{"paused": true/false}`. |

#### Review Gate (`review_gate_enabled`)

**Scope**: Task promotion past the review stage.

| Aspect | Behavior |
|--------|----------|
| When ON (default) | **Layer 1**: BACKLOG -> QUEUED transition is blocked. Tasks must go BACKLOG -> REVIEW -> (approved) -> QUEUED. **Layer 2**: Scheduler's `_can_execute()` verifies an approved review record exists before dispatching, as a last line of defense. |
| When OFF | Tasks can skip review and move directly BACKLOG -> QUEUED. Layer 2 is also disabled. |
| Does NOT affect | In-flight executions, pause state, or the review pipeline itself (reviews can still be manually triggered even when the gate is OFF). |
| Persistence | DB-backed (`ProjectSettingsRow.review_gate_enabled`). Survives restarts. |
| UI | Gate ON/OFF toggle in SwimLaneHeader. |
| SSE event | `review_gate_changed` with `{"review_gate_enabled": true/false}`. |

#### Start All Planned (batch operation)

**Scope**: One-shot batch transition for planned tasks.

| Aspect | Behavior |
|--------|----------|
| Trigger | `POST /api/projects/{project_id}/start-all-planned` or "Start All Planned Tasks" button. |
| Target tasks | All BACKLOG tasks with `plan_status=ready`. |
| Gate ON | Tasks move to REVIEW. Review pipeline starts for each. |
| Gate OFF | Tasks move to QUEUED. Scheduler picks them up on next tick. |
| Pause interaction | Start All Planned works while paused. Tasks will queue up (REVIEW or QUEUED) but QUEUED tasks will not execute until the project is resumed. |
| Concurrency safety | Each task transition uses optimistic locking (`expected_updated_at`). Concurrent edits are skipped, not failed. |
| Error handling | Plan validity errors and concurrent edits per-task are reported in `skipped_details`; the batch continues. |

#### Edge Cases and Combinations

| Scenario | Behavior |
|----------|----------|
| Pause ON + Gate ON | Tasks must be reviewed before execution. Even after approval, QUEUED tasks will not execute until resumed. |
| Pause ON + Gate OFF | Tasks can skip review (BACKLOG -> QUEUED), but the scheduler will not dispatch them until resumed. |
| Start All while Paused + Gate ON | Tasks move to REVIEW. Review pipeline runs. After approval, tasks reach QUEUED but stay there until resumed. |
| Start All while Paused + Gate OFF | Tasks move directly to QUEUED but stay there until resumed. |
| Resume while tasks in QUEUED | Scheduler picks up all QUEUED tasks on the next tick (subject to concurrency limits). |
| Pause after tasks already RUNNING | In-flight tasks complete normally. Only new dispatches are blocked. |
| Gate toggled while tasks in REVIEW | No effect on in-progress reviews. The gate only affects future BACKLOG -> QUEUED transitions. |

---

## 6. Data Model

### 6.1 Core Entities

```python
# orchestrator/models.py
from pydantic import BaseModel
from datetime import datetime
from enum import Enum
from pathlib import Path

class TaskStatus(str, Enum):
    BACKLOG = "backlog"
    REVIEW = "review"
    REVIEW_NEEDS_HUMAN = "review_needs_human"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"

class ExecutorType(str, Enum):
    CODE = "code"           # Claude Code in a git repo
    AGENT = "agent"         # Claude as research/writing agent in workspace
    SCHEDULED = "scheduled" # Cron-triggered agent with credentials

class Project(BaseModel):
    id: str                          # "P1", "P2", etc.
    name: str                        # "Job Hunter"
    repo_path: Path | None = None    # Git repo path (None for agent-only projects)
    workspace_path: Path | None = None  # Scratch workspace for AgentExecutor
    tasks_file: str = "TASKS.md"     # Relative to repo_path
    executor_type: ExecutorType
    max_concurrency: int = 1         # 0 = manual only, 1 = default
    env_keys: list[str] = []         # Which keys from unified .env this project needs
    claude_md_path: Path | None = None  # Custom claude.md for this project

class Task(BaseModel):
    id: str                          # "P1:T-P3-2" (project:local_task_id)
    project_id: str
    local_task_id: str               # "T-P3-2" (as appears in project's TASKS.md)
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.BACKLOG
    executor_type: ExecutorType
    depends_on: list[str] = []       # Other task IDs: ["P0:T-setup-db"]
    
    # Review state
    review: "ReviewState | None" = None
    
    # Execution state
    execution: "ExecutionState | None" = None
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

class ReviewState(BaseModel):
    rounds_total: int = 3
    rounds_completed: int = 0
    reviews: list["LLMReview"] = []
    consensus_score: float | None = None
    human_decision_needed: bool = False
    decision_points: list[str] = []      # Questions for human
    human_choice: str | None = None      # Human's selected option

class LLMReview(BaseModel):
    model: str                           # "claude-sonnet-4-5", "gpt-4o"
    focus: str                           # "feasibility", "edge_cases", "adversarial"
    verdict: str                         # "approve", "concerns", "reject"
    summary: str
    suggestions: list[str] = []
    timestamp: datetime

class ExecutionState(BaseModel):
    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 3
    exit_code: int | None = None
    log_tail: list[str] = []             # Last N lines of output
    result: str = "pending"              # "pending", "success", "failed"
    error_summary: str | None = None

class Dependency(BaseModel):
    upstream_task: str                   # "P0:T-setup-db"
    downstream_task: str                 # "P1:T-integrate"
    contract_path: str | None = None     # Optional contract file to validate
    fulfilled: bool = False
```

### 6.2 Project Registry (YAML Config)

```yaml
# orchestrator_config.yaml
orchestrator:
  global_concurrency_limit: 3   # Conservative default; min(this, active_projects_with_queued_tasks)
  per_project_concurrency: 1    # Max 1 running task per project (immutable for now)
  review_consensus_threshold: 0.8
  session_timeout_minutes: 60
  subprocess_terminate_grace_seconds: 5  # After timeout: SIGTERM, wait this, then SIGKILL
  unified_env_path: "~/.helixos/.env"
  state_db_path: "~/.helixos/state.db"

projects:
  P0:
    name: "HelixOS"
    repo_path: "~/projects/helixos"
    executor_type: "code"
    tasks_file: "TASKS.md"
    max_concurrency: 1              # default; max running tasks for this project

  P1:
    name: "Job Hunter"
    repo_path: "~/projects/job-hunter"
    executor_type: "code"
    tasks_file: "TASKS.md"
    claude_md_path: "~/projects/job-hunter/claude.md"
    max_concurrency: 1

  P2:
    name: "Blog Reorganization"
    repo_path: "~/projects/blog-reorg"
    workspace_path: "~/projects/blog-reorg/workspace"
    executor_type: "agent"
    tasks_file: "TASKS.md"
    max_concurrency: 1

  # Example: manual-only project (orchestrator tracks status but never auto-executes)
  # P99:
  #   name: "Manual Project"
  #   max_concurrency: 0

git:
  auto_commit: true                 # Orchestrator commits after successful task execution
  commit_message_template: "[helixos] {project}: {task_id} {task_title}"
  staged_safety_check:
    max_files: 50                   # Abort commit if more than this many files staged
    max_total_size_mb: 10           # Abort commit if staged diff exceeds this size

review_pipeline:
  reviewers:
    - model: "claude-sonnet-4-5"
      focus: "feasibility_and_edge_cases"
      api: "anthropic"
      required: true
    - model: "claude-sonnet-4-5"
      focus: "adversarial_red_team"
      api: "anthropic"
      required: false                # Optional; skip for [S] complexity tasks

dependencies:
  - upstream: "P2:T-structured-output"
    downstream: "P3:T-import-corpus"
    contract: "contracts/blog_corpus_v1.json"
```

---

## 7. Executor Interface

### 7.1 Abstract Interface

```python
# orchestrator/executors/base.py
from abc import ABC, abstractmethod

class ExecutorResult(BaseModel):
    success: bool
    exit_code: int
    log_lines: list[str]
    error_summary: str | None = None
    outputs: list[str] = []          # Files/artifacts produced
    duration_seconds: float

class BaseExecutor(ABC):
    @abstractmethod
    async def execute(
        self,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],   # SSE callback for real-time log streaming
    ) -> ExecutorResult:
        """Execute a task and return the result."""
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel a running execution."""
        ...
```

### 7.2 CodeExecutor

Spawns `claude` CLI in the project's git repo directory:

```python
# orchestrator/executors/code_executor.py
class CodeExecutor(BaseExecutor):
    async def execute(self, task, project, env, on_log) -> ExecutorResult:
        prompt = self._build_prompt(task)
        cmd = [
            "claude", "-p", prompt,
            "--allowedTools", "Bash,Read,Write,Edit,MultiTool",
            "--output-format", "json",
        ]
        
        timeout = self.config.session_timeout_minutes * 60
        grace = self.config.subprocess_terminate_grace_seconds
        start = time.monotonic()
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env},    # Inject project-specific env vars
        )
        
        log_lines = []
        timed_out = False
        
        try:
            # Stream stdout with overall timeout
            async with asyncio.timeout(timeout):
                async for line in proc.stdout:
                    decoded = line.decode("utf-8").strip()
                    log_lines.append(decoded)
                    on_log(decoded)
                await proc.wait()
        except TimeoutError:
            timed_out = True
            on_log(f"[TIMEOUT] Session exceeded {self.config.session_timeout_minutes}min, terminating...")
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=grace)
            except TimeoutError:
                on_log(f"[TIMEOUT] Process did not exit after {grace}s, killing...")
                proc.kill()
                await proc.wait()
        
        elapsed = time.monotonic() - start
        
        return ExecutorResult(
            success=(not timed_out and proc.returncode == 0),
            exit_code=proc.returncode if proc.returncode is not None else -9,
            log_lines=log_lines[-100:],
            error_summary="Session timeout - process killed" if timed_out else None,
            duration_seconds=elapsed,
        )
    
    def _build_prompt(self, task: Task) -> str:
        """Build the one-shot prompt for Claude Code."""
        return (
            f"You are working on task {task.local_task_id}: {task.title}\n\n"
            f"{task.description}\n\n"
            f"Follow the project's TASKS.md and claude.md conventions. "
            f"Complete this task, run tests, and update TASKS.md and PROGRESS.md."
        )
```

### 7.3 AgentExecutor

For non-code tasks (research, document authoring, file processing).
Runs Claude in a workspace directory without git conventions:

```python
# orchestrator/executors/agent_executor.py
class AgentExecutor(BaseExecutor):
    async def execute(self, task, project, env, on_log) -> ExecutorResult:
        prompt = self._build_prompt(task)
        
        # Ensure workspace exists
        workspace = project.workspace_path or (project.repo_path / "workspace")
        workspace.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            "claude", "-p", prompt,
            "--allowedTools", "Bash,Read,Write,Edit,WebSearch",
            "--output-format", "json",
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **env},
        )
        # ... same streaming pattern as CodeExecutor
```

### 7.4 ScheduledExecutor

Wraps AgentExecutor with cron scheduling and credential injection:

```python
# orchestrator/executors/scheduled_executor.py
class ScheduledExecutor(BaseExecutor):
    """Not triggered by queue -- triggered by cron schedule."""
    
    def __init__(self, schedule: str):  # e.g., "0 */6 * * *" (every 6 hours)
        self.schedule = schedule
        self.agent_executor = AgentExecutor()
    
    async def execute(self, task, project, env, on_log) -> ExecutorResult:
        # Inject cookies/credentials from unified .env
        enriched_env = {**env}
        for key in project.env_keys:
            if key in self.master_env:
                enriched_env[key] = self.master_env[key]
        
        return await self.agent_executor.execute(task, project, enriched_env, on_log)
```

---

## 8. Scheduler (Per-Project Concurrency Control)

```python
# orchestrator/scheduler.py
class Scheduler:
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.running: dict[str, asyncio.Task] = {}  # task_id -> asyncio.Task
    
    async def startup_recovery(self) -> None:
        """On startup, recover from any previous crash.
        
        Any task left in RUNNING status means the previous process died mid-execution.
        Mark them as FAILED so they can be retried or inspected.
        """
        orphaned = self.task_manager.get_tasks_by_status(TaskStatus.RUNNING)
        for task in orphaned:
            self.task_manager.update_status(task.id, TaskStatus.FAILED)
            task.execution.error_summary = "Recovered from crash -- was RUNNING when process exited"
            self.event_bus.emit(
                "alert", task.id, 
                f"Task was RUNNING at startup, marked FAILED. Retry or inspect."
            )
        if orphaned:
            logger.warning(f"Startup recovery: {len(orphaned)} orphaned tasks marked FAILED")
    
    def _project_is_busy(self, project_id: str) -> bool:
        """Check if project has reached its max concurrent tasks."""
        project = self.registry.get_project(project_id)
        if project.max_concurrency == 0:
            return True  # Manual-only project, never auto-execute
        running_count = sum(
            1 for tid in self.running if tid.startswith(f"{project_id}:")
        )
        return running_count >= project.max_concurrency
    
    @property
    def max_concurrent(self) -> int:
        active_projects = self.task_manager.count_active_projects()
        return min(self.config.global_concurrency_limit, active_projects)
    
    @property
    def available_slots(self) -> int:
        return max(0, self.max_concurrent - len(self.running))
    
    async def tick(self) -> None:
        """Called periodically (e.g., every 5 seconds). Main scheduling loop."""
        
        if self.available_slots <= 0:
            return
        
        # Get queued tasks, sorted by priority, with fulfilled dependencies
        candidates = self.task_manager.get_ready_tasks(limit=self.available_slots)
        
        for task in candidates:
            # Per-project concurrency: skip if this project already has a running task
            if self._project_is_busy(task.project_id):
                continue
            
            # Verify dependencies are met
            if not self._deps_fulfilled(task):
                continue
            
            # Launch execution
            project = self.registry.get_project(task.project_id)
            executor = self._get_executor(project.executor_type)
            
            self.task_manager.update_status(task.id, TaskStatus.RUNNING)
            self.running[task.id] = asyncio.create_task(
                self._run_with_retry(executor, task, project)
            )
    
    async def _run_with_retry(self, executor, task, project) -> None:
        """Execute with automatic retry and exponential backoff."""
        for attempt in range(task.execution.max_retries + 1):
            result = await executor.execute(
                task, project,
                env=self._load_env(project),
                on_log=lambda line: self.event_bus.emit("log", task.id, line),
            )
            
            if result.success:
                self.task_manager.update_status(task.id, TaskStatus.DONE)
                await self._auto_commit(project, task)
                self._check_downstream_contracts(task)
                break
            else:
                task.execution.retry_count += 1
                if task.execution.retry_count > task.execution.max_retries:
                    self.task_manager.update_status(task.id, TaskStatus.BLOCKED)
                    self.event_bus.emit("alert", task.id, "Max retries exhausted")
                    break
                # Exponential backoff: 30s, 60s, 120s, ...
                backoff = 30 * (2 ** (task.execution.retry_count - 1))
                self.event_bus.emit(
                    "log", task.id,
                    f"Retry {task.execution.retry_count}/{task.execution.max_retries} "
                    f"in {backoff}s..."
                )
                await asyncio.sleep(backoff)
        
        del self.running[task.id]
    
    def _deps_fulfilled(self, task: Task) -> bool:
        """Check if all upstream dependencies are done."""
        for dep_id in task.depends_on:
            dep_task = self.task_manager.get_task(dep_id)
            if dep_task.status != TaskStatus.DONE:
                return False
        return True
    
    async def _auto_commit(self, project: Project, task: Task) -> None:
        """Auto-commit changes after successful task execution.
        
        Includes a staged safety check: if the number of staged files or total
        diff size exceeds configured thresholds, abort the commit and alert.
        This prevents accidentally committing large artifacts or debug output.
        """
        if not project.repo_path or not self.config.git_auto_commit:
            return
        
        repo = str(project.repo_path)
        
        # Stage changes
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=repo, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        
        # Safety check: count staged files
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--cached", "--stat", "--numstat",
            cwd=repo, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        staged_lines = stdout.decode("utf-8").strip().split("\n")
        staged_count = len([l for l in staged_lines if l.strip()])
        
        if staged_count > self.config.staged_max_files:
            # Abort: too many files staged, something is wrong
            await asyncio.create_subprocess_exec(
                "git", "reset", "HEAD",
                cwd=repo, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            self.event_bus.emit(
                "alert", task.id,
                f"Auto-commit aborted: {staged_count} files staged "
                f"(limit: {self.config.staged_max_files}). Check .gitignore."
            )
            return
        
        # Commit
        msg = self.config.git_commit_template.format(
            project=project.name,
            task_id=task.local_task_id,
            task_title=task.title,
        )
        proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", msg,
            cwd=repo, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
```

---

## 9. Review Pipeline (LLM Auto-Review, Opt-In)

Review is **not on the critical path**. Tasks synced from TASKS.md skip review entirely.
Review is only triggered explicitly for tasks created from the dashboard or when user
requests it. When triggered, it runs as a background task (not blocking the API response).

```python
# orchestrator/review_pipeline.py
class ReviewPipeline:
    """MVP: 1 primary reviewer + 1 optional adversarial, both Anthropic API.
    
    Runs as asyncio.create_task (non-blocking). Results pushed via SSE.
    Phase 2: add multi-LLM support (GPT-4o, Gemini).
    """
    
    def __init__(self, config: ReviewConfig):
        self.reviewers = [r for r in config.reviewers if r.required]
        self.optional_reviewers = [r for r in config.reviewers if not r.required]
        self.threshold = config.consensus_threshold
    
    async def review_task(
        self,
        task: Task,
        plan_content: str,
        on_progress: Callable[[int, int], None],   # (completed, total) -> SSE
    ) -> ReviewState:
        """Run review pipeline. Called via asyncio.create_task, not awaited in API handler."""
        
        # Always run required reviewers
        active_reviewers = list(self.reviewers)
        
        # Add optional adversarial reviewer for [M] and [L] complexity tasks
        if task.complexity in ("M", "L") and self.optional_reviewers:
            active_reviewers.extend(self.optional_reviewers)
        
        reviews: list[LLMReview] = []
        
        for i, reviewer in enumerate(active_reviewers):
            review = await self._call_reviewer(reviewer, task, plan_content)
            reviews.append(review)
            on_progress(i + 1, len(active_reviewers))
        
        # Synthesize (only if multiple reviews)
        if len(reviews) > 1:
            synthesis = await self._synthesize(reviews, plan_content)
            score = synthesis.score
            disagreements = synthesis.disagreements
        else:
            # Single reviewer: approve/reject is binary
            score = 1.0 if reviews[0].verdict == "approve" else 0.3
            disagreements = reviews[0].suggestions if score < self.threshold else []
        
        return ReviewState(
            rounds_total=len(active_reviewers),
            rounds_completed=len(reviews),
            reviews=reviews,
            consensus_score=score,
            human_decision_needed=(score < self.threshold),
            decision_points=disagreements,
        )
    
    async def _call_reviewer(self, reviewer, task, plan) -> LLMReview:
        """Call Anthropic API reviewer."""
        system_prompt = self._build_review_prompt(reviewer.focus)
        
        response = await self.anthropic_client.messages.create(
            model=reviewer.model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": plan}],
        )
        
        return self._parse_review(response, reviewer)
    
    async def _synthesize(self, reviews, plan) -> SynthesisResult:
        """Use Claude to synthesize multiple reviews into a consensus."""
        
        review_texts = "\n---\n".join(
            f"[{r.focus}] ({r.model}): {r.verdict}\n{r.summary}"
            for r in reviews
        )
        
        synthesis_prompt = (
            f"Given these {len(reviews)} reviews of a task plan, determine:\n"
            f"1. Consensus score (0.0-1.0)\n"
            f"2. Key disagreements (if any)\n"
            f"3. Recommended decision options for a human reviewer\n\n"
            f"Reviews:\n{review_texts}\n\n"
            f"Original plan:\n{plan}\n\n"
            f"Respond in JSON."
        )
        
        response = await self.anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": synthesis_prompt}],
        )
        
        return self._parse_synthesis(response)
```

**API integration** -- the review endpoint returns 202 immediately:

```python
# In api.py
@app.post("/api/tasks/{task_id}/review", status_code=202)
async def trigger_review(task_id: str):
    task = task_manager.get_task(task_id)
    plan = task_manager.get_task_plan_content(task)
    
    # Fire and forget -- results delivered via SSE
    asyncio.create_task(
        review_pipeline.review_task(
            task, plan,
            on_progress=lambda done, total: event_bus.emit("review_progress", task_id, done, total),
        )
    )
    
    return {"status": "review_started", "task_id": task_id}
```

---

## 10. API Endpoints (FastAPI Backend)

```python
# orchestrator/api.py

# --- Project & Task CRUD ---
GET    /api/projects                        # List all projects
GET    /api/projects/{id}                   # Project detail + its tasks
GET    /api/tasks                           # All tasks across projects (filterable)
GET    /api/tasks/{id}                      # Task detail
PATCH  /api/tasks/{id}/status               # Transition task status (drag-drop)
POST   /api/tasks/{id}/review               # Trigger review pipeline
POST   /api/tasks/{id}/review/decide        # Submit human decision
POST   /api/tasks/{id}/execute              # Force-execute (skip queue)
POST   /api/tasks/{id}/retry                # Manual retry
POST   /api/tasks/{id}/cancel               # Cancel running execution

# --- Sync ---
POST   /api/projects/{id}/sync             # Re-parse project's TASKS.md into orchestrator
POST   /api/sync-all                        # Re-parse all projects

# --- Real-time ---
GET    /api/events                          # SSE stream (log lines, status changes, alerts)

# --- Dashboard ---
GET    /api/dashboard/summary               # Aggregate stats for dashboard header
```

---

## 11. Dashboard UI (React)

### 11.1 Layout

```
+------------------------------------------------------------------+
| [logo] AI Orchestrator          [Sync All] [Settings]   3 running |
+------------------------------------------------------------------+
| Filter: [All Projects v] [All Status v]         Search: [_______] |
+------------------------------------------------------------------+
|                                                                    |
|  BACKLOG (5)    REVIEW (1)     QUEUED (2)   RUNNING (2)  DONE (8) |
| +-----------+ +-----------+ +-----------+ +-----------+ +---------+
| | P2:T-01   | | P1:T-P3-2 | | P1:T-P4-1 | | P1:T-P3-1 | | P1:...|
| | Blog:     | | Job Hunter | | Job Hunter | | Job Hunter | |       |
| | Setup     | | Add filter | | Export     | | Scraper    | |       |
| | hexo      | |            | |            | | ████░░ 60% | |       |
| | parser    | | Review:    | | Depends:   | |            | |       |
| |           | | 2/3 done   | | P1:T-P3-1  | | 0:04:32    | |       |
| |           | | [?] human  | | [waiting]  | |            | |       |
| +-----------+ +-----------+ +-----------+ +-----------+ +---------+
| | P2:T-02   |                             | P2:T-setup | |       |
| | Blog:     |                             | Blog Reorg | |       |
| | Parse MD  |                             | ██░░░░ 30% | |       |
| +-----------+                             +-----------+ +---------+
|                                                                    |
+------------------------------------------------------------------+
| Execution Log                                   [P1:T-P3-1 v]    |
| 14:32:05  [P1:T-P3-1] Running pytest... 5/7 passed               |
| 14:32:03  [P1:T-P3-1] Edited src/scrapers/base.py                |
| 14:31:58  [P2:T-setup] Created workspace directory                |
| 14:31:55  [P1:T-P3-2] Review round 2/3: gpt-4o -- approve       |
+------------------------------------------------------------------+
```

### 11.2 Card Interactions

- **Drag card** between columns = status transition (with validation)
- **Click card** = expand detail panel (full log, review results, dependencies)
- **Review card with [?]** = show decision options inline, human clicks to approve
- **Running card** = real-time progress bar + elapsed time
- **Dependency badge** = shows upstream status; greyed out if not met

### 11.3 Review Decision UI

When a review has `human_decision_needed = true`, the card expands:

```
+-----------------------------------------------+
| P1:T-P3-2  "Add filter engine"                |
| Review Score: 0.6 (below 0.8 threshold)       |
|                                                |
| Disagreement:                                  |
| "Should filtering use SQLAlchemy ORM queries   |
|  or raw SQL for performance?"                  |
|                                                |
| Claude (feasibility): ORM -- simpler, testable |
| GPT-4o (edge cases): Raw SQL -- 10x perf gain  |
| Claude (adversarial): ORM + raw SQL escape hatch|
|                                                |
| [Option A: ORM only]  [Option B: Hybrid]       |
|                       [Option C: Edit plan]     |
+-----------------------------------------------+
```

---

## 12. TASKS.md Sync Strategy

### 12.1 Design Principle: Free-Form TASKS.md

TASKS.md remains **free-form markdown**. The orchestrator does NOT require structured
fields or enforce a schema. Task content under a task header can be anything -- acceptance
criteria, notes, links, code snippets, whatever the developer writes.

The parser only extracts minimal metadata:
- **Task ID**: regex match for `T-P\d+-\d+` or similar patterns
- **Title**: the rest of the heading line after the ID
- **Status**: inferred from which section the task is under (e.g., "## Done", "## In Progress")
- **Everything else**: stored as opaque `description` text blob, passed to executor as-is

If task granularity is wrong (too coarse or too fine), that's a **planning problem**,
not an orchestration problem. Fix it in TASKS.md during design review.

### 12.2 Sync Flow

```
TASKS.md (in git repo)  ----->  Orchestrator DB
       |                              |
   Source of truth for:         Source of truth for:
   - Task definitions           - Cross-project status
   - Task content (free-form)   - Review state
   - In-project ordering        - Execution logs
                                - Global dependencies
```

**One-way sync (TASKS.md -> DB):**
1. On `POST /api/projects/{id}/sync`, orchestrator parses TASKS.md
2. New tasks -> inserted into DB with status=backlog (or queued, see 12.3)
3. Tasks marked done in TASKS.md -> updated in DB
4. Task content changed -> DB description updated
5. DB execution state is never written back to TASKS.md (executor does that via its own hooks)

### 12.3 Review Skip Rule

Tasks that are already written in a project's TASKS.md have been through
human-driven design review. They enter the orchestrator as **queued** (not backlog),
skipping the LLM review pipeline entirely.

The review pipeline is only for:
- New tasks generated by the orchestrator itself
- Tasks created from the dashboard UI without prior planning
- Explicit "request review" action from the user

---

## 13. Operational Hardening (MVP Checklist)

These measures address real risks identified during review. They are the minimum
viable hardening for a system that spawns long-running subprocess on a personal machine.
Each item is already incorporated into the relevant code sections above; this is the
cross-reference summary.

| # | Measure | Where Implemented | Effort |
|---|---------|-------------------|--------|
| H1 | **Startup recovery**: RUNNING -> FAILED on boot | Scheduler.startup_recovery() (Section 8) | ~5 lines |
| H2 | **Subprocess timeout kill**: SIGTERM -> grace period -> SIGKILL | CodeExecutor.execute() (Section 7.2) | ~15 lines |
| H3 | **Global concurrency default = 3** | orchestrator_config.yaml (Section 6.2) | Config change |
| H4 | **Per-project max_concurrency with 0 = manual** | Project model + Scheduler._project_is_busy() | ~5 lines |
| H5 | **Exponential backoff on retry**: 30s, 60s, 120s | Scheduler._run_with_retry() (Section 8) | 1 line |
| H6 | **Git staged safety check**: abort if >50 files staged | Scheduler._auto_commit() (Section 8) | ~10 lines |
| H7 | **Review runs as background task**: 202 + SSE, no blocking | API endpoint + ReviewPipeline (Section 9) | ~5 lines |
| H8 | **Review reduced to 1+1**: primary + optional adversarial | review_pipeline config (Section 6.2) | Config change |

**What we explicitly decided NOT to do** (and why):

| Rejected Measure | Why Not |
|------------------|---------|
| Subprocess tree monitoring (psutil) | Implementation complexity exceeds risk; timeout kill is sufficient |
| Git commit allowlist per project | .gitignore already covers this; staged safety check catches anomalies |
| Dedicated sleep/wake detection | Timeout kill handles hung-after-sleep processes; no extra logic needed |
| Execution journal table | existing `execution_state.started_at` provides sufficient crash forensics |
| CPU/memory governor | User adjusts concurrency manually; not a platform |

---

## 14. MVP Scope (Phase 1)

### 14.1 In Scope

| Component | What's Included |
|-----------|----------------|
| **Backend** | FastAPI server, SQLite state, project registry (YAML) |
| **Executors** | CodeExecutor only (sufficient for P0, P1, P2) |
| **Scheduler** | FIFO queue with per-project concurrency (max 1) and linear dependency check |
| **Review** | Opt-in only. Anthropic API (2 rounds). TASKS.md-synced tasks skip review. |
| **Dashboard** | Kanban board, drag-drop status transitions, execution log panel |
| **Sync** | One-way: parse TASKS.md into DB. Synced tasks enter as queued directly. |
| **Projects** | P0 (orchestrator), P1 (Job Hunter), P2 (Blog Reorg) |

### 14.2 Out of Scope (Phase 2+)

- ScheduledExecutor (cron-triggered agents)
- Multi-LLM review (GPT-4o, Gemini)
- Bidirectional TASKS.md sync (Phase 1 is one-way: TASKS.md -> DB)
- Contract validation (just dependency task status for now)
- Remote control (email/messaging triggers)
- Notification system (Slack/email alerts)
- AgentExecutor differentiation (Phase 1 uses CodeExecutor for everything)
- **Failure auto-diagnosis** (LLM-based error log analysis before retry; write to backlog)

### 14.3 MVP Task Breakdown

```
P0:T-01  [S] Project scaffold (FastAPI + React + SQLite)
P0:T-02  [M] Data model implementation (Pydantic + SQLAlchemy)
P0:T-03  [M] Project registry + YAML config loader
P0:T-04  [S] TASKS.md parser (extract tasks from existing format)
P0:T-05  [M] CodeExecutor (spawn claude CLI, stream stdout, timeout kill)
P0:T-06  [M] Scheduler (FIFO queue, per-project concurrency, deps, startup recovery, backoff)
P0:T-07  [M] Review pipeline (1+1 Anthropic-only, opt-in, async background)
P0:T-08  [L] Dashboard Kanban UI (React + Tailwind)
P0:T-09  [M] SSE event stream (log lines + status changes + alerts)
P0:T-10  [M] API endpoints (CRUD + sync + execute + review trigger)
P0:T-11  [S] Unified .env loader + env injection into executors
P0:T-12  [S] Git auto-commit with staged safety check
P0:T-13  [M] Integration testing (end-to-end: sync -> execute -> commit)
```

Note: Hardening measures (H1-H8 from Section 13) are baked into T-05, T-06, T-07, and T-12
rather than being separate tasks. This prevents them from being deprioritized.

**Dependency chain:**
```
T-01 -> T-02 -> T-03 -> T-04 (foundations)
                    |
                    +-> T-05 -> T-06 -> T-12 (execution + git)
                    |
                    +-> T-07 (review, parallel with execution)
                    |
T-01 -> T-08 -> T-09 (frontend, parallel with backend)
                    |
T-10 (API, after T-06 and T-09)
    |
    +-> T-11 -> T-13 (integration)
```

**Estimated total effort:** ~40-60 autonomous Claude Code sessions

---

## 15. Human Intervention Points (Minimized)

| Intervention | When | What Human Does | Frequency |
|---|---|---|---|
| **Review decision** | Opt-in review with consensus < 0.8 | Pick Option A/B/C from summary | Rare (most tasks skip review) |
| **Blocked resolution** | 3x retry exhausted | Read error log, edit plan or unblock | Rare |
| **New project onboarding** | Adding a project | Write YAML config, initial TASKS.md | One-time per project |
| **Dependency definition** | Cross-project dep | Add to orchestrator_config.yaml | Rare |
| **Dashboard check-in** | Periodic | Glance at board, drag cards if needed | Daily, 5 min |

**Everything else is autonomous.**

---

## 16. Open Questions

### Resolved

| # | Question | Decision |
|---|----------|----------|
| 1 | TASKS.md parser: structured or flexible? | **Free-form**. Parser extracts only ID + title + status section. Content is opaque blob. |
| 2 | Session batching: combine small tasks? | **No**. One task per session. If granularity is wrong, fix at planning stage. |
| 3 | Failure auto-diagnosis in MVP? | **No**. Added to Phase 2 backlog. Phase 1 retries with same prompt, then blocks. |
| 4 | Review skip for existing TASKS.md? | **Yes**. Tasks synced from TASKS.md go directly to queued. Review is opt-in. |
| 5 | Concurrency model? | **Per-project**: max 1 per project. Global default **3** (not 10). |
| 6 | Dashboard hosting? | **FastAPI serves both** API and static React build. Single process, single port. |
| 7 | Git commit strategy? | **Orchestrator auto-commits** with staged safety check (abort if >50 files). |
| 8 | Per-project concurrency override? | **Yes**. `max_concurrency` field in project YAML (default 1, set 0 for manual-only). |
| 9 | TASKS.md section naming? | Configurable per project via `status_sections` in YAML. Sensible defaults provided. |
| 10 | Crash recovery complexity? | **Minimal**: RUNNING -> FAILED on startup. No execution journal table. |
| 11 | Subprocess timeout handling? | **SIGTERM -> 5s grace -> SIGKILL**. No heartbeat system. |
| 12 | Retry backoff strategy? | **Exponential**: 30s, 60s, 120s. Max 3 retries. |
| 13 | Review pipeline scope for MVP? | **1 primary + 1 optional adversarial**, Anthropic API only. Async (202 + SSE). |
| 14 | Mac sleep/wake handling? | **No dedicated handling**. Timeout kill covers hung-after-sleep processes. |
| 15 | Subprocess count monitoring? | **No**. No psutil scanning. Concurrency limit is sufficient. |

### Still Open

None -- all architectural decisions resolved for MVP.

---

## Appendix A: File Structure and Serving

**Serving model**: `uvicorn src.api:app` serves both the API (`/api/*`) and the
React static build (`/` -> `frontend/build/`). Single process, single port.
Development uses Vite dev server proxying API calls to FastAPI.

```
helixos/
+-- orchestrator_config.yaml       # Global config
+-- .env                           # Unified credentials (gitignored)
+-- contracts/                     # Cross-project contract files
+-- src/
|   +-- api.py                     # FastAPI endpoints
|   +-- models.py                  # Pydantic data models
|   +-- scheduler.py               # Queue + concurrency + startup recovery
|   +-- task_manager.py            # CRUD + state machine
|   +-- review_pipeline.py         # 1+1 Anthropic review (opt-in)
|   +-- sync/
|   |   +-- tasks_parser.py        # TASKS.md parser (free-form)
|   +-- executors/
|   |   +-- base.py                # Abstract executor
|   |   +-- code_executor.py       # Claude Code in git repo (with timeout kill)
|   |   +-- agent_executor.py      # Claude in workspace (Phase 2)
|   |   +-- scheduled_executor.py  # Cron-triggered agent (Phase 2)
|   +-- events.py                  # SSE event bus
|   +-- db.py                      # SQLite + SQLAlchemy
+-- frontend/
|   +-- src/
|   |   +-- App.jsx
|   |   +-- components/
|   |   |   +-- KanbanBoard.jsx
|   |   |   +-- TaskCard.jsx
|   |   |   +-- ReviewPanel.jsx
|   |   |   +-- ExecutionLog.jsx
|   |   +-- hooks/
|   |       +-- useSSE.js
|   +-- package.json
+-- tests/
+-- scripts/
|   +-- start.sh                   # Build frontend + launch uvicorn
+-- TASKS.md
+-- PROGRESS.md
+-- claude.md
```
