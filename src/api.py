"""FastAPI application for HelixOS orchestrator.

Defines the FastAPI app with lifespan handler, CORS middleware, static
file serving, and all REST API endpoints per PRD Section 10.  Delegates
business logic to TaskManager, Scheduler, ReviewPipeline, and TasksParser.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config import OrchestratorConfig, ProjectRegistry, load_config
from src.config_writer import add_project_to_config, suggest_next_project_id
from src.db import create_engine, create_session_factory, init_db
from src.enrichment import (
    PlanGenerationError,
    PlanGenerationErrorType,
    enrich_task_title,
    format_plan_as_text,
    generate_task_plan,
    is_claude_cli_available,
)
from src.env_loader import EnvLoader
from src.events import EventBus, sse_router
from src.history_writer import HistoryWriter
from src.models import PlanStatus, Project, ReviewLifecycleState, ReviewState, Task, TaskStatus
from src.port_registry import PortRegistry
from src.process_manager import ProcessManager
from src.process_monitor import ProcessMonitor
from src.project_settings import ProjectSettingsStore
from src.project_validator import validate_project_directory
from src.review_pipeline import ReviewPipeline
from src.scheduler import Scheduler
from src.schemas import (
    ActiveProcessesResponse,
    BrowseEntry,
    BrowseResponse,
    ConfirmGeneratedTasksResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    DashboardSummary,
    EnrichTaskRequest,
    EnrichTaskResponse,
    ErrorResponse,
    ExecutionLogEntry,
    ExecutionLogsResponse,
    ExecutionStateResponse,
    GeneratedTaskPreview,
    GenerateTasksPreviewResponse,
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
    StartAllPlannedResponse,
    StatusTransitionRequest,
    StreamLogResponse,
    SyncAllResponse,
    SyncResponse,
    TaskResponse,
    UpdateTaskRequest,
    ValidateProjectRequest,
    ValidateProjectResponse,
)
from src.subprocess_registry import SubprocessRegistry
from src.sync.tasks_parser import sync_project_tasks
from src.task_generator import (
    extract_proposals_from_plan,
    process_proposals,
    write_allocated_tasks,
)
from src.task_manager import (
    OptimisticLockError,
    PlanInvalidError,
    ReviewGateBlockedError,
    TaskManager,
)
from src.tasks_writer import NewTask, TasksWriter

logger = logging.getLogger(__name__)

# Defense-in-depth: set ProactorEventLoop on Windows for subprocess support.
# When running under uvicorn with --reload, uvicorn's setup_event_loop()
# overrides this policy BEFORE importing our module (sets SelectorEventLoop).
# The real fix is scripts/run_server.py which calls uvicorn.run(loop="none").
# This policy still protects non-uvicorn usage (pytest, direct import).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

CONFIG_PATH = Path("orchestrator_config.yaml")


# ------------------------------------------------------------------
# Conversion helpers
# ------------------------------------------------------------------


def _project_to_response(
    project: Project,
    *,
    execution_paused: bool = False,
    review_gate_enabled: bool = True,
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
        review_gate_enabled=review_gate_enabled,
        is_primary=project.is_primary,
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
        review_status=task.review_status,
        review_lifecycle_state=task.review_lifecycle_state,
    )


# ------------------------------------------------------------------
# Startup helpers
# ------------------------------------------------------------------


async def _reset_zombie_plan_status(task_manager: TaskManager) -> int:
    """Reset tasks stuck with plan_status='generating' to 'failed'.

    Called at startup to clean up zombies from a previous crash.
    Returns the number of tasks reset.
    """
    count = 0
    # Check all projects -- list_tasks returns all when no project filter
    all_tasks = await task_manager.list_tasks()
    for t in all_tasks:
        if t.plan_status == "generating":
            t.plan_status = "failed"
            await task_manager.update_task(t)
            count += 1
    return count


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
                stream_log_dir=config.orchestrator.stream_log_dir,
            )
        else:
            logger.warning(
                "Claude CLI exited with code %d -- review pipeline disabled",
                proc.returncode,
            )
    except NotImplementedError:
        logger.warning(
            "asyncio.create_subprocess_exec raised NotImplementedError -- "
            "this typically means uvicorn is using SelectorEventLoop on Windows. "
            "Use 'python scripts/run_server.py' to start with the correct "
            "event loop policy. Review pipeline disabled."
        )
    except (FileNotFoundError, OSError):
        logger.warning(
            "Claude CLI not found in PATH -- review pipeline disabled"
        )

    # Startup recovery
    recovered = await scheduler.startup_recovery()
    if recovered > 0:
        logger.info("Recovered %d orphaned tasks", recovered)

    # AC6: Reset zombie plan_status="generating" to "failed" on startup
    zombie_count = await _reset_zombie_plan_status(task_manager)
    if zombie_count > 0:
        logger.info("Reset %d zombie plan_status=generating tasks to failed", zombie_count)

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

    # Purge old execution_logs and review_history entries
    purge_counts = await history_writer.purge_old_entries(
        retention_days=config.orchestrator.log_retention_days,
    )
    if purge_counts["execution_logs"] or purge_counts["review_history"]:
        logger.info(
            "Purged %d execution logs + %d review history entries (retention=%dd)",
            purge_counts["execution_logs"],
            purge_counts["review_history"],
            config.orchestrator.log_retention_days,
        )

    # Clean up stale 0-byte log files from previous runs
    from src.executors.code_executor import cleanup_empty_log_files

    empty_removed = cleanup_empty_log_files(config.orchestrator.stream_log_dir)
    if empty_removed > 0:
        logger.info("Removed %d empty log files from %s", empty_removed, config.orchestrator.stream_log_dir)

    # Process monitor (background failure detection)
    process_monitor = ProcessMonitor(
        subprocess_registry=subprocess_registry,
        process_manager=process_manager,
        event_bus=event_bus,
    )

    # Start scheduler and process monitor
    await scheduler.start()
    await process_monitor.start()

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
    app.state.process_monitor = process_monitor
    app.state.settings_store = settings_store
    app.state.engine = engine

    logger.info("HelixOS API started")
    yield

    # Shutdown order: ProcessMonitor -> ProcessManager -> Scheduler -> DB
    await process_monitor.stop()
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
            p,
            execution_paused=scheduler.is_project_paused(p.id),
            review_gate_enabled=scheduler.is_review_gate_enabled(p.id),
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
        review_gate_enabled=scheduler.is_review_gate_enabled(project_id),
        is_primary=project.is_primary,
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

    suggested_id = suggest_next_project_id(config_path, directory.name)
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
    name = body.name or directory.name
    project_id = body.project_id or suggest_next_project_id(config_path, name)

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
                skipped=sync_result.skipped,
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
async def enrich_task(body: EnrichTaskRequest, request: Request) -> EnrichTaskResponse:
    """Enrich a task title with AI-suggested description and priority.

    Uses Claude CLI (``claude -p``) to generate structured suggestions.
    Returns 503 if Claude CLI is not available on PATH.
    """
    if not is_claude_cli_available():
        return JSONResponse(
            status_code=503,
            content={
                "detail": PlanGenerationErrorType.CLI_UNAVAILABLE.user_message,
                "error_type": PlanGenerationErrorType.CLI_UNAVAILABLE.value,
                "retryable": False,
            },
        )

    config: OrchestratorConfig = request.app.state.config
    enrichment_timeout = config.review_pipeline.enrichment_timeout_minutes

    try:
        result = await enrich_task_title(
            body.title, timeout_minutes=enrichment_timeout,
        )
    except PlanGenerationError as exc:
        logger.warning("Task enrichment failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "detail": exc.user_message,
                "error_type": exc.error_type.value,
                "retryable": exc.retryable,
            },
        )
    except RuntimeError as exc:
        logger.warning("Task enrichment failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"Enrichment failed: {exc}",
                "error_type": PlanGenerationErrorType.CLI_ERROR.value,
                "retryable": True,
            },
        )

    return EnrichTaskResponse(
        description=result["description"],
        priority=result["priority"],
    )


# ------------------------------------------------------------------
# Structured plan generation endpoint
# ------------------------------------------------------------------


@api_router.post(
    "/api/tasks/{task_id}/generate-plan",
    status_code=202,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def generate_plan(task_id: str, request: Request) -> JSONResponse:
    """Generate a structured implementation plan for a task (async).

    Launches plan generation as a background task and returns 202
    immediately.  Real-time progress is streamed via SSE ``log`` events
    (source="plan") and lifecycle transitions via ``plan_status_change``.

    Returns 503 if Claude CLI is not available.
    Returns 404 if the task does not exist.
    Returns 409 if the task already has plan_status="generating".
    """
    if not is_claude_cli_available():
        return JSONResponse(
            status_code=503,
            content={
                "detail": PlanGenerationErrorType.CLI_UNAVAILABLE.user_message,
                "error_type": PlanGenerationErrorType.CLI_UNAVAILABLE.value,
                "retryable": False,
            },
        )

    task_manager: TaskManager = request.app.state.task_manager
    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    # AC7: Idempotency guard -- reject if already generating
    if task.plan_status == "generating":
        raise HTTPException(
            status_code=409,
            detail="Plan generation already in progress for this task",
        )

    # Get repo_path from project registry for codebase context
    repo_path = None
    try:
        registry: ProjectRegistry = request.app.state.registry
        project = registry.get_project(task.project_id)
        if project.repo_path:
            repo_path = project.repo_path
    except (KeyError, AttributeError):
        logger.debug(
            "Could not resolve repo_path for project %s, "
            "generating plan without codebase context",
            task.project_id,
        )

    config: OrchestratorConfig = request.app.state.config
    enrichment_timeout = config.review_pipeline.enrichment_timeout_minutes
    event_bus: EventBus = request.app.state.event_bus
    history_writer: HistoryWriter = request.app.state.history_writer

    # Mark plan as generating + emit SSE event
    task.plan_status = "generating"
    await task_manager.update_task(task)
    event_bus.emit("plan_status_change", task_id, {"plan_status": "generating"}, origin="plan")

    # Launch background task (fire-and-forget, like review pipeline pattern)
    async def _run_plan_generation() -> None:
        try:
            def on_log(line: str) -> None:
                """Per-line callback: SSE emit + DB write."""
                event_bus.emit("log", task_id, {"message": line, "source": "plan"}, origin="plan")
                asyncio.ensure_future(
                    history_writer.write_log(
                        task_id, line, level="info", source="plan",
                    ),
                )

            async def on_raw_artifact(content: str) -> None:
                """Persist full raw CLI output before any parsing."""
                await history_writer.write_raw_artifact(
                    task_id=task_id,
                    artifact_type="plan_cli_output",
                    content=content,
                    metadata_json=json.dumps({"chars": len(content)}),
                )

            def on_plan_stream_event(event_dict: dict) -> None:
                """Emit parsed stream-json events as SSE for ConversationView."""
                event_bus.emit(
                    "execution_stream", task_id, event_dict,
                    origin="plan",
                )

            # Resolve stream log directory for JSONL persistence
            plan_log_dir: Path | None = None
            with contextlib.suppress(AttributeError, TypeError):
                plan_log_dir = config.review_pipeline.stream_log_dir

            plan_data = await generate_task_plan(
                title=task.title,
                description=task.description,
                repo_path=repo_path,
                timeout_minutes=enrichment_timeout,
                on_log=on_log,
                on_raw_artifact=on_raw_artifact,
                on_stream_event=on_plan_stream_event,
                stream_log_dir=plan_log_dir,
                task_id=task_id,
            )

            formatted = format_plan_as_text(plan_data)

            # Atomic update: description + plan_status + plan_json in one transaction
            await task_manager.update_plan(
                task_id=task_id,
                description=formatted,
                plan_status="ready",
                plan_json=json.dumps(plan_data),
            )
            event_bus.emit(
                "plan_status_change", task_id, {"plan_status": "ready"},
                origin="plan",
            )

            # Persist plan_status=ready to TASKS.md (non-fatal on failure)
            try:
                _task = await task_manager.get_task(task_id)
                if _task is not None:
                    _project = registry.get_project(_task.project_id)
                    if _project.repo_path is not None:
                        _tasks_md = _project.repo_path / _project.tasks_file
                        _writer = TasksWriter(_tasks_md)
                        if not _writer.update_task_plan_status(
                            _task.local_task_id, "ready",
                        ):
                            logger.warning(
                                "DB plan_status=ready but TASKS.md not "
                                "updated for %s", task_id,
                            )
            except Exception as _exc:
                logger.warning(
                    "DB plan_status=ready but TASKS.md not updated "
                    "for %s: %s", task_id, _exc,
                )
        except Exception as exc:
            logger.warning("Plan generation failed for %s: %s", task_id, exc)
            # Extract structured error info if available
            if isinstance(exc, PlanGenerationError):
                error_type = exc.error_type.value
                user_msg = exc.user_message
                retryable = exc.retryable
            else:
                error_type = PlanGenerationErrorType.CLI_ERROR.value
                user_msg = str(exc)
                retryable = True
            event_bus.emit("log", task_id, {
                "message": f"Plan generation failed: {user_msg}",
                "source": "plan",
            }, origin="plan")
            asyncio.ensure_future(
                history_writer.write_log(
                    task_id,
                    f"Plan generation failed [{error_type}]: {exc}",
                    level="error",
                    source="plan",
                ),
            )
            # AC5: finally guarantees terminal state -- set failed
            current = await task_manager.get_task(task_id)
            if current is not None and current.plan_status == "generating":
                current.plan_status = "failed"
                await task_manager.update_task(current)
            event_bus.emit(
                "plan_status_change", task_id, {
                    "plan_status": "failed",
                    "error_type": error_type,
                    "error_message": user_msg,
                    "retryable": retryable,
                },
                origin="plan",
            )

    asyncio.create_task(_run_plan_generation())

    return JSONResponse(
        status_code=202,
        content={"task_id": task_id, "plan_status": "generating"},
    )


# ------------------------------------------------------------------
# Task generator endpoints (proposal-to-TASKS.md pipeline)
# ------------------------------------------------------------------


@api_router.post(
    "/api/tasks/{task_id}/generate-tasks-preview",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def generate_tasks_preview(
    task_id: str,
    request: Request,
) -> GenerateTasksPreviewResponse:
    """Preview proposed tasks from a plan before writing to TASKS.md.

    Extracts ``proposed_tasks[]`` from the task's ``plan_json``,
    allocates IDs, validates dependencies, detects cycles, and
    returns a diff for human review.
    """
    task_manager: TaskManager = request.app.state.task_manager
    registry: ProjectRegistry = request.app.state.registry

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task.plan_status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} plan_status is {task.plan_status!r}, expected 'ready'",
        )

    proposals = extract_proposals_from_plan(task.plan_json)
    if not proposals:
        raise HTTPException(
            status_code=422,
            detail=f"Task {task_id} has no proposed_tasks in plan_json",
        )

    # Read TASKS.md content for ID allocation
    project = registry.get_project(task.project_id)
    if project.repo_path is None:
        raise HTTPException(
            status_code=422,
            detail=f"Project {task.project_id} has no repo_path",
        )

    tasks_md_path = project.repo_path / project.tasks_file
    if not tasks_md_path.is_file():
        raise HTTPException(
            status_code=422,
            detail=f"TASKS.md not found at {tasks_md_path}",
        )

    tasks_md_content = tasks_md_path.read_text(encoding="utf-8")

    result = process_proposals(
        proposals=proposals,
        tasks_md_content=tasks_md_content,
        parent_task_id=task.local_task_id,
    )

    if not result.success:
        raise HTTPException(status_code=422, detail=result.error or "Unknown error")

    preview_tasks = [
        GeneratedTaskPreview(
            task_id=t.task_id,
            title=t.title,
            priority=t.priority,
            complexity=t.complexity,
            depends_on=t.depends_on,
            acceptance_criteria=t.acceptance_criteria,
        )
        for t in result.allocated_tasks
    ]

    return GenerateTasksPreviewResponse(
        parent_task_id=task.local_task_id,
        tasks=preview_tasks,
        diff_text=result.diff_text,
        count=len(result.allocated_tasks),
    )


@api_router.post(
    "/api/tasks/{task_id}/confirm-generated-tasks",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def confirm_generated_tasks(
    task_id: str,
    request: Request,
) -> ConfirmGeneratedTasksResponse:
    """Confirm and write generated tasks to TASKS.md.

    Re-runs the proposal processing (to ensure fresh IDs),
    writes the tasks to TASKS.md, updates the parent task's
    plan_status, and optionally auto-pauses the pipeline.
    """
    task_manager: TaskManager = request.app.state.task_manager
    registry: ProjectRegistry = request.app.state.registry
    event_bus: EventBus = request.app.state.event_bus
    settings_store: ProjectSettingsStore = request.app.state.settings_store

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if task.plan_status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} plan_status is {task.plan_status!r}, expected 'ready'",
        )

    proposals = extract_proposals_from_plan(task.plan_json)
    if not proposals:
        raise HTTPException(
            status_code=422,
            detail=f"Task {task_id} has no proposed_tasks in plan_json",
        )

    project = registry.get_project(task.project_id)
    if project.repo_path is None:
        raise HTTPException(
            status_code=422,
            detail=f"Project {task.project_id} has no repo_path",
        )

    tasks_md_path = project.repo_path / project.tasks_file
    if not tasks_md_path.is_file():
        raise HTTPException(
            status_code=422,
            detail=f"TASKS.md not found at {tasks_md_path}",
        )

    tasks_md_content = tasks_md_path.read_text(encoding="utf-8")

    # Re-process with fresh content (IDs may have changed since preview)
    result = process_proposals(
        proposals=proposals,
        tasks_md_content=tasks_md_content,
        parent_task_id=task.local_task_id,
    )

    if not result.success:
        raise HTTPException(status_code=422, detail=result.error or "Unknown error")

    # Write tasks to TASKS.md
    writer = TasksWriter(tasks_md_path)
    write_result = write_allocated_tasks(writer, result.allocated_tasks)

    if not write_result.success:
        raise HTTPException(
            status_code=500,
            detail=write_result.error or "Failed to write tasks",
        )

    # Update parent task plan_status to reflect decomposition complete
    task.plan_status = "decomposed"
    await task_manager.update_task(task)

    # Auto-pause pipeline (configurable via config, default: True)
    auto_paused = False
    config: OrchestratorConfig = request.app.state.config
    auto_pause_enabled = getattr(
        config.orchestrator, "auto_pause_after_task_generation", True,
    )
    if auto_pause_enabled:
        await settings_store.set_paused(task.project_id, paused=True)
        auto_paused = True
        logger.info(
            "Auto-paused project %s after task generation from %s",
            task.project_id, task_id,
        )

    # Emit events for frontend sync
    event_bus.emit(
        "plan_status_change", task_id,
        {"plan_status": "decomposed"},
        origin="task_generator",
    )
    event_bus.emit(
        "board_sync", task_id,
        {"reason": "task_generation", "count": len(write_result.written_ids)},
        origin="task_generator",
    )

    return ConfirmGeneratedTasksResponse(
        parent_task_id=task.local_task_id,
        written_ids=write_result.written_ids,
        auto_paused=auto_paused,
        detail=f"Generated {len(write_result.written_ids)} tasks from {task.local_task_id}",
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
    sync_error_msg: str | None = None
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
    except Exception as exc:
        sync_error_msg = str(exc)
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
        sync_error=sync_error_msg,
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


@api_router.get("/api/processes/status")
async def list_active_processes(
    request: Request,
) -> ActiveProcessesResponse:
    """Health-check endpoint: list all active subprocesses.

    Returns PID, start_time, elapsed time, subprocess type, and project ID
    for every tracked subprocess.
    """
    monitor: ProcessMonitor = request.app.state.process_monitor
    processes = monitor.get_active_processes()
    return ActiveProcessesResponse(
        processes=processes,  # type: ignore[arg-type]
        total=len(processes),
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
# Start all planned tasks
# ------------------------------------------------------------------


@api_router.post(
    "/api/projects/{project_id}/start-all-planned",
    responses={404: {"model": ErrorResponse}},
)
async def start_all_planned(
    project_id: str,
    request: Request,
) -> StartAllPlannedResponse:
    """Batch-move all BACKLOG tasks with plan_status=ready into the pipeline.

    Review gate ON: tasks move to REVIEW (triggers review pipeline).
    Review gate OFF: tasks move to QUEUED (scheduler picks up).
    Uses optimistic locking (expected_updated_at) per-task for concurrent safety.
    """
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager
    scheduler: Scheduler = request.app.state.scheduler
    event_bus: EventBus = request.app.state.event_bus
    review_pipeline: ReviewPipeline | None = request.app.state.review_pipeline
    history_writer: HistoryWriter = request.app.state.history_writer

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    # Fetch all BACKLOG tasks with plan_status=ready
    all_tasks = await task_manager.list_tasks(project_id=project_id, status=TaskStatus.BACKLOG)
    planned_tasks = [t for t in all_tasks if t.plan_status == PlanStatus.READY]

    gate_enabled = scheduler.is_review_gate_enabled(project_id)
    target_status = TaskStatus.REVIEW if gate_enabled else TaskStatus.QUEUED

    started = 0
    skipped_details: list[dict] = []

    for task in planned_tasks:
        try:
            updated = await task_manager.update_status(
                task.id,
                target_status,
                review_gate_enabled=gate_enabled,
                expected_updated_at=task.updated_at.isoformat(),
            )
            started += 1

            event_bus.emit(
                "status_change", task.id,
                {"status": target_status.value}, origin="api",
            )

            # Enqueue review pipeline when entering REVIEW
            if target_status == TaskStatus.REVIEW:
                _enqueue_review_pipeline(
                    task_manager, review_pipeline, event_bus, updated, task.id,
                    history_writer=history_writer,
                )

        except OptimisticLockError:
            skipped_details.append({
                "task_id": task.id,
                "reason": "concurrent_edit",
                "message": f"Task {task.id} was updated by another request",
            })
        except (ValueError, ReviewGateBlockedError, PlanInvalidError) as exc:
            skipped_details.append({
                "task_id": task.id,
                "reason": "transition_error",
                "message": str(exc),
            })

    # Emit a single board_sync after the batch operation so the frontend
    # can refresh the entire board in one shot.
    if started > 0:
        event_bus.emit(
            "board_sync", project_id,
            {"trigger": "start_all_planned", "started": started},
            origin="api",
        )

    return StartAllPlannedResponse(
        project_id=project_id,
        started=started,
        skipped=len(skipped_details),
        skipped_details=skipped_details,
        detail=f"Started {started} planned task(s)",
    )


# ------------------------------------------------------------------
# Review gate endpoint
# ------------------------------------------------------------------


@api_router.patch(
    "/api/projects/{project_id}/review-gate",
    responses={404: {"model": ErrorResponse}},
)
async def set_review_gate(
    project_id: str,
    request: Request,
    enabled: bool = True,
) -> dict:
    """Toggle the review gate for a project.

    When enabled (default), tasks must pass through REVIEW before QUEUED
    and the scheduler verifies an approved review record before executing.
    """
    registry: ProjectRegistry = request.app.state.registry
    scheduler: Scheduler = request.app.state.scheduler

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    if enabled:
        await scheduler.enable_review_gate(project_id)
    else:
        await scheduler.disable_review_gate(project_id)

    return {
        "detail": f"Review gate {'enabled' if enabled else 'disabled'}",
        "project_id": project_id,
        "review_gate_enabled": enabled,
    }


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
    "/api/tasks/{task_id}",
    responses={404: {"model": ErrorResponse}},
)
async def update_task_fields(
    task_id: str,
    body: UpdateTaskRequest,
    request: Request,
) -> TaskResponse:
    """Update a task's title and/or description."""
    task_manager: TaskManager = request.app.state.task_manager
    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    changed = False
    if body.title is not None and body.title != task.title:
        task.title = body.title
        changed = True
    if body.description is not None and body.description != task.description:
        task.description = body.description
        changed = True

    if changed:
        task = await task_manager.update_task(task)

    return _task_to_response(task)


def _enqueue_review_pipeline(
    task_manager: TaskManager,
    review_pipeline: ReviewPipeline | None,
    event_bus: EventBus,
    task: Task,
    task_id: str,
    review_attempt: int = 1,
    human_feedback: list[dict] | None = None,
    history_writer: HistoryWriter | None = None,
) -> None:
    """Enqueue the review pipeline as a background asyncio task.

    If the pipeline is unavailable (Claude CLI not found), immediately
    marks review_status as "failed" and emits an SSE alert.

    Args:
        task_manager: TaskManager for status updates.
        review_pipeline: The ReviewPipeline instance (or None if unavailable).
        event_bus: EventBus for SSE events.
        task: The task to review.
        task_id: Task ID string.
        review_attempt: Attempt number (1-based). Retries increment this.
        human_feedback: Optional list of previous human feedback for injection.
        history_writer: Optional HistoryWriter for DB persistence of review logs.
    """
    if review_pipeline is None:
        # Pipeline unavailable -- fail immediately
        asyncio.create_task(
            _set_review_failed(task_manager, event_bus, task_id,
                               "Review pipeline unavailable (Claude CLI not found)")
        )
        return

    event_bus.emit("review_started", task_id, {}, origin="review")

    async def _run_review_bg() -> None:
        """Background task to run the review pipeline."""
        try:
            # Set lifecycle state to RUNNING at pipeline start
            await task_manager.set_review_lifecycle_state(
                task_id, ReviewLifecycleState.RUNNING,
            )

            def on_progress(completed: int, total: int, phase: str) -> None:
                event_bus.emit(
                    "review_progress",
                    task_id,
                    {"completed": completed, "total": total, "phase": phase},
                    origin="review",
                )
                if history_writer is not None:
                    asyncio.ensure_future(
                        history_writer.write_log(
                            task_id, phase, level="info", source="review",
                        ),
                    )

            def on_review_log(line: str) -> None:
                """Per-line callback: SSE emit + DB write (source=review)."""
                event_bus.emit(
                    "log", task_id, {"message": line, "source": "review"},
                    origin="review",
                )
                if history_writer is not None:
                    asyncio.ensure_future(
                        history_writer.write_log(
                            task_id, line, level="info", source="review",
                        ),
                    )

            async def on_review_raw_artifact(content: str) -> None:
                """Persist full raw CLI output before any parsing."""
                if history_writer is not None:
                    await history_writer.write_raw_artifact(
                        task_id=task_id,
                        artifact_type="review_cli_output",
                        content=content,
                        metadata_json=json.dumps({"chars": len(content)}),
                    )

            def on_review_stream_event(event_dict: dict) -> None:
                """Emit parsed stream-json events as SSE for ConversationView."""
                event_bus.emit(
                    "execution_stream", task_id, event_dict,
                    origin="review",
                )

            review_state = await review_pipeline.review_task(
                task=task,
                plan_content=task.description,
                on_progress=on_progress,
                review_attempt=review_attempt,
                human_feedback=human_feedback,
                on_log=on_review_log,
                on_raw_artifact=on_review_raw_artifact,
                on_stream_event=on_review_stream_event,
            )

            updated_task = task.model_copy(update={"review": review_state})
            await task_manager.update_task(updated_task)

            # Mark review_status as "done"
            await task_manager.set_review_status(task_id, "done")

            # Set lifecycle state to the terminal state computed by pipeline
            await task_manager.set_review_lifecycle_state(
                task_id,
                ReviewLifecycleState(review_state.lifecycle_state),
            )

            if review_state.human_decision_needed:
                new_status = TaskStatus.REVIEW_NEEDS_HUMAN
            else:
                new_status = TaskStatus.REVIEW_AUTO_APPROVED

            await task_manager.update_status(task_id, new_status)
            event_bus.emit(
                "status_change", task_id, {"status": new_status.value},
                origin="review",
            )
        except Exception as exc:
            logger.exception("Review failed for task %s", task_id)
            error_msg = f"Review pipeline failed: {exc}"
            event_bus.emit(
                "log", task_id,
                {"message": error_msg, "source": "review"},
                origin="review",
            )
            if history_writer is not None:
                asyncio.ensure_future(
                    history_writer.write_log(
                        task_id, error_msg, level="error", source="review",
                    ),
                )
            await _set_review_failed(
                task_manager, event_bus, task_id, "Review pipeline failed",
            )

    asyncio.create_task(_run_review_bg())


async def _set_review_failed(
    task_manager: TaskManager,
    event_bus: EventBus,
    task_id: str,
    error_msg: str,
) -> None:
    """Mark review_status as failed, set lifecycle state to FAILED, and emit SSE alert."""
    try:
        await task_manager.set_review_status(task_id, "failed")
        await task_manager.set_review_lifecycle_state(
            task_id, ReviewLifecycleState.FAILED,
        )
    except Exception:
        logger.exception("Failed to set review status for task %s", task_id)
    event_bus.emit("alert", task_id, {"error": error_msg}, origin="review")
    event_bus.emit("review_failed", task_id, {"error": error_msg}, origin="review")


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
    """Transition a task to a new status (validates state machine).

    Supports bidirectional transitions (backward drags) with optional
    *reason* and optimistic concurrency via *expected_updated_at*.
    """
    task_manager: TaskManager = request.app.state.task_manager
    scheduler: Scheduler = request.app.state.scheduler

    existing = await task_manager.get_task(task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    # Pass review gate state so TaskManager can enforce Layer 1
    gate_enabled = scheduler.is_review_gate_enabled(existing.project_id)

    try:
        updated = await task_manager.update_status(
            task_id, body.status,
            review_gate_enabled=gate_enabled,
            reason=body.reason,
            expected_updated_at=body.expected_updated_at,
        )
    except ReviewGateBlockedError as exc:
        return JSONResponse(
            status_code=428,
            content={
                "detail": str(exc),
                "gate_action": "review_required",
                "task_id": task_id,
            },
        )
    except PlanInvalidError as exc:
        return JSONResponse(
            status_code=428,
            content={
                "detail": str(exc),
                "gate_action": "plan_invalid",
                "task_id": task_id,
            },
        )
    except OptimisticLockError:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Task was just updated by another request. Refreshing...",
                "conflict": True,
                "task_id": task_id,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    event_bus: EventBus = request.app.state.event_bus
    event_bus.emit("status_change", task_id, {"status": body.status.value}, origin="api")
    event_bus.emit("board_sync", task_id, {"trigger": "status_change"}, origin="api")

    # Auto-cancel execution when a RUNNING task is moved away.
    # The scheduler's cancel_task() terminates the SDK query, cancels the
    # asyncio task, and cleans up internal tracking.  Status is already
    # updated above, so cancel_task's own FAILED transition is harmlessly
    # suppressed by its contextlib.suppress(ValueError).
    if existing.status == TaskStatus.RUNNING and body.status != TaskStatus.RUNNING:
        cancelled = await scheduler.cancel_task(task_id)
        if cancelled:
            logger.info("Auto-cancelled execution for task %s (moved to %s)", task_id, body.status.value)

    # Transition-driven pipeline trigger: enqueue review when entering REVIEW
    if (
        existing.status != TaskStatus.REVIEW
        and body.status == TaskStatus.REVIEW
    ):
        review_pipeline: ReviewPipeline | None = request.app.state.review_pipeline
        hw: HistoryWriter = request.app.state.history_writer
        _enqueue_review_pipeline(
            task_manager, review_pipeline, event_bus, updated, task_id,
            history_writer=hw,
        )

    return _task_to_response(updated)


@api_router.post(
    "/api/tasks/{task_id}/review",
    status_code=202,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def retry_review(task_id: str, request: Request) -> dict:
    """Retry the review pipeline for a task (202 Accepted).

    Only works when ``review_status`` is ``"failed"`` or ``"idle"``.
    Returns 409 if the pipeline is already running.

    Each retry creates NEW ReviewHistoryRow entries with incremented
    ``review_attempt``. First attempt = 1, retry = 2, etc.
    """
    task_manager: TaskManager = request.app.state.task_manager
    review_pipeline: ReviewPipeline | None = request.app.state.review_pipeline
    event_bus: EventBus = request.app.state.event_bus
    history_writer: HistoryWriter = request.app.state.history_writer

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.review_status == "running":
        raise HTTPException(
            status_code=409,
            detail="Review pipeline is already running for this task",
        )

    if task.review_status not in ("failed", "idle"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot retry review: review_status is '{task.review_status}'",
        )

    # Determine next review_attempt number
    max_attempt = await history_writer.get_max_review_attempt(task_id)
    next_attempt = max_attempt + 1

    # Fetch all previous human feedback for injection into re-review
    feedback = await history_writer.get_human_feedback(task_id)

    # Ensure task is in REVIEW status; if not, transition it
    if task.status != TaskStatus.REVIEW:
        try:
            task = await task_manager.update_status(task_id, TaskStatus.REVIEW)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        event_bus.emit("status_change", task_id, {"status": "review"}, origin="api")
        event_bus.emit("board_sync", task_id, {"trigger": "status_change"}, origin="api")
    else:
        # Already in REVIEW, just reset review_status to running
        await task_manager.set_review_status(task_id, "running")

    _enqueue_review_pipeline(
        task_manager, review_pipeline, event_bus, task, task_id,
        review_attempt=next_attempt,
        human_feedback=feedback if feedback else None,
        history_writer=history_writer,
    )

    return {"detail": "Review retry started", "task_id": task_id}


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
    """Submit a human review decision for a task.

    Supported decisions:
    - ``approve`` -> QUEUED (reason optional)
    - ``reject`` -> BACKLOG (reason optional)
    - ``request_changes`` -> REVIEW with review_status=idle (reason REQUIRED)
    """
    task_manager: TaskManager = request.app.state.task_manager
    scheduler: Scheduler = request.app.state.scheduler
    event_bus: EventBus = request.app.state.event_bus

    if body.decision not in ("approve", "reject", "request_changes"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision: '{body.decision}'. "
                   "Must be 'approve', 'reject', or 'request_changes'.",
        )

    # request_changes requires a non-empty reason
    if body.decision == "request_changes" and not body.reason.strip():
        raise HTTPException(
            status_code=400,
            detail="Reason is required when requesting changes.",
        )

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

    # Persist human decision (and optional reason) to review history
    history_writer: HistoryWriter = request.app.state.history_writer
    await history_writer.write_review_decision(task_id, body.decision, reason=body.reason)

    # Determine target status based on decision
    if body.decision == "approve":
        new_status = TaskStatus.QUEUED
    elif body.decision == "request_changes":
        new_status = TaskStatus.REVIEW  # stays in REVIEW with idle review_status
    else:
        new_status = TaskStatus.BACKLOG

    # Pass review gate for defense-in-depth (REVIEW_NEEDS_HUMAN -> QUEUED
    # is allowed even with gate on, since the task already went through review)
    gate_enabled = scheduler.is_review_gate_enabled(task.project_id)
    try:
        updated = await task_manager.update_status(
            task_id, new_status, review_gate_enabled=gate_enabled,
        )
    except ReviewGateBlockedError as exc:
        return JSONResponse(
            status_code=428,
            content={
                "detail": str(exc),
                "gate_action": "review_required",
                "task_id": task_id,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    # For request_changes: set review_status to idle (not running)
    # The state machine sets it to "running" when entering REVIEW,
    # but request_changes means the user wants to edit before re-review.
    if body.decision == "request_changes":
        await task_manager.set_review_status(task_id, "idle")

    event_bus.emit("status_change", task_id, {"status": new_status.value}, origin="api")
    event_bus.emit("board_sync", task_id, {"trigger": "status_change"}, origin="api")

    # Re-fetch to get the updated review_status
    if body.decision == "request_changes":
        updated = await task_manager.get_task(task_id)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

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
    scheduler: Scheduler = request.app.state.scheduler
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    gate_enabled = scheduler.is_review_gate_enabled(task.project_id)
    try:
        await task_manager.update_status(
            task_id, TaskStatus.QUEUED, review_gate_enabled=gate_enabled,
        )
    except ReviewGateBlockedError as exc:
        return JSONResponse(
            status_code=428,
            content={
                "detail": str(exc),
                "gate_action": "review_required",
                "task_id": task_id,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    event_bus.emit("status_change", task_id, {"status": "queued"}, origin="api")
    event_bus.emit("board_sync", task_id, {"trigger": "status_change"}, origin="api")

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
    scheduler: Scheduler = request.app.state.scheduler
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.execution is not None:
        execution = task.execution.model_copy(update={"retry_count": 0})
        updated_task = task.model_copy(update={"execution": execution})
        await task_manager.update_task(updated_task)

    gate_enabled = scheduler.is_review_gate_enabled(task.project_id)
    try:
        updated = await task_manager.update_status(
            task_id, TaskStatus.QUEUED, review_gate_enabled=gate_enabled,
        )
    except ReviewGateBlockedError as exc:
        return JSONResponse(
            status_code=428,
            content={
                "detail": str(exc),
                "gate_action": "review_required",
                "task_id": task_id,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    event_bus.emit("status_change", task_id, {"status": "queued"}, origin="api")
    event_bus.emit("board_sync", task_id, {"trigger": "status_change"}, origin="api")

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


@api_router.delete(
    "/api/tasks/{task_id}",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def delete_task(
    task_id: str,
    request: Request,
    force: bool = False,
) -> JSONResponse:
    """Soft-delete a task.

    Query params:
        force: If true, delete even if task has active dependents.

    Returns 204 on success, 404 if not found, 409 if RUNNING or has
    dependents (without force). The 409 body includes a ``dependents``
    list when blocked by dependent tasks.
    """
    task_manager: TaskManager = request.app.state.task_manager

    try:
        await task_manager.delete_task(task_id, force=force)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg) from None

        # Check if it's a dependents issue -- include the list for UI
        if "active dependents" in msg:
            dependents = await task_manager.get_dependents(task_id)
            return JSONResponse(
                status_code=409,
                content={
                    "detail": msg,
                    "dependents": dependents,
                },
            )
        raise HTTPException(status_code=409, detail=msg) from None

    event_bus: EventBus = request.app.state.event_bus
    event_bus.emit("task_deleted", task_id, {"task_id": task_id}, origin="api")
    event_bus.emit("board_sync", task_id, {"trigger": "task_deleted"}, origin="api")

    return JSONResponse(status_code=204, content=None)


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
    "/api/tasks/{task_id}/stream-log",
    responses={404: {"model": ErrorResponse}},
)
async def get_task_stream_log(
    task_id: str,
    request: Request,
) -> StreamLogResponse:
    """Get the most recent stream-json JSONL log for a task.

    Returns parsed events from the most recent JSONL file in
    ``data/logs/{task_id}/``.
    """
    config: OrchestratorConfig = request.app.state.config
    task_manager: TaskManager = request.app.state.task_manager

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    log_dir = (
        config.orchestrator.stream_log_dir / task_id.replace(":", "_")
    )
    if not log_dir.is_dir():
        return StreamLogResponse(task_id=task_id, file="", events=[])

    # Find the most recent JSONL file
    jsonl_files = sorted(log_dir.glob("stream_*.jsonl"))
    if not jsonl_files:
        return StreamLogResponse(task_id=task_id, file="", events=[])

    latest = jsonl_files[-1]
    events: list[dict] = []
    with open(latest, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                with contextlib.suppress(json.JSONDecodeError, ValueError):
                    events.append(json.loads(stripped))

    return StreamLogResponse(
        task_id=task_id,
        file=latest.name,
        events=events,
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
        skipped=result.skipped,
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
            skipped=result.skipped,
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
