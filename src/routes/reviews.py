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
import uuid
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
    AnswerQuestionRequest,
    ErrorResponse,
    ReviewDecisionRequest,
    StatusTransitionRequest,
    SubmitForReviewRequest,
    TaskResponse,
)
from src.task_manager import (
    DecompositionRequiredError,
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


def _resolve_repo_path(task: Task, request: Request) -> Path | None:
    """Resolve the project repository path for a task.

    Args:
        task: The task whose project_id is used for lookup.
        request: The FastAPI request for app state access.

    Returns:
        The project's ``repo_path``, or ``None`` if unavailable.
    """
    try:
        registry: ProjectRegistry = request.app.state.registry
        project = registry.get_project(task.project_id)
        if project.repo_path:
            return project.repo_path
    except (KeyError, AttributeError):
        pass
    return None


def _enqueue_review_pipeline(
    task_manager: TaskManager,
    review_pipeline: ReviewPipeline | None,
    event_bus: EventBus,
    task: Task,
    task_id: str,
    review_attempt: int = 1,
    human_feedback: list[dict] | None = None,
    history_writer: HistoryWriter | None = None,
    repo_path: Path | None = None,
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
        repo_path: Optional project repository path for agent ``cwd`` setting.
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
            # Pre-flight: verify task is still in a review state before expensive LLM work.
            # Lifecycle state is set AFTER pre-flight passes to avoid writing RUNNING
            # to a task that will immediately abort (Gap 2 fix).
            _review_preflight_statuses = {
                TaskStatus.REVIEW, TaskStatus.REVIEW_NEEDS_HUMAN,
            }
            preflight = await task_manager.get_task(task_id)
            if preflight is None or preflight.status not in _review_preflight_statuses:
                logger.warning(
                    "Review pre-flight failed for %s: status=%s, expected review. Aborting.",
                    task_id, preflight.status.value if preflight else "deleted",
                )
                return

            # Set lifecycle state to RUNNING only after pre-flight passes
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
                repo_path=repo_path,
            )

            # Determine target status based on review outcome
            if review_state.human_decision_needed:
                new_status = TaskStatus.REVIEW_NEEDS_HUMAN
            else:
                new_status = TaskStatus.QUEUED  # auto-approved: skip intermediate state

            # Atomic completion: write review_json, review_status,
            # lifecycle_state, AND transition task status in one DB session.
            # All writes are guarded by expected_status=REVIEW -- if the task
            # moved away during the async pipeline, nothing is written.
            result = await task_manager.finalize_review(
                task_id,
                review_json=review_state.model_dump_json(),
                review_status="done",
                lifecycle_state=ReviewLifecycleState(review_state.lifecycle_state),
                new_task_status=new_status,
                expected_status=TaskStatus.REVIEW,
            )
            if result is None:
                logger.warning(
                    "Review completed for %s but task no longer in REVIEW, "
                    "all writes skipped",
                    task_id,
                )
                return

            # Log auto-approve event
            if not review_state.human_decision_needed:
                logger.info(
                    "Task %s auto-approved (consensus=%.2f), transitioned directly to QUEUED",
                    task_id, review_state.consensus_score,
                )
                event_bus.emit("log", task_id,
                               {"message": "Review auto-approved, task queued for execution",
                                "source": "review"},
                               origin="review")

            event_bus.emit(
                "status_change", task_id, {"status": result.status.value},
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
            force_decompose_bypass=body.force_decompose_bypass,
        )
    except DecompositionRequiredError as exc:
        return JSONResponse(
            status_code=428,
            content={
                "detail": str(exc),
                "gate_action": "decomposition_required",
                "task_id": task_id,
            },
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
            repo_path=_resolve_repo_path(updated, request),
        )

    return _task_to_response(updated)


# ------------------------------------------------------------------
# Atomic submit-for-review endpoint
# ------------------------------------------------------------------


@router.post(
    "/api/tasks/{task_id}/submit-for-review",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def submit_for_review(
    task_id: str,
    body: SubmitForReviewRequest,
    request: Request,
) -> TaskResponse:
    """Atomically update title/description and transition to REVIEW.

    Combines the separate PATCH /tasks/{id} + PATCH /tasks/{id}/status
    calls into a single transactional operation, eliminating the race
    condition where concurrent updates between the two calls could cause
    data loss.
    """
    task_manager: TaskManager = request.app.state.task_manager
    scheduler: Scheduler = request.app.state.scheduler

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    # --- Step 1: Apply field updates (same logic as update_task_fields) ---
    if body.title is not None and body.title != task.title:
        task.title = body.title
    if body.description is not None and body.description != task.description:
        if task.plan_json:
            try:
                plan_data = json.loads(task.plan_json)
            except (json.JSONDecodeError, TypeError):
                plan_data = None
            if isinstance(plan_data, dict):
                plan_data["plan"] = body.description
                task.plan_json = json.dumps(plan_data)
                task.description = format_plan_as_text(plan_data)
            else:
                task.description = body.description
        else:
            task.description = body.description

    # Persist field changes before status transition so the status
    # transition sees the updated description (needed for plan validity).
    task = await task_manager.update_task(task)

    # Write-back title to TASKS.md (non-fatal, same as update_task_fields)
    if body.title is not None:
        try:
            registry: ProjectRegistry = request.app.state.registry
            project = registry.get_project(task.project_id)
            if project.repo_path is not None:
                tasks_md = project.repo_path / project.tasks_file
                writer = TasksWriter(tasks_md)
                if not writer.update_task_title(task.local_task_id, body.title):
                    logger.warning("Failed to write-back title for %s", task_id)
        except Exception as exc:
            logger.warning("Title write-back failed for %s: %s", task_id, exc)

    # --- Step 2: Transition to REVIEW ---
    old_status = task.status
    gate_enabled = scheduler.is_review_gate_enabled(task.project_id)

    try:
        updated = await task_manager.update_status(
            task_id, TaskStatus.REVIEW,
            review_gate_enabled=gate_enabled,
        )
    except DecompositionRequiredError as exc:
        return JSONResponse(
            status_code=428,
            content={
                "detail": str(exc),
                "gate_action": "decomposition_required",
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
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    event_bus: EventBus = request.app.state.event_bus
    event_bus.emit("status_change", task_id, {"status": "review"}, origin="api")
    event_bus.emit("board_sync", task_id, {"trigger": "status_change"}, origin="api")

    # Enqueue review pipeline if entering REVIEW
    if old_status != TaskStatus.REVIEW:
        review_pipeline: ReviewPipeline | None = request.app.state.review_pipeline
        hw: HistoryWriter = request.app.state.history_writer
        _enqueue_review_pipeline(
            task_manager, review_pipeline, event_bus, updated, task_id,
            history_writer=hw,
            repo_path=_resolve_repo_path(updated, request),
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
        repo_path=_resolve_repo_path(task, request),
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

    # --- request_changes / reject: auto-replan with semantic differentiation ---
    if body.decision in ("request_changes", "reject"):
        review_state = task.review if task.review is not None else ReviewState()
        review_state = review_state.model_copy(update={"human_choice": body.decision})
        updated_task = task.model_copy(update={"review": review_state})
        await task_manager.update_task(updated_task)

        history_writer: HistoryWriter = request.app.state.history_writer
        await history_writer.write_review_decision(task_id, body.decision, reason=body.reason)

        return await _handle_replan(task_id, updated_task, body, request)

    # --- approve branch ---
    review_state = task.review if task.review is not None else ReviewState()
    review_state = review_state.model_copy(update={"human_choice": body.decision})
    updated_task = task.model_copy(update={"review": review_state})
    await task_manager.update_task(updated_task)

    history_writer_approve: HistoryWriter = request.app.state.history_writer
    await history_writer_approve.write_review_decision(task_id, body.decision, reason=body.reason)

    new_status = TaskStatus.QUEUED

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

    event_bus.emit("status_change", task_id, {"status": new_status.value}, origin="api")
    event_bus.emit("board_sync", task_id, {"trigger": "status_change"}, origin="api")

    # Force immediate scheduler tick so approved task starts without 5s delay
    asyncio.create_task(scheduler.force_tick())

    return _task_to_response(updated)


# Maximum number of replan attempts before the endpoint refuses further replans.
MAX_REPLAN_ATTEMPTS = 4


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
        # reject at max attempts falls back to BACKLOG (hard stop)
        if body.decision == "reject":
            await task_manager.update_status(task_id, TaskStatus.BACKLOG)
            event_bus.emit("status_change", task_id, {"status": "backlog"}, origin="api")
            refreshed = await task_manager.get_task(task_id)
            if refreshed is None:
                raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
            return _task_to_response(refreshed)
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
    review_feedback = _build_replan_feedback(task, body.reason, decision_type=body.decision)

    # reject = fresh start (don't increment); request_changes/replan = targeted fix (increment)
    new_attempt = task.replan_attempt if body.decision == "reject" else task.replan_attempt + 1
    generation_id = uuid.uuid4().hex
    await task_manager.set_plan_state(
        task_id, "generating",
        plan_generation_id=generation_id,
        replan_attempt=new_attempt,
    )
    event_bus.emit(
        "plan_status_change", task_id,
        {
            "plan_status": "generating",
            "generation_id": generation_id,
            "replan_attempt": new_attempt,
        },
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
                complexity_hint=task.complexity,
            )

            formatted = format_plan_as_text(plan_data)

            # Check generation_id match before writing
            current_task = await task_manager.get_task(task_id)
            if (
                current_task is None
                or current_task.plan_generation_id != generation_id
            ):
                logger.info(
                    "Replan generation_id mismatch for %s, discarding stale result",
                    task_id,
                )
                return

            await task_manager.set_plan_state(
                task_id=task_id,
                new_status="ready",
                plan_generation_id=generation_id,
                description=formatted,
                plan_json=json.dumps(plan_data),
            )
            event_bus.emit(
                "plan_status_change", task_id,
                {
                    "plan_status": "ready",
                    "generation_id": generation_id,
                    "description": formatted,
                },
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

            # Auto-enqueue review pipeline for the new plan (T-P1-165)
            # Transition back to REVIEW if needed (e.g., from REVIEW_NEEDS_HUMAN)
            refreshed = await task_manager.get_task(task_id)
            if refreshed is not None and refreshed.status == TaskStatus.REVIEW_NEEDS_HUMAN:
                refreshed = await task_manager.update_status(
                    task_id, TaskStatus.REVIEW,
                    expected_status=TaskStatus.REVIEW_NEEDS_HUMAN,
                    review_gate_enabled=False,
                )
                event_bus.emit(
                    "status_change", task_id, {"status": "review"},
                    origin="review",
                )

            # Idempotent: skip if review is already running or task left review
            if refreshed is not None and refreshed.status == TaskStatus.REVIEW:
                rlc = refreshed.review_lifecycle_state
                if rlc != ReviewLifecycleState.RUNNING.value:
                    review_pipeline: ReviewPipeline | None = getattr(
                        request.app.state, "review_pipeline", None,
                    )
                    max_attempt = await history_writer.get_max_review_attempt(
                        task_id,
                    )
                    _enqueue_review_pipeline(
                        task_manager=task_manager,
                        review_pipeline=review_pipeline,
                        event_bus=event_bus,
                        task=refreshed,
                        task_id=task_id,
                        review_attempt=max_attempt + 1,
                        history_writer=history_writer,
                        repo_path=_resolve_repo_path(refreshed, request),
                    )
                    logger.info(
                        "Auto-triggered review for %s after replan ready",
                        task_id,
                    )
                else:
                    logger.info(
                        "Skipped auto-review for %s: already running",
                        task_id,
                    )
            else:
                logger.info(
                    "Replan auto-review skipped for %s: task status is %s, not review",
                    task_id,
                    refreshed.status.value if refreshed else "deleted",
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

    asyncio.create_task(_run_replan())

    # Re-fetch task with updated replan_attempt + generating status
    updated = await task_manager.get_task(task_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _task_to_response(updated)


# ------------------------------------------------------------------
# Answer clarifying question endpoint
# ------------------------------------------------------------------


@router.post(
    "/api/tasks/{task_id}/review/answer",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def answer_review_question(
    task_id: str,
    body: AnswerQuestionRequest,
    request: Request,
) -> TaskResponse:
    """Answer a clarifying question raised during review.

    Updates the question's ``answer`` and ``answered_at`` fields in the
    task's ``review_json.questions`` list. Persists across page reloads.
    """
    task_manager: TaskManager = request.app.state.task_manager
    event_bus: EventBus = request.app.state.event_bus

    task = await task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    if task.review is None or not task.review.questions:
        raise HTTPException(
            status_code=409,
            detail="No review questions found for this task.",
        )

    # Find the question by ID
    question_found = False
    from datetime import UTC, datetime

    for q in task.review.questions:
        if q.id == body.question_id:
            q.answer = body.answer
            q.answered_at = datetime.now(UTC)
            question_found = True
            break

    if not question_found:
        raise HTTPException(
            status_code=404,
            detail=f"Question not found: {body.question_id}",
        )

    # Persist the updated review state
    await task_manager.update_task(task)

    event_bus.emit(
        "review_question_answered", task_id,
        {"question_id": body.question_id},
        origin="api",
    )

    return _task_to_response(task)


def _build_replan_feedback(
    task: Task,
    user_reason: str,
    decision_type: str = "replan",
) -> str:
    """Build structured review feedback for replan from the task's review state.

    Combines the latest review suggestions (from task.review.reviews[]),
    answered clarifying questions, and any user-supplied reason text.

    Args:
        task: The task being replanned.
        user_reason: Human reviewer's reason text.
        decision_type: One of "replan", "reject", "request_changes".
            Controls the framing preamble for the LLM.
    """
    parts: list[str] = []

    # Semantic framing based on decision type
    if decision_type == "reject":
        parts.append(
            "PLAN REJECTED by reviewer. The overall approach/direction needs "
            "fundamental rework. Do NOT make incremental tweaks -- reconsider "
            "the core strategy."
        )
    elif decision_type == "request_changes":
        parts.append(
            "CHANGES REQUESTED by reviewer. The overall direction is correct, "
            "but specific details need targeted fixes. Preserve the existing approach."
        )

    if task.review is not None and task.review.reviews:
        for review in task.review.reviews:
            if review.blocking_issues:
                parts.append(f"Reviewer ({review.focus}) BLOCKING ISSUES:")
                for b in review.blocking_issues:
                    parts.append(f"- [BLOCKING] {b}")
            if review.suggestions:
                parts.append(f"Reviewer ({review.focus}) suggestions:")
                for s in review.suggestions:
                    parts.append(f"- {s}")
            if review.summary:
                parts.append(f"Reviewer ({review.focus}) summary: {review.summary}")

    # Include answered clarifying questions
    if task.review is not None and task.review.questions:
        answered = [q for q in task.review.questions if q.answer.strip()]
        if answered:
            parts.append("\n--- Answered clarifying questions ---")
            for q in answered:
                parts.append(f"Q ({q.source_reviewer}): {q.text}")
                parts.append(f"A: {q.answer}")
            parts.append("--- End of answers ---")

    if user_reason.strip():
        parts.append(f"\nHuman reviewer comment: {user_reason}")
    return "\n".join(parts) if parts else "The previous plan was rejected."
