"""Dashboard and log viewing route endpoints.

Endpoints: GET /api/tasks/{task_id}/logs,
GET /api/tasks/{task_id}/stream-log,
GET /api/tasks/{task_id}/reviews,
GET /api/dashboard/summary.
"""

from __future__ import annotations

import contextlib
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from src.config import OrchestratorConfig, ProjectRegistry
from src.history_writer import HistoryWriter
from src.models import TaskStatus
from src.process_manager import ProcessManager
from src.schemas import (
    DashboardSummary,
    ErrorResponse,
    ExecutionLogEntry,
    ExecutionLogsResponse,
    ProjectProcessStatus,
    ReviewHistoryEntry,
    ReviewHistoryResponse,
    StreamLogResponse,
)
from src.task_manager import TaskManager

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Execution log + review history endpoints
# ------------------------------------------------------------------


@router.get(
    "/api/tasks/{task_id}/logs",
    responses={404: {"model": ErrorResponse}},
)
async def get_task_logs(
    task_id: str,
    request: Request,
    limit: int = 100,
    offset: int = 0,
    level: str | None = None,
    include_artifacts: bool = False,
) -> ExecutionLogsResponse:
    """Get paginated execution logs for a task.

    Query params:
        limit: Max entries to return (default 100).
        offset: Number of entries to skip (default 0).
        level: Optional filter by log level (info, warn, error, artifact).
        include_artifacts: If true, include raw artifact entries (default false).
    """
    task_manager: TaskManager = request.app.state.task_manager
    history_writer: HistoryWriter = request.app.state.history_writer

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    entries = await history_writer.get_logs(
        task_id, limit=limit, offset=offset, level=level,
        include_artifacts=include_artifacts,
    )
    total = await history_writer.count_logs(
        task_id, include_artifacts=include_artifacts,
    )

    return ExecutionLogsResponse(
        task_id=task_id,
        total=total,
        offset=offset,
        limit=limit,
        entries=[ExecutionLogEntry(**e) for e in entries],
    )


@router.get(
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


@router.get(
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
# Dashboard endpoint
# ------------------------------------------------------------------


@router.get("/api/dashboard/summary")
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
