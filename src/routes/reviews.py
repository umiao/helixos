"""Review pipeline route endpoints.

Endpoints: PATCH /api/projects/{project_id}/review-gate,
PATCH /api/tasks/{task_id}/status,
POST /api/tasks/{task_id}/review,
POST /api/tasks/{task_id}/review/decide.

Also contains helper functions: _enqueue_review_pipeline, _set_review_failed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api_helpers import _task_to_response
from src.config import OrchestratorConfig, ProjectRegistry
from src.enrichment import (
    PlanGenerationError,
    PlanGenerationErrorType,
    format_plan_as_text,
    generate_task_plan,
    is_claude_cli_available,
)
from src.events import EventBus
from src.history_writer import HistoryWriter
from src.models import ReviewLifecycleState, ReviewState, Task, TaskStatus
from src.review_pipeline import ReviewPipeline
from src.scheduler import Scheduler
from src.schemas import (
    ErrorResponse,
    ReviewDecisionRequest,
    StatusTransitionRequest,
    TaskResponse,
)
from src.task_manager import (
    OptimisticLockError,
    PlanInvalidError,
    ReviewGateBlockedError,
    TaskManager,
)
from src.tasks_writer import TasksWriter

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Review helper functions
# ------------------------------------------------------------------


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
                complexity=task.complexity or "S",
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


# ------------------------------------------------------------------
# Review gate endpoint
# ------------------------------------------------------------------


@router.patch(
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
# Status transition endpoint
# ------------------------------------------------------------------


@router.patch(
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
        cancel_result = await scheduler.cancel_task(task_id)
        if cancel_result:
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


# ------------------------------------------------------------------
# Review retry endpoint
# ------------------------------------------------------------------


@router.post(
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


# ------------------------------------------------------------------
# Review decision endpoint
# ------------------------------------------------------------------


@router.post(
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
    - ``replan`` -> regenerate plan with review feedback (max 2 replan attempts)
    """
    task_manager: TaskManager = request.app.state.task_manager
    scheduler: Scheduler = request.app.state.scheduler
    event_bus: EventBus = request.app.state.event_bus

    valid_decisions = ("approve", "reject", "request_changes", "replan")
    if body.decision not in valid_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision: '{body.decision}'. "
                   f"Must be one of: {', '.join(valid_decisions)}.",
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

    # --- Replan branch: regenerate plan incorporating review feedback ---
    if body.decision == "replan":
        return await _handle_replan(task_id, task, body, request)

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


# Maximum number of replan attempts before the endpoint refuses further replans.
MAX_REPLAN_ATTEMPTS = 2


async def _handle_replan(
    task_id: str,
    task: Task,
    body: ReviewDecisionRequest,
    request: Request,
) -> TaskResponse:
    """Handle the ``replan`` decision: regenerate the plan with review feedback.

    Enforces a max of ``MAX_REPLAN_ATTEMPTS`` replan attempts.  On success the
    new plan is saved and the review pipeline is auto-enqueued.
    """
    task_manager: TaskManager = request.app.state.task_manager
    event_bus: EventBus = request.app.state.event_bus
    history_writer: HistoryWriter = request.app.state.history_writer

    # Enforce replan attempt limit
    if task.replan_attempt >= MAX_REPLAN_ATTEMPTS:
        raise HTTPException(
            status_code=409,
            detail=f"Maximum replan attempts ({MAX_REPLAN_ATTEMPTS}) reached. "
                   "Use 'reject' to move to BACKLOG or 'approve' to proceed.",
        )

    if not is_claude_cli_available():
        raise HTTPException(
            status_code=503,
            detail="Plan generation unavailable (Claude SDK not importable).",
        )

    # Persist decision to review history
    await history_writer.write_review_decision(
        task_id, "replan", reason=body.reason,
    )

    # Build review feedback from latest review suggestions
    review_feedback = _build_replan_feedback(task, body.reason)

    # Increment replan_attempt and set plan_status to generating
    new_attempt = task.replan_attempt + 1
    task_update = task.model_copy(update={
        "replan_attempt": new_attempt,
        "plan_status": "generating",
    })
    await task_manager.update_task(task_update)
    event_bus.emit(
        "plan_status_change", task_id,
        {"plan_status": "generating", "replan_attempt": new_attempt},
        origin="plan",
    )

    # Get repo_path for codebase context
    repo_path: Path | None = None
    registry: ProjectRegistry = request.app.state.registry
    try:
        project = registry.get_project(task.project_id)
        if project.repo_path:
            repo_path = project.repo_path
    except (KeyError, AttributeError):
        pass

    config: OrchestratorConfig = request.app.state.config
    enrichment_timeout = config.review_pipeline.enrichment_timeout_minutes

    # Launch replan as background task
    async def _run_replan() -> None:
        try:
            def on_log(line: str) -> None:
                event_bus.emit(
                    "log", task_id,
                    {"message": line, "source": "plan"},
                    origin="plan",
                )
                asyncio.ensure_future(
                    history_writer.write_log(
                        task_id, line, level="info", source="plan",
                    ),
                )

            def on_stream_event(event_dict: dict) -> None:
                event_bus.emit(
                    "execution_stream", task_id, event_dict,
                    origin="plan",
                )

            plan_log_dir: Path | None = None
            with contextlib.suppress(AttributeError, TypeError):
                plan_log_dir = config.review_pipeline.stream_log_dir

            plan_data = await generate_task_plan(
                title=task.title,
                description=task.description,
                repo_path=repo_path,
                timeout_minutes=enrichment_timeout,
                on_log=on_log,
                on_stream_event=on_stream_event,
                stream_log_dir=plan_log_dir,
                task_id=task_id,
                plan_validation=config.orchestrator.plan_validation,
                review_feedback=review_feedback,
            )

            formatted = format_plan_as_text(plan_data)

            # Atomic update: description + plan_status + plan_json
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

            # Persist plan_status=ready to TASKS.md (non-fatal)
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
                                "updated for %s (replan)", task_id,
                            )
            except Exception as _exc:
                logger.warning(
                    "DB plan_status=ready but TASKS.md not updated "
                    "for %s (replan): %s", task_id, _exc,
                )

            # Auto-enqueue review pipeline for the new plan
            refreshed = await task_manager.get_task(task_id)
            if refreshed is not None:
                review_pipeline: ReviewPipeline | None = getattr(
                    request.app.state, "review_pipeline", None,
                )
                _enqueue_review_pipeline(
                    task_manager=task_manager,
                    review_pipeline=review_pipeline,
                    event_bus=event_bus,
                    task=refreshed,
                    task_id=task_id,
                    review_attempt=1,
                    history_writer=history_writer,
                )

        except Exception as exc:
            logger.warning("Replan failed for %s: %s", task_id, exc)
            error_type = (
                exc.error_type.value
                if isinstance(exc, PlanGenerationError) else
                PlanGenerationErrorType.CLI_ERROR.value
            )
            user_msg = (
                exc.user_message
                if isinstance(exc, PlanGenerationError) else str(exc)
            )
            retryable = (
                exc.retryable
                if isinstance(exc, PlanGenerationError) else True
            )
            event_bus.emit("log", task_id, {
                "message": f"Replan failed: {user_msg}",
                "source": "plan",
            }, origin="plan")
            asyncio.ensure_future(
                history_writer.write_log(
                    task_id,
                    f"Replan failed [{error_type}]: {exc}",
                    level="error",
                    source="plan",
                ),
            )
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

    asyncio.create_task(_run_replan())

    # Re-fetch task with updated replan_attempt + generating status
    updated = await task_manager.get_task(task_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _task_to_response(updated)


def _build_replan_feedback(task: Task, user_reason: str) -> str:
    """Build structured review feedback for replan from the task's review state.

    Combines the latest review suggestions (from task.review.reviews[]) with
    any user-supplied reason text.
    """
    parts: list[str] = []
    if task.review is not None and task.review.reviews:
        for review in task.review.reviews:
            if review.suggestions:
                parts.append(f"Reviewer ({review.focus}) suggestions:")
                for s in review.suggestions:
                    parts.append(f"- {s}")
            if review.summary:
                parts.append(f"Reviewer ({review.focus}) summary: {review.summary}")
    if user_reason.strip():
        parts.append(f"\nHuman reviewer comment: {user_reason}")
    return "\n".join(parts) if parts else "The previous plan was rejected."
