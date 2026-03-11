"""TaskManager -- CRUD operations and state machine for tasks.

Implements the task lifecycle from PRD Section 5.3 with valid state
transitions enforced by ``update_status``.  Supports bidirectional
transitions (backward drags) and optimistic concurrency control.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import TaskRow, get_session, task_dict_to_row_kwargs, task_row_to_dict
from src.dependency_graph import extract_priority  # noqa: F401 (re-export)
from src.models import ExecutionState, PlanStatus, ReviewLifecycleState, Task, TaskStatus

logger = logging.getLogger(__name__)


class UpsertResult(StrEnum):
    """Outcome of an upsert_task call."""

    created = "created"
    resurrected = "resurrected"
    updated = "updated"
    unchanged = "unchanged"
    skipped_deleted = "skipped_deleted"


class ReviewGateBlockedError(Exception):
    """Raised when the review gate blocks a status transition.

    Carries enough context for the API layer to return HTTP 428
    (Precondition Required) with a ``gate_action`` hint.
    """

    def __init__(self, task_id: str, message: str) -> None:
        """Initialize with *task_id* and a human-readable *message*."""
        self.task_id = task_id
        super().__init__(message)


class PlanInvalidError(Exception):
    """Raised when a task's plan fails validity checks.

    Carries enough context for the API layer to return HTTP 428
    (Precondition Required) with a ``gate_action`` of ``plan_invalid``.
    """

    def __init__(self, task_id: str, message: str) -> None:
        """Initialize with *task_id* and a human-readable *message*."""
        self.task_id = task_id
        super().__init__(message)


class DecompositionRequiredError(Exception):
    """Raised when a task has undecomposed proposed tasks.

    Carries enough context for the API layer to return HTTP 428
    (Precondition Required) with a ``gate_action`` of
    ``decomposition_required``.
    """

    def __init__(self, task_id: str, message: str) -> None:
        """Initialize with *task_id* and a human-readable *message*."""
        self.task_id = task_id
        super().__init__(message)


class OptimisticLockError(Exception):
    """Raised when ``expected_updated_at`` does not match the DB row.

    Signals a concurrent edit conflict (HTTP 409 with ``conflict=true``).
    """

    def __init__(self, task_id: str) -> None:
        """Initialize with the conflicting *task_id*."""
        self.task_id = task_id
        super().__init__(
            f"Task {task_id} was just updated by another request. "
            "Please refresh and try again."
        )


# ---------------------------------------------------------------------------
# Valid state transitions (bidirectional)
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.BACKLOG: {TaskStatus.REVIEW, TaskStatus.QUEUED},
    TaskStatus.REVIEW: {
        TaskStatus.REVIEW_AUTO_APPROVED,
        TaskStatus.REVIEW_NEEDS_HUMAN,
        TaskStatus.BACKLOG,
    },
    TaskStatus.REVIEW_AUTO_APPROVED: {TaskStatus.QUEUED, TaskStatus.BACKLOG},
    TaskStatus.REVIEW_NEEDS_HUMAN: {TaskStatus.QUEUED, TaskStatus.BACKLOG, TaskStatus.REVIEW},
    TaskStatus.QUEUED: {
        TaskStatus.RUNNING,
        TaskStatus.BLOCKED,
        TaskStatus.BACKLOG,
        TaskStatus.REVIEW,
    },
    TaskStatus.RUNNING: {TaskStatus.DONE, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.QUEUED, TaskStatus.BLOCKED, TaskStatus.BACKLOG},
    TaskStatus.DONE: {TaskStatus.BACKLOG, TaskStatus.QUEUED},
    TaskStatus.BLOCKED: {TaskStatus.QUEUED, TaskStatus.BACKLOG},
}

# ---------------------------------------------------------------------------
# Plan state machine transitions
# ---------------------------------------------------------------------------

VALID_PLAN_TRANSITIONS: dict[str, set[str]] = {
    PlanStatus.NONE: {PlanStatus.GENERATING},
    PlanStatus.GENERATING: {PlanStatus.READY, PlanStatus.FAILED, PlanStatus.NONE},
    PlanStatus.READY: {PlanStatus.GENERATING, PlanStatus.DECOMPOSED, PlanStatus.NONE},
    PlanStatus.FAILED: {PlanStatus.GENERATING, PlanStatus.NONE},
    PlanStatus.DECOMPOSED: {PlanStatus.GENERATING, PlanStatus.NONE},
}

# ---------------------------------------------------------------------------
# Plan validity
# ---------------------------------------------------------------------------

MIN_PLAN_LENGTH = 20
"""Minimum character count for a valid plan (after stripping whitespace)."""


def is_plan_valid(description: str | None) -> bool:
    """Return True if *description* passes minimum plan validity checks.

    A valid plan must be non-empty, non-whitespace, and at least
    ``MIN_PLAN_LENGTH`` characters long (after stripping).
    """
    if not description:
        return False
    stripped = description.strip()
    return len(stripped) >= MIN_PLAN_LENGTH


def _build_transition_error(
    current: TaskStatus,
    target: TaskStatus,
    task_id: str,
) -> str:
    """Return a user-friendly error message for an invalid transition."""
    valid = VALID_TRANSITIONS.get(current, set())

    # Trying to move from RUNNING to anything except DONE/FAILED
    if current == TaskStatus.RUNNING:
        return (
            f"Task {task_id} is currently running. "
            "Wait for it to finish or cancel it first."
        )

    # Has valid targets but this one isn't among them
    if valid:
        names = sorted(s.value for s in valid)
        return (
            f"Cannot move task {task_id} from {current.value} to "
            f"{target.value}. Valid targets: {', '.join(names)}."
        )

    return (
        f"Invalid transition: {current.value} -> {target.value} "
        f"for task {task_id}"
    )


class TaskManager:
    """CRUD and state machine for tasks stored in SQLite."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize with an async session factory from db.py."""
        self._sf = session_factory

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_task(self, task: Task) -> Task:
        """Insert a new task into the database.

        Returns the task as persisted. Raises ``ValueError`` if a task
        with the same id already exists.
        """
        async with get_session(self._sf) as session:
            existing = await session.get(TaskRow, task.id)
            if existing is not None:
                raise ValueError(f"Task already exists: {task.id}")

            data = task.model_dump(mode="json")
            row = TaskRow(**task_dict_to_row_kwargs(data))
            session.add(row)

        return task

    async def upsert_task(
        self,
        task: Task,
        *,
        plan_status: str | None = None,
    ) -> UpsertResult:
        """Insert, resurrect, or update a task -- no exceptions on conflict.

        Handles all cases that ``sync_project_tasks`` needs:
        - Row missing: INSERT (created)
        - Row soft-deleted: un-delete + full field update (resurrected)
        - Row exists + fields changed: UPDATE changed fields (updated)
        - Row exists + nothing changed: no-op (unchanged)

        Args:
            task: The task data to upsert.
            plan_status: When not *None*, overwrite the DB ``plan_status``
                column with this value.  *None* (line absent in TASKS.md)
                means "DB wins" -- the existing value is preserved.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task.id)
            now = datetime.now(UTC).isoformat()

            if row is None:
                data = task.model_dump(mode="json")
                if plan_status is not None:
                    data["plan_status"] = plan_status
                new_row = TaskRow(**task_dict_to_row_kwargs(data))
                session.add(new_row)
                return UpsertResult.created

            if row.is_deleted:
                if row.deleted_source == "user":
                    logger.warning(
                        "Skipping user-deleted task %s during sync", task.id,
                    )
                    return UpsertResult.skipped_deleted
                # sync-deleted or legacy (NULL) -- allow resurrection
                data = task.model_dump(mode="json")
                if plan_status is not None:
                    data["plan_status"] = plan_status
                kwargs = task_dict_to_row_kwargs(data)
                for key, value in kwargs.items():
                    setattr(row, key, value)
                row.is_deleted = False
                row.deleted_source = None
                row.updated_at = now
                return UpsertResult.resurrected

            changed = False
            if row.title != task.title:
                row.title = task.title
                changed = True
            if (row.description or "") != task.description:
                row.description = task.description
                changed = True
            if row.status != task.status.value and task.status == TaskStatus.DONE:
                # Force DONE transition when TASKS.md says completed
                row.status = task.status.value
                changed = True
            # plan_status: only overwrite when TASKS.md explicitly sets it
            if plan_status is not None and row.plan_status != plan_status:
                row.plan_status = plan_status
                changed = True

            if changed:
                row.updated_at = now
                return UpsertResult.updated

            return UpsertResult.unchanged

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_task(self, task_id: str) -> Task | None:
        """Fetch a single task by id, or None if not found.

        Soft-deleted tasks are excluded by default.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                return None
            return Task.model_validate(task_row_to_dict(row))

    async def list_tasks(
        self,
        project_id: str | None = None,
        status: TaskStatus | None = None,
    ) -> list[Task]:
        """List tasks with optional filtering by project and/or status.

        Soft-deleted tasks are always excluded.
        """
        async with get_session(self._sf) as session:
            stmt = select(TaskRow).where(TaskRow.is_deleted == False)  # noqa: E712
            if project_id is not None:
                stmt = stmt.where(TaskRow.project_id == project_id)
            if status is not None:
                stmt = stmt.where(TaskRow.status == status.value)
            stmt = stmt.order_by(TaskRow.created_at)

            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [Task.model_validate(task_row_to_dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        *,
        expected_status: TaskStatus | None = None,
        review_gate_enabled: bool = False,
        reason: str = "",
        expected_updated_at: str | None = None,
        force_decompose_bypass: bool = False,
    ) -> Task:
        """Transition a task to *new_status*, enforcing the state machine.

        When *review_gate_enabled* is True, BACKLOG -> QUEUED is blocked;
        the task must go through REVIEW first (Layer 1 review gate).
        Additionally, BACKLOG -> REVIEW is blocked when the task's plan
        (description) fails validity checks (Layer 2 plan validity gate).

        *reason* is an optional human-supplied note for backward transitions
        (logged but not persisted on the task itself).

        *expected_updated_at* enables optimistic concurrency control.  If
        provided, the row's ``updated_at`` must match exactly; otherwise
        ``OptimisticLockError`` is raised (HTTP 409 with ``conflict=true``).

        *force_decompose_bypass* when True skips the decomposition gate
        (Layer 3) allowing execution with undecomposed proposed tasks.

        Raises ``ValueError`` on illegal transitions or missing tasks.
        Raises ``ReviewGateBlockedError`` when the review gate blocks
        the transition (callers should return HTTP 428).
        Raises ``PlanInvalidError`` when the plan fails validity checks
        (callers should return HTTP 428 with ``gate_action=plan_invalid``).
        Raises ``DecompositionRequiredError`` when the task has undecomposed
        proposed tasks (callers should return HTTP 428).
        Raises ``OptimisticLockError`` on concurrent-edit conflict.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")

            current = TaskStatus(row.status)

            # Atomic conditional: if expected_status is set and doesn't match,
            # return current state as a no-op (not an error).
            if expected_status is not None and current != expected_status:
                return Task.model_validate(task_row_to_dict(row))

            if new_status not in VALID_TRANSITIONS.get(current, set()):
                raise ValueError(
                    _build_transition_error(current, new_status, task_id)
                )

            # Optimistic concurrency check -- normalize timezone suffix
            # (Pydantic uses "Z", Python isoformat uses "+00:00")
            if expected_updated_at is not None:
                db_val = row.updated_at.replace("+00:00", "Z")
                exp_val = expected_updated_at.replace("+00:00", "Z")
                if db_val != exp_val:
                    raise OptimisticLockError(task_id)

            # Layer 1: review gate blocks BACKLOG -> QUEUED
            if (
                review_gate_enabled
                and current == TaskStatus.BACKLOG
                and new_status == TaskStatus.QUEUED
            ):
                raise ReviewGateBlockedError(
                    task_id,
                    f"Review gate is enabled: BACKLOG -> QUEUED is blocked "
                    f"for task {task_id}. Submit for review first.",
                )

            # Layer 2: plan validity gate blocks BACKLOG -> REVIEW
            if (
                review_gate_enabled
                and current == TaskStatus.BACKLOG
                and new_status == TaskStatus.REVIEW
                and not is_plan_valid(row.description)
            ):
                raise PlanInvalidError(
                    task_id,
                    f"Plan is missing or too short for task {task_id}. "
                    f"Write or generate a plan (at least {MIN_PLAN_LENGTH} "
                    f"characters) before sending to review.",
                )

            # Layer 3: decomposition gate blocks transition to RUNNING
            # when the task has undecomposed proposed tasks (plan_status=ready)
            if (
                new_status == TaskStatus.RUNNING
                and row.has_proposed_tasks
                and row.plan_status == PlanStatus.READY
            ):
                if force_decompose_bypass:
                    logger.warning(
                        "Decomposition gate bypassed for task %s by user action",
                        task_id,
                    )
                else:
                    raise DecompositionRequiredError(
                        task_id,
                        f"Task {task_id} has undecomposed proposed tasks. "
                        f"Review and confirm the plan before executing, "
                        f"or bypass with force_decompose_bypass.",
                    )

            now = datetime.now(UTC).isoformat()
            row.status = new_status.value
            row.updated_at = now

            if new_status in (
                TaskStatus.DONE,
                TaskStatus.FAILED,
                TaskStatus.BLOCKED,
            ):
                row.completed_at = now

            # Initialize execution state when entering RUNNING
            if new_status == TaskStatus.RUNNING and row.execution_json is None:
                exec_state = ExecutionState(
                    started_at=datetime.now(UTC),
                )
                row.execution_json = exec_state.model_dump_json()

            # review_status lifecycle
            old_review_status = getattr(row, "review_status", "idle")
            if (
                current != TaskStatus.REVIEW
                and new_status == TaskStatus.REVIEW
            ):
                # Entering REVIEW: set to "running" (pipeline will be enqueued by caller)
                row.review_status = "running"
            elif (
                current == TaskStatus.REVIEW
                and new_status == TaskStatus.REVIEW
                and old_review_status == "running"
            ):
                # Idempotent: already in REVIEW with running pipeline, no change
                pass

            # Timestamp cleanup on backward transitions
            self._cleanup_on_backward(row, current, new_status)

            if reason:
                logger.info(
                    "Transition %s: %s -> %s (reason: %s)",
                    task_id, current.value, new_status.value, reason,
                )

            return Task.model_validate(task_row_to_dict(row))

    @staticmethod
    def _cleanup_on_backward(
        row: TaskRow,
        current: TaskStatus,
        target: TaskStatus,
    ) -> None:
        """Reset timestamp/state fields per the backward-transition cleanup matrix."""
        # * -> BACKLOG: clear started_at, completed_at, execution_state, error_summary, review_status
        if target == TaskStatus.BACKLOG:
            row.completed_at = None
            row.review_status = "idle"
            row.review_lifecycle_state = "not_started"
            row.execution_epoch_id = None
            if row.execution_json:
                exec_data = json.loads(row.execution_json)
                exec_data.pop("started_at", None)
                exec_data.pop("error_summary", None)
                exec_data.pop("error_type", None)
                row.execution_json = json.dumps(exec_data) if exec_data else None
            # If no execution data remains meaningful, clear it
            if row.execution_json:
                exec_data = json.loads(row.execution_json)
                # Clear the whole execution state for clean slate
                row.execution_json = None

        # DONE/FAILED/BLOCKED -> QUEUED: clear completed_at, execution_state
        elif current in (
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
        ) and target == TaskStatus.QUEUED:
            row.completed_at = None
            row.execution_json = None

        # QUEUED -> REVIEW: no cleanup needed (just status change)
        # QUEUED -> BACKLOG is handled by the * -> BACKLOG rule above

    async def set_execution_epoch(self, task_id: str, epoch_id: str) -> None:
        """Set the execution epoch ID for a task entering RUNNING.

        Called by the scheduler immediately after transitioning to RUNNING.
        The epoch identifies this specific execution attempt so that
        finalization can verify it is still the rightful owner.

        Args:
            task_id: The task to tag.
            epoch_id: A unique identifier for this execution attempt.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")
            row.execution_epoch_id = epoch_id
            row.updated_at = datetime.now(UTC).isoformat()

    async def verify_execution_epoch(self, task_id: str, epoch_id: str) -> bool:
        """Check whether the task's current epoch matches *epoch_id*.

        Returns False if the task doesn't exist, is deleted, or the
        epoch doesn't match (meaning another execution or user action
        has claimed ownership).

        Args:
            task_id: The task to check.
            epoch_id: The epoch to verify against.

        Returns:
            True if the epoch matches, False otherwise.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                return False
            return row.execution_epoch_id == epoch_id

    async def finalize_review(
        self,
        task_id: str,
        review_json: str,
        review_status: str,
        lifecycle_state: ReviewLifecycleState,
        new_task_status: TaskStatus,
        expected_status: TaskStatus = TaskStatus.REVIEW,
    ) -> Task | None:
        """Atomic review pipeline completion.

        Writes review_json, review_status, review_lifecycle_state, AND transitions
        task status -- all in one session, guarded by expected_status.
        Returns the updated Task, or None if precondition failed (task moved away).

        Args:
            task_id: The task to finalize.
            review_json: Serialized ReviewState JSON.
            review_status: Review status string (e.g. "done").
            lifecycle_state: Terminal lifecycle state from the pipeline.
            new_task_status: Target task status (REVIEW_AUTO_APPROVED or REVIEW_NEEDS_HUMAN).
            expected_status: The status the task must be in for writes to proceed.

        Raises:
            ValueError: If the task is not found or is deleted.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")
            if TaskStatus(row.status) != expected_status:
                return None  # task moved away, all writes skipped

            # Validate transition
            if new_task_status not in VALID_TRANSITIONS.get(TaskStatus(row.status), set()):
                return None  # invalid transition, skip silently

            now = datetime.now(UTC).isoformat()
            row.review_json = review_json
            row.review_status = review_status
            row.review_lifecycle_state = lifecycle_state.value
            row.status = new_task_status.value
            row.updated_at = now
            self._cleanup_on_backward(row, TaskStatus(expected_status), new_task_status)
            return Task.model_validate(task_row_to_dict(row))

    async def set_review_result(
        self,
        task_id: str,
        review_json: str,
        *,
        expected_status: TaskStatus | None = None,
    ) -> bool:
        """Update ONLY the review_json column. Does NOT overwrite status or other fields.

        When *expected_status* is set, the write is skipped (returns False) if the
        task's current status doesn't match -- preventing stale review results from
        being persisted to a task that has moved on.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")
            if expected_status is not None and TaskStatus(row.status) != expected_status:
                return False
            row.review_json = review_json
            row.updated_at = datetime.now(UTC).isoformat()
            return True

    async def set_review_status(self, task_id: str, review_status: str) -> None:
        """Update only the review_status column for a task.

        Valid values: ``"idle"``, ``"running"``, ``"done"``, ``"failed"``.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")
            row.review_status = review_status
            row.updated_at = datetime.now(UTC).isoformat()

    async def set_review_lifecycle_state(
        self,
        task_id: str,
        state: ReviewLifecycleState,
    ) -> None:
        """Update the canonical review lifecycle state for a task.

        The backend is the single source of truth for lifecycle state.
        The frontend renders this value directly without guessing.

        Args:
            task_id: The task to update.
            state: The new lifecycle state (from ReviewLifecycleState enum).
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")
            row.review_lifecycle_state = state.value
            row.updated_at = datetime.now(UTC).isoformat()

    async def set_plan_state(
        self,
        task_id: str,
        new_status: str,
        *,
        plan_generation_id: str | None = None,
        description: str | None = None,
        plan_json: str | None = None,
        complexity: str | None = None,
        replan_attempt: int | None = None,
    ) -> None:
        """Single entry point for plan state transitions with invariant enforcement.

        Validates the transition against ``VALID_PLAN_TRANSITIONS`` and enforces
        field invariants per state:

        - **NONE**: clears plan_json, description="", has_proposed_tasks=False,
          plan_generation_id=NULL.
        - **GENERATING**: clears plan_json + description, preserves caller's
          generation_id.
        - **READY**: requires plan_json + description, computes has_proposed_tasks.
        - **FAILED**: clears plan_json, preserves description.
        - **DECOMPOSED**: preserves all fields.

        Args:
            task_id: The task to update.
            new_status: Target plan status.
            plan_generation_id: Generation ID for async race protection.
            description: Formatted plan text (required for READY).
            plan_json: JSON string of plan data (required for READY).
            complexity: Optional complexity override.
            replan_attempt: Optional replan attempt counter override.

        Raises:
            ValueError: On invalid transition, missing task, or missing
                required fields for the target state.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")

            current = row.plan_status
            valid_targets = VALID_PLAN_TRANSITIONS.get(current, set())
            if new_status not in valid_targets:
                raise ValueError(
                    f"Invalid plan transition: {current} -> {new_status} "
                    f"for task {task_id}. "
                    f"Valid targets: {sorted(valid_targets)}"
                )

            # Enforce field invariants per target state
            if new_status == PlanStatus.NONE:
                row.plan_json = None
                row.description = ""
                row.has_proposed_tasks = False
                row.plan_generation_id = None
            elif new_status == PlanStatus.GENERATING:
                row.plan_json = None
                row.description = ""
                row.has_proposed_tasks = False
                row.plan_generation_id = plan_generation_id
            elif new_status == PlanStatus.READY:
                if plan_json is None or description is None:
                    raise ValueError(
                        f"READY state requires plan_json and description "
                        f"for task {task_id}"
                    )
                row.plan_json = plan_json
                row.description = description
                row.plan_generation_id = plan_generation_id
                # Compute has_proposed_tasks from plan_json
                try:
                    data = json.loads(plan_json)
                    proposed = data.get("proposed_tasks", []) if isinstance(data, dict) else []
                    row.has_proposed_tasks = len(proposed) > 0
                except (json.JSONDecodeError, TypeError):
                    row.has_proposed_tasks = False
            elif new_status == PlanStatus.FAILED:
                row.plan_json = None
                # Preserve description if no new one provided
                if description is not None:
                    row.description = description
            elif new_status == PlanStatus.DECOMPOSED:
                # Preserve all fields
                pass

            row.plan_status = new_status

            if complexity is not None:
                row.complexity = complexity
            if replan_attempt is not None:
                row.replan_attempt = replan_attempt

            row.updated_at = datetime.now(UTC).isoformat()

    async def update_plan(
        self,
        task_id: str,
        description: str,
        plan_status: str,
        plan_json: str | None,
        complexity: str | None = None,
    ) -> None:
        """Atomically update plan fields (description + status + json).

        .. deprecated:: Use :meth:`set_plan_state` instead for transition
           validation and invariant enforcement.

        All three fields are written in a single transaction so they are
        always consistent.  This avoids the get-then-update pattern that
        can lose data if a concurrent request modifies the task between
        the read and the write.

        Args:
            task_id: The task to update.
            description: Formatted plan text for display.
            plan_status: New plan lifecycle state (e.g. 'ready').
            plan_json: JSON string of the structured plan data, or None to clear.
            complexity: Optional complexity override (``"S"``, ``"M"``, ``"L"``).

        Raises:
            ValueError: If the task is not found or is deleted.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")
            row.description = description
            row.plan_status = plan_status
            row.plan_json = plan_json
            if complexity is not None:
                row.complexity = complexity
            row.updated_at = datetime.now(UTC).isoformat()

    async def update_task(self, task: Task) -> Task:
        """Persist arbitrary field updates from a Task object.

        The task must already exist in the database (and not be soft-deleted).
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task.id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task.id}")

            data = task.model_dump(mode="json")
            kwargs = task_dict_to_row_kwargs(data)
            for key, value in kwargs.items():
                setattr(row, key, value)

        return task

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_ready_tasks(self, limit: int = 10) -> list[Task]:
        """Return tasks that are QUEUED, ordered by priority then creation time.

        Priority is extracted from the local_task_id (e.g. T-P0-99 -> priority 0).
        Lower priority numbers are dispatched first (P0 before P1).
        Soft-deleted tasks are excluded.
        Caller (Scheduler) is responsible for additional dep/concurrency checks.
        """
        async with get_session(self._sf) as session:
            stmt = (
                select(TaskRow)
                .where(TaskRow.status == TaskStatus.QUEUED.value)
                .where(TaskRow.is_deleted == False)  # noqa: E712
                .order_by(TaskRow.created_at)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            tasks = [Task.model_validate(task_row_to_dict(r)) for r in rows]
            tasks.sort(key=lambda t: (extract_priority(t.local_task_id), t.created_at))
            return tasks[:limit]

    async def count_running_by_project(self, project_id: str) -> int:
        """Count tasks in RUNNING status for a given project."""
        async with get_session(self._sf) as session:
            stmt = (
                select(TaskRow)
                .where(TaskRow.project_id == project_id)
                .where(TaskRow.status == TaskStatus.RUNNING.value)
                .where(TaskRow.is_deleted == False)  # noqa: E712
            )
            result = await session.execute(stmt)
            return len(result.scalars().all())

    async def delete_task(
        self,
        task_id: str,
        *,
        force: bool = False,
    ) -> None:
        """Soft-delete a task by setting is_deleted=True.

        Raises ``ValueError`` if:
        - Task not found (or already deleted)
        - Task is RUNNING (cannot delete active process)
        - Task has non-deleted dependents (unless *force* is True)

        Returns the list of dependent task IDs that blocked deletion
        (empty on success).
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")

            if row.status == TaskStatus.RUNNING.value:
                raise ValueError(
                    f"Cannot delete RUNNING task {task_id}. Cancel it first."
                )

            # Check for non-deleted dependents (tasks that depend on this one)
            dep_stmt = (
                select(TaskRow)
                .where(TaskRow.is_deleted == False)  # noqa: E712
                .where(TaskRow.id != task_id)
            )
            dep_result = await session.execute(dep_stmt)
            all_active = dep_result.scalars().all()

            dependents: list[str] = []
            for other in all_active:
                deps_list = (
                    json.loads(other.depends_on_json)
                    if other.depends_on_json
                    else []
                )
                if task_id in deps_list:
                    dependents.append(other.id)

            if dependents and not force:
                raise ValueError(
                    f"Task {task_id} has active dependents: "
                    + ", ".join(dependents)
                )

            now = datetime.now(UTC).isoformat()
            row.is_deleted = True
            row.deleted_source = "user"
            row.updated_at = now
            logger.info("Soft-deleted task %s (force=%s)", task_id, force)

    async def get_dependents(self, task_id: str) -> list[str]:
        """Return IDs of non-deleted tasks that depend on *task_id*."""
        async with get_session(self._sf) as session:
            stmt = (
                select(TaskRow)
                .where(TaskRow.is_deleted == False)  # noqa: E712
                .where(TaskRow.id != task_id)
            )
            result = await session.execute(stmt)
            all_active = result.scalars().all()

            dependents: list[str] = []
            for other in all_active:
                deps_list = (
                    json.loads(other.depends_on_json)
                    if other.depends_on_json
                    else []
                )
                if task_id in deps_list:
                    dependents.append(other.id)
            return dependents

    async def sync_mark_removed(
        self,
        project_id: str,
        parsed_ids: set[str],
    ) -> int:
        """Mark tasks for *project_id* not in *parsed_ids* as sync-deleted.

        Only affects non-deleted tasks. Tasks already deleted by the user
        (``deleted_source='user'``) are left untouched. Returns the count
        of newly sync-deleted tasks.
        """
        async with get_session(self._sf) as session:
            stmt = (
                select(TaskRow)
                .where(TaskRow.project_id == project_id)
                .where(TaskRow.is_deleted == False)  # noqa: E712
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

            now = datetime.now(UTC).isoformat()
            count = 0
            for row in rows:
                if row.id not in parsed_ids:
                    row.is_deleted = True
                    row.deleted_source = "sync"
                    row.updated_at = now
                    count += 1
                    logger.info("Sync-deleted task %s (removed from TASKS.md)", row.id)

            return count

    async def mark_running_as_failed(self) -> int:
        """Mark all RUNNING tasks as FAILED (startup recovery).

        Returns the number of tasks affected.
        """
        now = datetime.now(UTC).isoformat()
        async with get_session(self._sf) as session:
            # First count them (exclude soft-deleted)
            stmt = (
                select(TaskRow)
                .where(TaskRow.status == TaskStatus.RUNNING.value)
                .where(TaskRow.is_deleted == False)  # noqa: E712
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            count = len(rows)

            if count == 0:
                return 0

            # Update each row with error summary
            for row in rows:
                row.status = TaskStatus.FAILED.value
                row.updated_at = now
                # Update execution state with recovery info
                exec_data = json.loads(row.execution_json) if row.execution_json else {}
                exec_data["result"] = "failed"
                exec_data["error_summary"] = (
                    "Recovered from crash -- was RUNNING when process exited"
                )
                row.execution_json = json.dumps(exec_data)

            logger.warning("Startup recovery: %d orphaned tasks marked FAILED", count)
            return count
