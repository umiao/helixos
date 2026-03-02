"""FastAPI application for HelixOS orchestrator.

Defines the FastAPI app with lifespan handler, CORS middleware, static
file serving, and all REST API endpoints per PRD Section 10.  Delegates
business logic to TaskManager, Scheduler, ReviewPipeline, and TasksParser.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.config import ProjectRegistry, load_config
from src.config_writer import add_project_to_config, suggest_next_project_id
from src.db import create_engine, create_session_factory, init_db
from src.enrichment import enrich_task_title, is_claude_cli_available
from src.env_loader import EnvLoader
from src.events import EventBus, sse_router
from src.history_writer import HistoryWriter
from src.models import Project, ReviewState, Task, TaskStatus
from src.port_registry import PortRegistry
from src.process_manager import ProcessManager
from src.project_settings import ProjectSettingsStore
from src.project_validator import validate_project_directory
from src.review_pipeline import ReviewPipeline
from src.scheduler import Scheduler
from src.schemas import (
    BrowseEntry,
    BrowseResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    DashboardSummary,
    EnrichTaskRequest,
    EnrichTaskResponse,
    ErrorResponse,
    ExecutionLogEntry,
    ExecutionLogsResponse,
    ExecutionStateResponse,
    ImportProjectRequest,
    ImportProjectResponse,
    ProcessStatusResponse,
    ProjectDetailResponse,
    ProjectProcessStatus,
    ProjectResponse,
    ReviewDecisionRequest,
    ReviewHistoryEntry,
    ReviewHistoryResponse,
    ReviewStateResponse,
    StatusTransitionRequest,
    SyncAllResponse,
    SyncResponse,
    TaskResponse,
    ValidateProjectRequest,
    ValidateProjectResponse,
)
from src.subprocess_registry import SubprocessRegistry
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import TaskManager
from src.tasks_writer import NewTask, TasksWriter

logger = logging.getLogger(__name__)

# Windows: ProactorEventLoop required for asyncio.create_subprocess_exec
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

CONFIG_PATH = Path("orchestrator_config.yaml")


# ------------------------------------------------------------------
# Conversion helpers
# ------------------------------------------------------------------


def _project_to_response(
    project: Project, *, execution_paused: bool = False,
) -> ProjectResponse:
    """Convert a domain Project to an API response."""
    return ProjectResponse(
        id=project.id,
        name=project.name,
        repo_path=str(project.repo_path) if project.repo_path else None,
        tasks_file=project.tasks_file,
        executor_type=project.executor_type,
        max_concurrency=project.max_concurrency,
        claude_md_path=str(project.claude_md_path) if project.claude_md_path else None,
        execution_paused=execution_paused,
    )


def _task_to_response(task: Task) -> TaskResponse:
    """Convert a domain Task to an API response."""
    review_resp = None
    if task.review is not None:
        review_resp = ReviewStateResponse(
            rounds_total=task.review.rounds_total,
            rounds_completed=task.review.rounds_completed,
            consensus_score=task.review.consensus_score,
            human_decision_needed=task.review.human_decision_needed,
            decision_points=task.review.decision_points,
            human_choice=task.review.human_choice,
        )

    execution_resp = None
    if task.execution is not None:
        execution_resp = ExecutionStateResponse(
            started_at=task.execution.started_at,
            finished_at=task.execution.finished_at,
            retry_count=task.execution.retry_count,
            max_retries=task.execution.max_retries,
            exit_code=task.execution.exit_code,
            log_tail=task.execution.log_tail,
            result=task.execution.result,
            error_summary=task.execution.error_summary,
        )

    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        local_task_id=task.local_task_id,
        title=task.title,
        description=task.description,
        status=task.status,
        executor_type=task.executor_type,
        depends_on=task.depends_on,
        review=review_resp,
        execution=execution_resp,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: init services on startup, cleanup on shutdown.

    Startup:
        1. Load config from orchestrator_config.yaml
        2. Create DB engine and init tables
        3. Create all service objects (TaskManager, Registry, EnvLoader, etc.)
        4. Run startup_recovery
        5. Start scheduler tick loop

    Shutdown:
        1. Stop scheduler
        2. Dispose DB engine
    """
    # Load config
    config = load_config(CONFIG_PATH)

    # Ensure data directories exist (e.g. ~/.helixos/ for state.db and .env)
    config.orchestrator.state_db_path.parent.mkdir(parents=True, exist_ok=True)
    config.orchestrator.unified_env_path.parent.mkdir(parents=True, exist_ok=True)

    # Database
    engine = create_engine(config.orchestrator.state_db_path)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    # Services
    task_manager = TaskManager(session_factory)
    registry = ProjectRegistry(config)
    env_loader = EnvLoader(config.orchestrator.unified_env_path)
    event_bus = EventBus()
    history_writer = HistoryWriter(session_factory)

    # Port registry
    ports_path = config.orchestrator.state_db_path.parent / "ports.json"
    port_registry = PortRegistry(config.orchestrator.port_ranges, ports_path)

    # Subprocess registry (shared limit across Scheduler + ProcessManager)
    subprocess_registry = SubprocessRegistry(
        max_total=config.orchestrator.max_total_subprocesses,
    )

    # Project settings store (execution_paused persistence)
    settings_store = ProjectSettingsStore(session_factory)

    # Scheduler
    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
        history_writer=history_writer,
        settings_store=settings_store,
    )

    # Process manager (dev server lifecycle)
    process_manager = ProcessManager(
        config=config,
        registry=registry,
        port_registry=port_registry,
        subprocess_registry=subprocess_registry,
        event_bus=event_bus,
    )

    # Claude CLI check -- verify claude is available before creating pipeline
    review_pipeline: ReviewPipeline | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await proc.communicate()
        if proc.returncode == 0:
            claude_version = stdout_bytes.decode("utf-8").strip()
            logger.info("Claude CLI available: %s", claude_version)
            review_pipeline = ReviewPipeline(
                config=config.review_pipeline,
                threshold=config.orchestrator.review_consensus_threshold,
                history_writer=history_writer,
            )
        else:
            logger.warning(
                "Claude CLI exited with code %d -- review pipeline disabled",
                proc.returncode,
            )
    except (FileNotFoundError, NotImplementedError, OSError):
        logger.warning(
            "Claude CLI not found in PATH -- review pipeline disabled"
        )

    # Startup recovery
    recovered = await scheduler.startup_recovery()
    if recovered > 0:
        logger.info("Recovered %d orphaned tasks", recovered)

    # Orphan cleanup for subprocesses and ports
    subprocess_orphans = subprocess_registry.cleanup_dead()
    if subprocess_orphans:
        logger.info("Cleaned up %d orphaned subprocesses", len(subprocess_orphans))
    port_orphans = port_registry.cleanup_orphans()
    if port_orphans:
        logger.info("Cleaned up %d orphaned port assignments", len(port_orphans))
    pm_orphans = process_manager.cleanup_orphans()
    if pm_orphans:
        logger.info("Cleaned up %d orphaned dev servers", len(pm_orphans))

    # Start scheduler
    await scheduler.start()

    # Store on app.state for endpoint access
    app.state._config_path = CONFIG_PATH
    app.state.config = config
    app.state.task_manager = task_manager
    app.state.registry = registry
    app.state.env_loader = env_loader
    app.state.event_bus = event_bus
    app.state.scheduler = scheduler
    app.state.review_pipeline = review_pipeline
    app.state.history_writer = history_writer
    app.state.port_registry = port_registry
    app.state.subprocess_registry = subprocess_registry
    app.state.process_manager = process_manager
    app.state.settings_store = settings_store
    app.state.engine = engine

    logger.info("HelixOS API started")
    yield

    # Shutdown order: ProcessManager -> Scheduler -> EventBus -> DB
    await process_manager.stop_all()
    await scheduler.stop()
    await engine.dispose()
    logger.info("HelixOS API stopped")


# ------------------------------------------------------------------
# API Router (all endpoints)
# ------------------------------------------------------------------

api_router = APIRouter()


# ------------------------------------------------------------------
# Project endpoints
# ------------------------------------------------------------------


@api_router.get("/api/projects")
async def list_projects(request: Request) -> list[ProjectResponse]:
    """List all registered projects."""
    registry: ProjectRegistry = request.app.state.registry
    scheduler: Scheduler = request.app.state.scheduler
    projects = registry.list_projects()
    return [
        _project_to_response(
            p, execution_paused=scheduler.is_project_paused(p.id),
        )
        for p in projects
    ]


@api_router.get(
    "/api/projects/{project_id}",
    responses={404: {"model": ErrorResponse}},
)
async def get_project(project_id: str, request: Request) -> ProjectDetailResponse:
    """Get a project with its tasks."""
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager

    try:
        project = registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    tasks = await task_manager.list_tasks(project_id=project_id)

    scheduler: Scheduler = request.app.state.scheduler

    return ProjectDetailResponse(
        id=project.id,
        name=project.name,
        repo_path=str(project.repo_path) if project.repo_path else None,
        tasks_file=project.tasks_file,
        executor_type=project.executor_type,
        max_concurrency=project.max_concurrency,
        claude_md_path=str(project.claude_md_path) if project.claude_md_path else None,
        execution_paused=scheduler.is_project_paused(project_id),
        tasks=[_task_to_response(t) for t in tasks],
    )


# ------------------------------------------------------------------
# Filesystem browse endpoint
# ------------------------------------------------------------------


@api_router.get(
    "/api/filesystem/browse",
    responses={400: {"model": ErrorResponse}},
)
async def browse_directory(
    path: str | None = None,
) -> BrowseResponse:
    """Browse a directory, sandboxed to the user's home directory.

    Returns subdirectories with project indicator flags (has .git, TASKS.md,
    CLAUDE.md).  Hidden directories (starting with '.') are excluded.
    The path must be within the user's home directory.
    """
    home = Path.home().resolve()
    target = Path(path).expanduser().resolve() if path else home

    # Sandbox: target must be home or a descendant of home
    try:
        target.relative_to(home)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Path is outside the home directory: {target}",
        ) from None

    if not target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Not a directory: {target}",
        )

    # Compute parent (None if already at home)
    parent: str | None = None
    if target != home:
        parent = str(target.parent)

    entries: list[BrowseEntry] = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not item.is_dir():
                continue
            # Skip hidden directories
            if item.name.startswith("."):
                continue
            entries.append(BrowseEntry(
                name=item.name,
                path=str(item),
                is_dir=True,
                has_git=(item / ".git").is_dir(),
                has_tasks_md=(item / "TASKS.md").is_file(),
                has_claude_md=(item / "CLAUDE.md").is_file(),
            ))
    except PermissionError:
        raise HTTPException(
            status_code=400,
            detail=f"Permission denied: {target}",
        ) from None

    return BrowseResponse(
        path=str(target),
        parent=parent,
        entries=entries,
    )


# ------------------------------------------------------------------
# Project onboarding endpoints
# ------------------------------------------------------------------


@api_router.post(
    "/api/projects/validate",
    responses={400: {"model": ErrorResponse}},
)
async def validate_project(
    body: ValidateProjectRequest,
    request: Request,
) -> ValidateProjectResponse:
    """Validate a directory for import as a managed project."""
    config_path: Path = request.app.state._config_path  # noqa: SLF001

    directory = Path(body.path)
    if not directory.is_absolute():
        directory = directory.expanduser().resolve()

    suggested_id = suggest_next_project_id(config_path)
    result = validate_project_directory(directory, suggested_id)

    return ValidateProjectResponse(
        valid=result.valid,
        name=result.name,
        path=result.path,
        has_git=result.has_git,
        has_tasks_md=result.has_tasks_md,
        has_claude_config=result.has_claude_config,
        suggested_id=result.suggested_id,
        warnings=result.warnings,
        limited_mode_reasons=result.limited_mode_reasons,
    )


@api_router.post(
    "/api/projects/import",
    responses={
        400: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def import_project(
    body: ImportProjectRequest,
    request: Request,
) -> ImportProjectResponse:
    """Import a project into the orchestrator.

    Writes to orchestrator_config.yaml via ruamel.yaml (preserving
    comments), reloads the ProjectRegistry, auto-assigns a port,
    and triggers sync if TASKS.md is present.
    """
    config_path: Path = request.app.state._config_path  # noqa: SLF001
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager
    port_registry: PortRegistry = request.app.state.port_registry

    directory = Path(body.path).expanduser().resolve()

    # Validate path
    if not directory.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path: {directory} is not a directory",
        )

    # Determine project ID
    project_id = body.project_id or suggest_next_project_id(config_path)
    name = body.name or directory.name

    # Check for duplicate
    try:
        registry.get_project(project_id)
        raise HTTPException(
            status_code=409,
            detail=f"Project already exists: {project_id}",
        )
    except KeyError:
        pass  # Good -- not a duplicate

    # Build project data for YAML
    project_data: dict[str, object] = {
        "name": name,
        "repo_path": str(directory),
        "executor_type": "code",
        "tasks_file": "TASKS.md",
        "max_concurrency": 1,
    }
    if body.project_type != "other":
        project_data["project_type"] = body.project_type
    if body.launch_command is not None:
        project_data["launch_command"] = body.launch_command
    if body.preferred_port is not None:
        project_data["preferred_port"] = body.preferred_port

    # Auto-set claude_md_path if CLAUDE.md exists in the project directory
    claude_md_file = directory / "CLAUDE.md"
    if claude_md_file.is_file():
        project_data["claude_md_path"] = str(claude_md_file)

    # Write to YAML (atomic, comment-preserving)
    try:
        add_project_to_config(config_path, project_id, project_data)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    # Reload config and registry
    new_config = load_config(config_path)
    request.app.state.config = new_config
    new_registry = ProjectRegistry(new_config)
    request.app.state.registry = new_registry

    # Auto-assign port
    assigned_port: int | None = None
    try:
        assigned_port = port_registry.assign_port(
            project_id,
            body.project_type,
            preferred_port=body.preferred_port,
        )
    except (ValueError, RuntimeError):
        logger.warning(
            "Could not assign port for project %s", project_id, exc_info=True,
        )

    warnings: list[str] = []
    has_tasks_md = (directory / "TASKS.md").is_file()

    if not (directory / ".git").is_dir():
        warnings.append("No .git directory -- git operations will not work")
    if not has_tasks_md:
        warnings.append("No TASKS.md -- task sync skipped")
    if not (directory / "CLAUDE.md").is_file():
        warnings.append("No CLAUDE.md -- Claude context unavailable")

    # Auto-sync if TASKS.md present
    sync_result_resp = None
    synced = False
    if has_tasks_md:
        try:
            sync_result = await sync_project_tasks(
                project_id, task_manager, new_registry,
            )
            sync_result_resp = SyncResponse(
                project_id=project_id,
                added=sync_result.added,
                updated=sync_result.updated,
                unchanged=sync_result.unchanged,
                warnings=sync_result.warnings,
            )
            synced = True
        except Exception:
            logger.warning(
                "Auto-sync failed for project %s", project_id, exc_info=True,
            )
            warnings.append("Auto-sync failed -- run sync manually")

    return ImportProjectResponse(
        project_id=project_id,
        name=name,
        repo_path=str(directory),
        port=assigned_port,
        synced=synced,
        sync_result=sync_result_resp,
        warnings=warnings,
    )


# ------------------------------------------------------------------
# Task enrichment endpoint (AI-assisted via Claude CLI)
# ------------------------------------------------------------------


@api_router.post(
    "/api/tasks/enrich",
    responses={
        503: {"model": ErrorResponse},
    },
)
async def enrich_task(body: EnrichTaskRequest) -> EnrichTaskResponse:
    """Enrich a task title with AI-suggested description and priority.

    Uses Claude CLI (``claude -p``) to generate structured suggestions.
    Returns 503 if Claude CLI is not available on PATH.
    """
    if not is_claude_cli_available():
        raise HTTPException(
            status_code=503,
            detail="Claude CLI not available -- enrichment disabled",
        )

    try:
        result = await enrich_task_title(body.title)
    except RuntimeError as exc:
        logger.warning("Task enrichment failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"Enrichment failed: {exc}",
        ) from exc

    return EnrichTaskResponse(
        description=result["description"],
        priority=result["priority"],
    )


# ------------------------------------------------------------------
# Task creation endpoint (writes to TASKS.md)
# ------------------------------------------------------------------


@api_router.post(
    "/api/projects/{project_id}/tasks",
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def create_project_task(
    project_id: str,
    body: CreateTaskRequest,
    request: Request,
) -> CreateTaskResponse:
    """Create a new task by appending to the project's TASKS.md.

    Uses filelock for safe concurrent writes, creates a .bak backup,
    and auto-triggers sync to bring the new task into the database.
    """
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager

    # Verify project exists
    try:
        project = registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    # Verify repo_path and TASKS.md path
    if project.repo_path is None:
        raise HTTPException(
            status_code=400,
            detail=f"Project {project_id} has no repo_path configured",
        )

    tasks_md_path = project.repo_path / project.tasks_file
    writer = TasksWriter(tasks_md_path)

    # Create the new task
    new_task = NewTask(
        title=body.title,
        description=body.description,
        priority=body.priority,
    )
    result = writer.append_task(new_task)

    if not result.success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to write task: {result.error}",
        )

    # Auto-trigger sync to bring the new task into the database
    sync_result_resp = None
    synced = False
    try:
        sync_result = await sync_project_tasks(
            project_id, task_manager, registry,
        )
        sync_result_resp = SyncResponse(
            project_id=project_id,
            added=sync_result.added,
            updated=sync_result.updated,
            unchanged=sync_result.unchanged,
            warnings=sync_result.warnings,
        )
        synced = True
    except Exception:
        logger.warning(
            "Auto-sync failed after task creation for project %s",
            project_id,
            exc_info=True,
        )

    return CreateTaskResponse(
        task_id=result.task_id,
        success=True,
        backup_path=result.backup_path,
        synced=synced,
        sync_result=sync_result_resp,
    )


# ------------------------------------------------------------------
# Process management endpoints
# ------------------------------------------------------------------


@api_router.post(
    "/api/projects/{project_id}/launch",
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def launch_project(
    project_id: str,
    request: Request,
) -> ProcessStatusResponse:
    """Launch the dev server for a project."""
    registry: ProjectRegistry = request.app.state.registry
    pm: ProcessManager = request.app.state.process_manager

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    try:
        status = await pm.launch(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    return ProcessStatusResponse(
        running=status.running,
        pid=status.pid,
        port=status.port,
        uptime_seconds=status.uptime_seconds,
    )


@api_router.post(
    "/api/projects/{project_id}/stop",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def stop_project(
    project_id: str,
    request: Request,
) -> dict:
    """Stop the dev server for a project."""
    registry: ProjectRegistry = request.app.state.registry
    pm: ProcessManager = request.app.state.process_manager

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    stopped = await pm.stop(project_id)
    if not stopped:
        raise HTTPException(
            status_code=409,
            detail=f"No running dev server for project {project_id}",
        )

    return {"detail": "Dev server stopped", "project_id": project_id}


@api_router.get(
    "/api/projects/{project_id}/process-status",
    responses={404: {"model": ErrorResponse}},
)
async def get_process_status(
    project_id: str,
    request: Request,
) -> ProcessStatusResponse:
    """Get the dev server status for a project."""
    registry: ProjectRegistry = request.app.state.registry
    pm: ProcessManager = request.app.state.process_manager

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    status = pm.status(project_id)
    return ProcessStatusResponse(
        running=status.running,
        pid=status.pid,
        port=status.port,
        uptime_seconds=status.uptime_seconds,
    )


# ------------------------------------------------------------------
# Execution pause/resume endpoints
# ------------------------------------------------------------------


@api_router.post(
    "/api/projects/{project_id}/pause-execution",
    responses={404: {"model": ErrorResponse}},
)
async def pause_execution(
    project_id: str,
    request: Request,
) -> dict:
    """Pause task execution for a project.

    New tasks will not be dispatched while paused; in-flight tasks continue.
    The pause state is persisted to DB and survives server restarts.
    """
    registry: ProjectRegistry = request.app.state.registry
    scheduler: Scheduler = request.app.state.scheduler

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    await scheduler.pause_project(project_id)

    return {"detail": "Execution paused", "project_id": project_id, "paused": True}


@api_router.post(
    "/api/projects/{project_id}/resume-execution",
    responses={404: {"model": ErrorResponse}},
)
async def resume_execution(
    project_id: str,
    request: Request,
) -> dict:
    """Resume task execution for a project.

    Previously-paused project will dispatch tasks again on the next tick.
    """
    registry: ProjectRegistry = request.app.state.registry
    scheduler: Scheduler = request.app.state.scheduler

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    await scheduler.resume_project(project_id)

    return {"detail": "Execution resumed", "project_id": project_id, "paused": False}


# ------------------------------------------------------------------
# Task endpoints
# ------------------------------------------------------------------


@api_router.get("/api/tasks")
async def list_tasks(
    request: Request,
    project_id: str | None = None,
    status: TaskStatus | None = None,
) -> list[TaskResponse]:
    """List all tasks with optional filtering by project_id and/or status."""
    task_manager: TaskManager = request.app.state.task_manager
    tasks = await task_manager.list_tasks(project_id=project_id, status=status)
    return [_task_to_response(t) for t in tasks]


@api_router.get(
    "/api/tasks/{task_id}",
    responses={404: {"model": ErrorResponse}},
)
async def get_task(task_id: str, request: Request) -> TaskResponse:
    """Get a single task by ID."""
    task_manager: TaskManager = request.app.state.task_manager
    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _task_to_response(task)


@api_router.patch(
    "/api/tasks/{task_id}/status",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def update_task_status(
    task_id: str,
    body: StatusTransitionRequest,
    request: Request,
) -> TaskResponse:
    """Transition a task to a new status (validates state machine)."""
    task_manager: TaskManager = request.app.state.task_manager

    existing = await task_manager.get_task(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    try:
        updated = await task_manager.update_status(task_id, body.status)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    event_bus: EventBus = request.app.state.event_bus
    event_bus.emit("status_change", task_id, {"status": body.status.value})

    return _task_to_response(updated)


@api_router.post(
    "/api/tasks/{task_id}/review",
    status_code=202,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def trigger_review(task_id: str, request: Request) -> dict:
    """Trigger an async review for a task (202 Accepted)."""
    task_manager: TaskManager = request.app.state.task_manager
    review_pipeline: ReviewPipeline | None = request.app.state.review_pipeline
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if review_pipeline is None:
        raise HTTPException(
            status_code=409,
            detail="Review pipeline not available",
        )

    try:
        await task_manager.update_status(task_id, TaskStatus.REVIEW)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    event_bus.emit("status_change", task_id, {"status": "review"})

    async def _run_review() -> None:
        """Background task to run the review pipeline."""
        try:
            def on_progress(completed: int, total: int) -> None:
                event_bus.emit(
                    "review_progress",
                    task_id,
                    {"completed": completed, "total": total},
                )

            review_state = await review_pipeline.review_task(
                task=task,
                plan_content=task.description,
                on_progress=on_progress,
            )

            updated_task = task.model_copy(update={"review": review_state})
            await task_manager.update_task(updated_task)

            if review_state.human_decision_needed:
                new_status = TaskStatus.REVIEW_NEEDS_HUMAN
            else:
                new_status = TaskStatus.REVIEW_AUTO_APPROVED

            await task_manager.update_status(task_id, new_status)
            event_bus.emit(
                "status_change", task_id, {"status": new_status.value},
            )
        except Exception:
            logger.exception("Review failed for task %s", task_id)
            event_bus.emit("alert", task_id, {"error": "Review pipeline failed"})

    asyncio.create_task(_run_review())

    return {"detail": "Review started", "task_id": task_id}


@api_router.post(
    "/api/tasks/{task_id}/review/decide",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def submit_review_decision(
    task_id: str,
    body: ReviewDecisionRequest,
    request: Request,
) -> TaskResponse:
    """Submit a human review decision for a task."""
    task_manager: TaskManager = request.app.state.task_manager
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.status != TaskStatus.REVIEW_NEEDS_HUMAN:
        raise HTTPException(
            status_code=409,
            detail=f"Task is not awaiting human decision (status: {task.status.value})",
        )

    review_state = task.review if task.review is not None else ReviewState()
    review_state = review_state.model_copy(update={"human_choice": body.decision})
    updated_task = task.model_copy(update={"review": review_state})
    await task_manager.update_task(updated_task)

    # Persist human decision to review history
    history_writer: HistoryWriter = request.app.state.history_writer
    await history_writer.write_review_decision(task_id, body.decision)

    new_status = TaskStatus.QUEUED if body.decision == "approve" else TaskStatus.BACKLOG

    updated = await task_manager.update_status(task_id, new_status)
    event_bus.emit("status_change", task_id, {"status": new_status.value})

    return _task_to_response(updated)


@api_router.post(
    "/api/tasks/{task_id}/execute",
    status_code=202,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def force_execute(task_id: str, request: Request) -> dict:
    """Force-execute a task (202 Accepted). Transitions to QUEUED if possible."""
    task_manager: TaskManager = request.app.state.task_manager
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    try:
        await task_manager.update_status(task_id, TaskStatus.QUEUED)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    event_bus.emit("status_change", task_id, {"status": "queued"})

    return {"detail": "Task queued for execution", "task_id": task_id}


@api_router.post(
    "/api/tasks/{task_id}/retry",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def retry_task(task_id: str, request: Request) -> TaskResponse:
    """Reset retry count and move task back to QUEUED."""
    task_manager: TaskManager = request.app.state.task_manager
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.execution is not None:
        execution = task.execution.model_copy(update={"retry_count": 0})
        updated_task = task.model_copy(update={"execution": execution})
        await task_manager.update_task(updated_task)

    try:
        updated = await task_manager.update_status(task_id, TaskStatus.QUEUED)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    event_bus.emit("status_change", task_id, {"status": "queued"})

    return _task_to_response(updated)


@api_router.post(
    "/api/tasks/{task_id}/cancel",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def cancel_task(task_id: str, request: Request) -> dict:
    """Cancel a running task."""
    task_manager: TaskManager = request.app.state.task_manager
    scheduler: Scheduler = request.app.state.scheduler

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    cancelled = await scheduler.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail=f"Task is not currently running (status: {task.status.value})",
        )

    return {"detail": "Task cancelled", "task_id": task_id}


# ------------------------------------------------------------------
# Execution log + review history endpoints
# ------------------------------------------------------------------


@api_router.get(
    "/api/tasks/{task_id}/logs",
    responses={404: {"model": ErrorResponse}},
)
async def get_task_logs(
    task_id: str,
    request: Request,
    limit: int = 100,
    offset: int = 0,
    level: str | None = None,
) -> ExecutionLogsResponse:
    """Get paginated execution logs for a task.

    Query params:
        limit: Max entries to return (default 100).
        offset: Number of entries to skip (default 0).
        level: Optional filter by log level (info, warn, error).
    """
    task_manager: TaskManager = request.app.state.task_manager
    history_writer: HistoryWriter = request.app.state.history_writer

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    entries = await history_writer.get_logs(
        task_id, limit=limit, offset=offset, level=level,
    )
    total = await history_writer.count_logs(task_id)

    return ExecutionLogsResponse(
        task_id=task_id,
        total=total,
        offset=offset,
        limit=limit,
        entries=[ExecutionLogEntry(**e) for e in entries],
    )


@api_router.get(
    "/api/tasks/{task_id}/reviews",
    responses={404: {"model": ErrorResponse}},
)
async def get_task_reviews(
    task_id: str,
    request: Request,
    limit: int = 50,
    offset: int = 0,
) -> ReviewHistoryResponse:
    """Get paginated review history for a task.

    Query params:
        limit: Max entries to return (default 50).
        offset: Number of entries to skip (default 0).
    """
    task_manager: TaskManager = request.app.state.task_manager
    history_writer: HistoryWriter = request.app.state.history_writer

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    entries = await history_writer.get_reviews(
        task_id, limit=limit, offset=offset,
    )
    total = await history_writer.count_reviews(task_id)

    return ReviewHistoryResponse(
        task_id=task_id,
        total=total,
        offset=offset,
        limit=limit,
        entries=[ReviewHistoryEntry(**e) for e in entries],
    )


# ------------------------------------------------------------------
# Sync endpoints
# ------------------------------------------------------------------


@api_router.post(
    "/api/projects/{project_id}/sync",
    responses={404: {"model": ErrorResponse}},
)
async def sync_project(project_id: str, request: Request) -> SyncResponse:
    """Re-parse TASKS.md for a specific project."""
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    result = await sync_project_tasks(project_id, task_manager, registry)

    return SyncResponse(
        project_id=project_id,
        added=result.added,
        updated=result.updated,
        unchanged=result.unchanged,
        warnings=result.warnings,
    )


@api_router.post("/api/sync-all")
async def sync_all(request: Request) -> SyncAllResponse:
    """Re-parse TASKS.md for all projects."""
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager

    results: list[SyncResponse] = []
    for project in registry.list_projects():
        result = await sync_project_tasks(project.id, task_manager, registry)
        results.append(SyncResponse(
            project_id=project.id,
            added=result.added,
            updated=result.updated,
            unchanged=result.unchanged,
            warnings=result.warnings,
        ))

    return SyncAllResponse(results=results)


# ------------------------------------------------------------------
# Dashboard endpoint
# ------------------------------------------------------------------


@api_router.get("/api/dashboard/summary")
async def dashboard_summary(request: Request) -> DashboardSummary:
    """Get aggregate dashboard stats including per-project process status."""
    task_manager: TaskManager = request.app.state.task_manager
    registry: ProjectRegistry = request.app.state.registry
    pm: ProcessManager = request.app.state.process_manager

    all_tasks = await task_manager.list_tasks()

    by_status: dict[str, int] = {}
    running_count = 0
    for task in all_tasks:
        status_val = task.status.value
        by_status[status_val] = by_status.get(status_val, 0) + 1
        if task.status == TaskStatus.RUNNING:
            running_count += 1

    # Per-project process status
    projects = registry.list_projects()
    process_status: dict[str, ProjectProcessStatus] = {}
    for project in projects:
        ps = pm.status(project.id)
        process_status[project.id] = ProjectProcessStatus(
            running=ps.running,
            pid=ps.pid,
            port=ps.port,
            uptime_seconds=ps.uptime_seconds,
        )

    return DashboardSummary(
        total_tasks=len(all_tasks),
        by_status=by_status,
        running_count=running_count,
        project_count=len(projects),
        process_status=process_status,
    )


# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="HelixOS",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS for Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # SSE router (from T-P0-9)
    app.include_router(sse_router)

    # API routes
    app.include_router(api_router)

    # Static mount for frontend/dist/ (after API routes so API takes priority)
    frontend_dist = Path("frontend/dist")
    if frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(frontend_dist), html=True),
            name="static",
        )

    return app


# Default app instance for ``uvicorn src.api:app``
app = create_app()
