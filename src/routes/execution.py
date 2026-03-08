"""Execution management route endpoints.

Endpoints: POST /api/projects/{project_id}/launch,
POST /api/projects/{project_id}/stop,
GET /api/projects/{project_id}/process-status,
GET /api/processes/status,
POST /api/projects/{project_id}/pause-execution,
POST /api/projects/{project_id}/resume-execution,
POST /api/projects/{project_id}/start-all-planned,
POST /api/tasks/{task_id}/execute,
POST /api/tasks/{task_id}/retry,
POST /api/tasks/{task_id}/cancel.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api_helpers import _task_to_response
from src.config import ProjectRegistry
from src.events import EventBus
from src.history_writer import HistoryWriter
from src.models import PlanStatus, TaskStatus
from src.process_manager import ProcessManager
from src.process_monitor import ProcessMonitor
from src.review_pipeline import ReviewPipeline
from src.routes.reviews import _enqueue_review_pipeline
from src.scheduler import Scheduler
from src.schemas import (
    ActiveProcessesResponse,
    ErrorResponse,
    ProcessStatusResponse,
    StartAllPlannedResponse,
    TaskResponse,
)
from src.task_manager import (
    OptimisticLockError,
    PlanInvalidError,
    ReviewGateBlockedError,
    TaskManager,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Process management endpoints
# ------------------------------------------------------------------


@router.post(
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


@router.post(
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


@router.get(
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


@router.get("/api/processes/status")
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


@router.post(
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


@router.post(
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


@router.post(
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
# Task execution endpoints
# ------------------------------------------------------------------


@router.post(
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


@router.post(
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


@router.post(
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
