"""Task CRUD and enrichment route endpoints.

Endpoints: POST /api/projects/{project_id}/tasks, GET /api/tasks,
GET /api/tasks/{task_id}, PATCH /api/tasks/{task_id},
POST /api/tasks/enrich, POST /api/tasks/{task_id}/generate-plan,
POST /api/tasks/{task_id}/confirm-generated-tasks,
DELETE /api/tasks/{task_id}, DELETE /api/tasks/{task_id}/plan.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api_helpers import _task_to_response
from src.config import OrchestratorConfig, ProjectRegistry
from src.enrichment import (
    PlanGenerationError,
    PlanGenerationErrorType,
    enrich_task_title,
    format_plan_as_text,
    generate_task_plan,
    is_claude_cli_available,
)
from src.events import EventBus
from src.history_writer import HistoryWriter
from src.models import ReviewLifecycleState, TaskStatus
from src.project_settings import ProjectSettingsStore
from src.review_pipeline import ReviewPipeline
from src.schemas import (
    ConfirmGeneratedTasksResponse,
    CreateTaskRequest,
    CreateTaskResponse,
    EnrichTaskRequest,
    EnrichTaskResponse,
    ErrorResponse,
    SyncResponse,
    TaskResponse,
    UpdateTaskRequest,
)
from src.sync.task_store_bridge import TaskStoreBridge
from src.sync.tasks_parser import sync_project_tasks
from src.task_generator import (
    extract_proposals_from_plan,
    process_proposals,
)
from src.task_manager import TaskManager

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Task enrichment endpoint (AI-assisted via Claude CLI)
# ------------------------------------------------------------------


@router.post(
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
        title=result.get("title", ""),
        description=result["description"],
        priority=result["priority"],
    )


# ------------------------------------------------------------------
# Structured plan generation endpoint
# ------------------------------------------------------------------


@router.post(
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

    # Mark plan as generating with generation_id for race protection
    generation_id = uuid.uuid4().hex
    await task_manager.set_plan_state(
        task_id, "generating", plan_generation_id=generation_id,
    )
    event_bus.emit(
        "plan_status_change", task_id,
        {"plan_status": "generating", "generation_id": generation_id},
        origin="plan",
    )

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
                plan_validation=config.orchestrator.plan_validation,
                complexity_hint=task.complexity,
            )

            formatted = format_plan_as_text(plan_data)
            plan_data_json = json.dumps(plan_data)

            # Check generation_id match before writing -- mismatch means
            # a newer generation was started, discard this result silently
            current_task = await task_manager.get_task(task_id)
            if (
                current_task is None
                or current_task.plan_generation_id != generation_id
            ):
                logger.info(
                    "Plan generation_id mismatch for %s, discarding stale result",
                    task_id,
                )
                return

            # Infer task complexity from plan structure
            inferred_complexity = "S"
            if isinstance(plan_data, dict):
                proposed = plan_data.get("proposed_tasks", [])
                num_steps = len(plan_data.get("steps", []))
                if len(proposed) > 3 or num_steps > 8:
                    inferred_complexity = "L"
                elif len(proposed) > 0 or num_steps > 4:
                    inferred_complexity = "M"

            await task_manager.set_plan_state(
                task_id=task_id,
                new_status="ready",
                plan_generation_id=generation_id,
                description=formatted,
                plan_json=plan_data_json,
                complexity=inferred_complexity,
            )

            # AC1 (T-P1-116): Include proposed_tasks in SSE event
            proposed_tasks_payload: list[dict] = []
            if isinstance(plan_data, dict):
                for pt in plan_data.get("proposed_tasks", []):
                    if isinstance(pt, dict):
                        proposed_tasks_payload.append({
                            "title": pt.get("title", ""),
                            "description": pt.get("description", ""),
                            "files": pt.get("files", []),
                            "suggested_priority": pt.get("suggested_priority", "P1"),
                            "suggested_complexity": pt.get("suggested_complexity", "M"),
                            "dependencies": pt.get("dependencies", []),
                            "acceptance_criteria": pt.get("acceptance_criteria", []),
                        })
            event_bus.emit(
                "plan_status_change", task_id, {
                    "plan_status": "ready",
                    "generation_id": generation_id,
                    "proposed_tasks": proposed_tasks_payload,
                    "description": formatted,
                },
                origin="plan",
            )

            # Auto-trigger review after plan generation (T-P1-165)
            # First transition BACKLOG -> REVIEW (race-safe, bypass gate)
            # Then enqueue the review pipeline.
            try:
                refreshed = await task_manager.update_status(
                    task_id,
                    TaskStatus.REVIEW,
                    expected_status=TaskStatus.BACKLOG,
                    review_gate_enabled=False,
                )
                if refreshed.status == TaskStatus.REVIEW:
                    event_bus.emit(
                        "status_change", task_id, {"status": "review"},
                        origin="plan",
                    )

                    # Enqueue review if not already running
                    # (inside status guard: only when BACKLOG->REVIEW succeeded)
                    refreshed = await task_manager.get_task(task_id)
                    if refreshed is not None:
                        from src.routes.reviews import (
                            _enqueue_review_pipeline,
                            _resolve_repo_path,
                        )
                        rlc = refreshed.review_lifecycle_state
                        if rlc != ReviewLifecycleState.RUNNING.value:
                            rp: ReviewPipeline | None = getattr(
                                request.app.state, "review_pipeline", None,
                            )
                            _enqueue_review_pipeline(
                                task_manager, rp, event_bus, refreshed, task_id,
                                history_writer=history_writer,
                                repo_path=_resolve_repo_path(
                                    refreshed, request,
                                ),
                            )
                            logger.info(
                                "Auto-triggered review for %s after plan ready",
                                task_id,
                            )
                        else:
                            logger.info(
                                "Skipped auto-review for %s: already running",
                                task_id,
                            )
                else:
                    logger.info(
                        "Skipped auto-review for %s: transition to REVIEW "
                        "was no-op (current status: %s)",
                        task_id, refreshed.status.value,
                    )
            except Exception as auto_rev_exc:
                logger.warning(
                    "Auto-review trigger failed for %s: %s",
                    task_id, auto_rev_exc,
                )

            # plan_status is a state.db-only concept; tasks.db does not
            # store it, so no TASKS.md writeback needed.
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
            # Only transition if generation_id still matches (not superseded)
            current = await task_manager.get_task(task_id)
            if (
                current is not None
                and current.plan_status == "generating"
                and current.plan_generation_id == generation_id
            ):
                await task_manager.set_plan_state(
                    task_id, "failed",
                    description=current.description or None,
                )
            event_bus.emit(
                "plan_status_change", task_id, {
                    "plan_status": "failed",
                    "generation_id": generation_id,
                    "error_type": error_type,
                    "error_message": user_msg,
                    "retryable": retryable,
                },
                origin="plan",
            )

    asyncio.create_task(_run_plan_generation())

    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "plan_status": "generating",
            "generation_id": generation_id,
        },
    )


# ------------------------------------------------------------------
# Plan rejection endpoint (T-P1-116 AC4)
# ------------------------------------------------------------------


@router.post(
    "/api/tasks/{task_id}/reject-plan",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def reject_plan(task_id: str, request: Request) -> JSONResponse:
    """Reject a generated plan, resetting plan_status to 'none'.

    Returns 404 if task not found.
    Returns 409 if plan_status is not 'ready' (nothing to reject).
    """
    task_manager: TaskManager = request.app.state.task_manager
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.plan_status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} plan_status is {task.plan_status!r}, "
                   f"expected 'ready' to reject",
        )

    # Reset plan state via state machine
    await task_manager.set_plan_state(task_id, "none")
    event_bus.emit(
        "plan_status_change", task_id, {"plan_status": "none"},
        origin="plan",
    )

    return JSONResponse(
        status_code=200,
        content={"task_id": task_id, "plan_status": "none"},
    )


# ------------------------------------------------------------------
# Plan deletion endpoint (T-P0-136)
# ------------------------------------------------------------------


@router.delete(
    "/api/tasks/{task_id}/plan",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def delete_plan(task_id: str, request: Request) -> JSONResponse:
    """Delete a task's plan, resetting plan_status to 'none'.

    Works from any non-none state: ready, failed, decomposed, generating.
    When deleting from generating state, clears generation_id so any
    in-flight result will be discarded (cancel semantics).

    Returns 404 if task not found.
    Returns 409 if plan_status is already 'none'.
    """
    task_manager: TaskManager = request.app.state.task_manager
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.plan_status == "none":
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} plan_status is already 'none'",
        )

    previous_status = task.plan_status
    await task_manager.set_plan_state(task_id, "none")

    logger.info(
        "Plan deleted for %s (was %s)", task_id, previous_status,
    )

    event_bus.emit(
        "plan_status_change", task_id, {"plan_status": "none"},
        origin="plan",
    )

    return JSONResponse(
        status_code=200,
        content={
            "task_id": task_id,
            "plan_status": "none",
            "previous_status": previous_status,
        },
    )


# ------------------------------------------------------------------
# Task generator endpoints (proposal-to-TASKS.md pipeline)
# ------------------------------------------------------------------


@router.post(
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

    # Use bridge for direct SQL-to-SQL task creation
    try:
        bridge = TaskStoreBridge(project.repo_path)
    except (FileNotFoundError, ImportError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to load task store bridge: {exc}",
        ) from None

    # Re-process with fresh IDs from tasks.db
    result = process_proposals(
        proposals=proposals,
        bridge=bridge,
        parent_task_id=task.local_task_id,
    )

    if not result.success:
        raise HTTPException(status_code=422, detail=result.error or "Unknown error")

    # Write tasks to tasks.db via bridge
    written_ids: list[str] = []
    try:
        for at in result.allocated_tasks:
            bridge.add_task(
                title=at.title,
                priority=at.priority,
                complexity=at.complexity,
                description=at.description,
                depends_on=at.depends_on,
                task_id=at.task_id,
            )
            written_ids.append(at.task_id)
        bridge.reproject()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write tasks: {exc}",
        ) from None

    # Update parent task plan_status to reflect decomposition complete
    await task_manager.set_plan_state(task_id, "decomposed")

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
        {"reason": "task_generation", "count": len(written_ids)},
        origin="task_generator",
    )

    return ConfirmGeneratedTasksResponse(
        parent_task_id=task.local_task_id,
        written_ids=written_ids,
        auto_paused=auto_paused,
        detail=f"Generated {len(written_ids)} tasks from {task.local_task_id}",
    )


# ------------------------------------------------------------------
# Task creation endpoint (writes to tasks.db)
# ------------------------------------------------------------------


@router.post(
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
    """Create a new task by writing to the project's tasks.db.

    Uses the TaskStoreBridge for direct SQL writes, then reprojects
    TASKS.md and auto-triggers sync to bring the new task into state.db.
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

    # Verify repo_path
    if project.repo_path is None:
        raise HTTPException(
            status_code=400,
            detail=f"Project {project_id} has no repo_path configured",
        )

    try:
        bridge = TaskStoreBridge(project.repo_path)
    except (FileNotFoundError, ImportError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to load task store bridge: {exc}",
        ) from None

    # Create the new task via bridge
    try:
        task_id = bridge.add_task(
            title=body.title,
            priority=body.priority,
            description=body.description,
        )
        bridge.reproject()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to write task: {exc}",
        ) from None

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
        task_id=task_id,
        success=True,
        backup_path=None,
        synced=synced,
        sync_result=sync_result_resp,
        sync_error=sync_error_msg,
    )


# ------------------------------------------------------------------
# Task CRUD endpoints
# ------------------------------------------------------------------


@router.get("/api/tasks")
async def list_tasks(
    request: Request,
    project_id: str | None = None,
    status: TaskStatus | None = None,
) -> list[TaskResponse]:
    """List all tasks with optional filtering by project_id and/or status."""
    task_manager: TaskManager = request.app.state.task_manager
    tasks = await task_manager.list_tasks(project_id=project_id, status=status)
    return [_task_to_response(t) for t in tasks]


@router.get(
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


@router.patch(
    "/api/tasks/{task_id}",
    responses={404: {"model": ErrorResponse}},
)
async def update_task_fields(
    task_id: str,
    body: UpdateTaskRequest,
    request: Request,
) -> TaskResponse:
    """Update a task's title and/or description.

    When the task has a ``plan_json``, description edits are routed through
    ``plan_json["plan"]`` (the canonical source of truth) and then
    ``description`` is re-derived via ``format_plan_as_text()``.  This
    keeps ``plan_json`` and ``description`` in sync.
    """
    task_manager: TaskManager = request.app.state.task_manager
    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    changed = False
    if body.title is not None and body.title != task.title:
        task.title = body.title
        changed = True
    if body.description is not None and body.description != task.description:
        # When plan_json exists, route edits through plan_json["plan"]
        # so that plan_json stays in sync with description.
        if task.plan_json:
            try:
                plan_data = json.loads(task.plan_json)
            except (json.JSONDecodeError, TypeError):
                plan_data = None

            if isinstance(plan_data, dict):
                plan_data["plan"] = body.description
                new_plan_json = json.dumps(plan_data)
                new_description = format_plan_as_text(plan_data)
                task.plan_json = new_plan_json
                task.description = new_description
                changed = True
            else:
                # plan_json is corrupt/unparseable -- fall back to direct update
                task.description = body.description
                changed = True
        else:
            task.description = body.description
            changed = True

    if changed:
        task = await task_manager.update_task(task)
        # Write-back title to tasks.db so sync doesn't overwrite (non-fatal)
        if body.title is not None:
            try:
                registry: ProjectRegistry = request.app.state.registry
                project = registry.get_project(task.project_id)
                if project.repo_path is not None:
                    bridge = TaskStoreBridge(project.repo_path)
                    if not bridge.update_task_title(task.local_task_id, body.title):
                        logger.warning("Failed to write-back title for %s", task_id)
                    else:
                        bridge.reproject()
            except Exception as exc:
                logger.warning("Title write-back failed for %s: %s", task_id, exc)

    return _task_to_response(task)


@router.delete(
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
