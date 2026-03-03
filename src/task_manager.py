"""TaskManager -- CRUD operations and state machine for tasks.

Implements the task lifecycle from PRD Section 5.3 with valid state
transitions enforced by ``update_status``.
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


# ---------------------------------------------------------------------------
# Valid state transitions per PRD Section 5.3
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.BACKLOG: {TaskStatus.REVIEW, TaskStatus.QUEUED},
    TaskStatus.REVIEW: {
        TaskStatus.REVIEW_AUTO_APPROVED,
        TaskStatus.REVIEW_NEEDS_HUMAN,
    },
    TaskStatus.REVIEW_AUTO_APPROVED: {TaskStatus.QUEUED},
    TaskStatus.REVIEW_NEEDS_HUMAN: {TaskStatus.QUEUED, TaskStatus.BACKLOG},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.BLOCKED},
    TaskStatus.RUNNING: {TaskStatus.DONE, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.QUEUED, TaskStatus.BLOCKED},
    TaskStatus.DONE: set(),
    TaskStatus.BLOCKED: {TaskStatus.QUEUED, TaskStatus.BACKLOG},
}


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
    ) -> Task:
        """Transition a task to *new_status*, enforcing the state machine.

        When *review_gate_enabled* is True, BACKLOG -> QUEUED is blocked;
        the task must go through REVIEW first (Layer 1 review gate).

        Raises ``ValueError`` on illegal transitions or missing tasks.
        Raises ``ReviewGateBlockedError`` when the review gate blocks
        the transition (callers should return HTTP 428).
        """
        async with get_session(self._sf) as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.is_deleted:
                raise ValueError(f"Task not found: {task_id}")

            current = TaskStatus(row.status)
            if new_status not in VALID_TRANSITIONS.get(current, set()):
                raise ValueError(
                    f"Invalid transition: {current.value} -> {new_status.value} "
                    f"for task {task_id}"
                )

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

            return Task.model_validate(task_row_to_dict(row))

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
