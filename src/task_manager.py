"""TaskManager -- CRUD operations and state machine for tasks.

Implements the task lifecycle from PRD Section 5.3 with valid state
transitions enforced by ``update_status``.  Supports bidirectional
transitions (backward drags) and optimistic concurrency control.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import TaskRow, get_session, task_dict_to_row_kwargs, task_row_to_dict
from src.models import ExecutionState, Task, TaskStatus

logger = logging.getLogger(__name__)


class ReviewGateBlockedError(Exception):
    """Raised when the review gate blocks a status transition.

    Carries enough context for the API layer to return HTTP 428
    (Precondition Required) with a ``gate_action`` hint.
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
    TaskStatus.REVIEW_NEEDS_HUMAN: {TaskStatus.QUEUED, TaskStatus.BACKLOG},
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
        review_gate_enabled: bool = False,
        reason: str = "",
        expected_updated_at: str | None = None,
    ) -> Task:
        """Transition a task to *new_status*, enforcing the state machine.

        When *review_gate_enabled* is True, BACKLOG -> QUEUED is blocked;
        the task must go through REVIEW first (Layer 1 review gate).

        *reason* is an optional human-supplied note for backward transitions
        (logged but not persisted on the task itself).

        *expected_updated_at* enables optimistic concurrency control.  If
        provided, the row's ``updated_at`` must match exactly; otherwise
        ``OptimisticLockError`` is raised (HTTP 409 with ``conflict=true``).

        Raises ``ValueError`` on illegal transitions or missing tasks.
        Raises ``ReviewGateBlockedError`` when the review gate blocks
        the transition (callers should return HTTP 428).
        Raises ``OptimisticLockError`` on concurrent-edit conflict.
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")

            current = TaskStatus(row.status)
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

            now = datetime.now(UTC).isoformat()
            row.status = new_status.value
            row.updated_at = now

            if new_status == TaskStatus.DONE:
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

        # DONE -> QUEUED: clear completed_at, execution_state
        elif current == TaskStatus.DONE and target == TaskStatus.QUEUED:
            row.completed_at = None
            row.execution_json = None

        # FAILED -> QUEUED: clear error_summary, execution_state
        elif current == TaskStatus.FAILED and target == TaskStatus.QUEUED:
            row.execution_json = None

        # QUEUED -> REVIEW: no cleanup needed (just status change)
        # QUEUED -> BACKLOG is handled by the * -> BACKLOG rule above

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
        """Return tasks that are QUEUED, ordered by creation time.

        Soft-deleted tasks are excluded.
        Caller (Scheduler) is responsible for additional dep/concurrency checks.
        """
        async with get_session(self._sf) as session:
            stmt = (
                select(TaskRow)
                .where(TaskRow.status == TaskStatus.QUEUED.value)
                .where(TaskRow.is_deleted == False)  # noqa: E712
                .order_by(TaskRow.created_at)
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [Task.model_validate(task_row_to_dict(r)) for r in rows]

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
