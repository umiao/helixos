"""FastAPI application for HelixOS orchestrator.

Defines the FastAPI app with lifespan handler, CORS middleware, static
file serving, and all REST API endpoints per PRD Section 10.  Delegates
business logic to TaskManager, Scheduler, ReviewPipeline, and TasksParser.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.config import ProjectRegistry, load_config
from src.db import create_engine, create_session_factory, init_db
from src.env_loader import EnvLoader
from src.events import EventBus, sse_router
from src.models import Project, ReviewState, Task, TaskStatus
from src.review_pipeline import ReviewPipeline
from src.scheduler import Scheduler
from src.schemas import (
    DashboardSummary,
    ErrorResponse,
    ExecutionStateResponse,
    ProjectDetailResponse,
    ProjectResponse,
    ReviewDecisionRequest,
    ReviewStateResponse,
    StatusTransitionRequest,
    SyncAllResponse,
    SyncResponse,
    TaskResponse,
)
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import TaskManager

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("orchestrator_config.yaml")


# ------------------------------------------------------------------
# Conversion helpers
# ------------------------------------------------------------------


def _project_to_response(project: Project) -> ProjectResponse:
    """Convert a domain Project to an API response."""
    return ProjectResponse(
        id=project.id,
        name=project.name,
        repo_path=str(project.repo_path) if project.repo_path else None,
        tasks_file=project.tasks_file,
        executor_type=project.executor_type,
        max_concurrency=project.max_concurrency,
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

    # Database
    engine = create_engine(config.orchestrator.state_db_path)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    # Services
    task_manager = TaskManager(session_factory)
    registry = ProjectRegistry(config)
    env_loader = EnvLoader(config.orchestrator.unified_env_path)
    event_bus = EventBus()

    # Scheduler
    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
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
            )
        else:
            logger.warning(
                "Claude CLI exited with code %d -- review pipeline disabled",
                proc.returncode,
            )
    except FileNotFoundError:
        logger.warning(
            "Claude CLI not found in PATH -- review pipeline disabled"
        )

    # Startup recovery
    recovered = await scheduler.startup_recovery()
    if recovered > 0:
        logger.info("Recovered %d orphaned tasks", recovered)

    # Start scheduler
    await scheduler.start()

    # Store on app.state for endpoint access
    app.state.config = config
    app.state.task_manager = task_manager
    app.state.registry = registry
    app.state.env_loader = env_loader
    app.state.event_bus = event_bus
    app.state.scheduler = scheduler
    app.state.review_pipeline = review_pipeline
    app.state.engine = engine

    logger.info("HelixOS API started")
    yield

    # Shutdown
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
    projects = registry.list_projects()
    return [_project_to_response(p) for p in projects]


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

    return ProjectDetailResponse(
        id=project.id,
        name=project.name,
        repo_path=str(project.repo_path) if project.repo_path else None,
        tasks_file=project.tasks_file,
        executor_type=project.executor_type,
        max_concurrency=project.max_concurrency,
        tasks=[_task_to_response(t) for t in tasks],
    )


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
    """Get aggregate dashboard stats."""
    task_manager: TaskManager = request.app.state.task_manager
    registry: ProjectRegistry = request.app.state.registry

    all_tasks = await task_manager.list_tasks()

    by_status: dict[str, int] = {}
    running_count = 0
    for task in all_tasks:
        status_val = task.status.value
        by_status[status_val] = by_status.get(status_val, 0) + 1
        if task.status == TaskStatus.RUNNING:
            running_count += 1

    return DashboardSummary(
        total_tasks=len(all_tasks),
        by_status=by_status,
        running_count=running_count,
        project_count=len(registry.list_projects()),
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
