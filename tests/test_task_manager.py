"""Tests for TaskManager in src/task_manager.py."""

from __future__ import annotations

import pytest

from src.models import ExecutorType, Task, TaskStatus
from src.task_manager import VALID_TRANSITIONS, TaskManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "P0:T-P0-1",
    project_id: str = "P0",
    local_task_id: str = "T-P0-1",
    title: str = "Test task",
    status: TaskStatus = TaskStatus.BACKLOG,
    **kwargs,
) -> Task:
    """Create a Task with sensible defaults."""
    return Task(
        id=task_id,
        project_id=project_id,
        local_task_id=local_task_id,
        title=title,
        status=status,
        executor_type=kwargs.pop("executor_type", ExecutorType.CODE),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------


class TestTaskManagerCrud:
    """TaskManager create / get / list operations."""

    async def test_create_and_get(self, session_factory) -> None:
        """Create a task and retrieve it by id."""
        tm = TaskManager(session_factory)
        task = _make_task()
        await tm.create_task(task)

        fetched = await tm.get_task("P0:T-P0-1")
        assert fetched is not None
        assert fetched.title == "Test task"
        assert fetched.status == TaskStatus.BACKLOG

    async def test_create_duplicate_raises(self, session_factory) -> None:
        """Creating a task with the same id should raise ValueError."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        with pytest.raises(ValueError, match="already exists"):
            await tm.create_task(_make_task())

    async def test_get_nonexistent(self, session_factory) -> None:
        """Getting a non-existent task returns None."""
        tm = TaskManager(session_factory)
        assert await tm.get_task("P0:T-doesnt-exist") is None

    async def test_list_all(self, session_factory) -> None:
        """List all tasks without filters."""
        tm = TaskManager(session_factory)
        for i in range(3):
            await tm.create_task(_make_task(task_id=f"P0:T-P0-{i}", local_task_id=f"T-P0-{i}"))

        tasks = await tm.list_tasks()
        assert len(tasks) == 3

    async def test_list_by_project(self, session_factory) -> None:
        """Filter tasks by project_id."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-1", project_id="P0", local_task_id="T-1"))
        await tm.create_task(_make_task(task_id="P1:T-1", project_id="P1", local_task_id="T-1"))

        p0_tasks = await tm.list_tasks(project_id="P0")
        assert len(p0_tasks) == 1
        assert p0_tasks[0].project_id == "P0"

    async def test_list_by_status(self, session_factory) -> None:
        """Filter tasks by status."""
        tm = TaskManager(session_factory)
        await tm.create_task(
            _make_task(task_id="P0:T-1", local_task_id="T-1", status=TaskStatus.BACKLOG)
        )
        await tm.create_task(
            _make_task(task_id="P0:T-2", local_task_id="T-2", status=TaskStatus.QUEUED)
        )

        queued = await tm.list_tasks(status=TaskStatus.QUEUED)
        assert len(queued) == 1
        assert queued[0].status == TaskStatus.QUEUED


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------


class TestStateMachine:
    """TaskManager.update_status enforces valid transitions."""

    async def test_backlog_to_review(self, session_factory) -> None:
        """BACKLOG -> REVIEW is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)
        assert updated.status == TaskStatus.REVIEW

    async def test_backlog_to_queued(self, session_factory) -> None:
        """BACKLOG -> QUEUED (skip review path) is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_queued_to_running(self, session_factory) -> None:
        """QUEUED -> RUNNING is valid and initializes execution state."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)
        assert updated.status == TaskStatus.RUNNING

    async def test_running_to_done(self, session_factory) -> None:
        """RUNNING -> DONE is valid and sets completed_at."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.DONE)
        assert updated.status == TaskStatus.DONE
        assert updated.completed_at is not None

    async def test_running_to_failed(self, session_factory) -> None:
        """RUNNING -> FAILED is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.FAILED)
        assert updated.status == TaskStatus.FAILED

    async def test_failed_to_queued(self, session_factory) -> None:
        """FAILED -> QUEUED (retry) is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.FAILED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_failed_to_blocked(self, session_factory) -> None:
        """FAILED -> BLOCKED (max retries exhausted) is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.FAILED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BLOCKED)
        assert updated.status == TaskStatus.BLOCKED

    async def test_review_to_auto_approved(self, session_factory) -> None:
        """REVIEW -> REVIEW_AUTO_APPROVED is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.REVIEW))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW_AUTO_APPROVED)
        assert updated.status == TaskStatus.REVIEW_AUTO_APPROVED

    async def test_review_to_needs_human(self, session_factory) -> None:
        """REVIEW -> REVIEW_NEEDS_HUMAN is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.REVIEW))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW_NEEDS_HUMAN)
        assert updated.status == TaskStatus.REVIEW_NEEDS_HUMAN

    async def test_auto_approved_to_queued(self, session_factory) -> None:
        """REVIEW_AUTO_APPROVED -> QUEUED is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.REVIEW_AUTO_APPROVED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_needs_human_to_queued(self, session_factory) -> None:
        """REVIEW_NEEDS_HUMAN -> QUEUED (human approved) is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.REVIEW_NEEDS_HUMAN))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_blocked_to_queued(self, session_factory) -> None:
        """BLOCKED -> QUEUED is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.BLOCKED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    # --- Invalid transitions ---

    async def test_backlog_to_running_invalid(self, session_factory) -> None:
        """BACKLOG -> RUNNING is NOT valid (must go through QUEUED)."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        with pytest.raises(ValueError, match="Invalid transition"):
            await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)

    async def test_done_to_anything_invalid(self, session_factory) -> None:
        """DONE is a terminal state -- no transitions allowed."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.DONE))
        with pytest.raises(ValueError, match="Invalid transition"):
            await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)

    async def test_queued_to_done_invalid(self, session_factory) -> None:
        """QUEUED -> DONE is NOT valid (must go through RUNNING)."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))
        with pytest.raises(ValueError, match="Invalid transition"):
            await tm.update_status("P0:T-P0-1", TaskStatus.DONE)

    async def test_running_to_backlog_invalid(self, session_factory) -> None:
        """RUNNING -> BACKLOG is NOT valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        with pytest.raises(ValueError, match="Invalid transition"):
            await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)

    async def test_update_nonexistent_raises(self, session_factory) -> None:
        """Updating a non-existent task raises ValueError."""
        tm = TaskManager(session_factory)
        with pytest.raises(ValueError, match="not found"):
            await tm.update_status("P0:T-doesnt-exist", TaskStatus.QUEUED)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


class TestQueryHelpers:
    """Tests for get_ready_tasks, count_running_by_project, mark_running_as_failed."""

    async def test_get_ready_tasks(self, session_factory) -> None:
        """get_ready_tasks returns QUEUED tasks ordered by creation."""
        tm = TaskManager(session_factory)
        # Create tasks in different statuses
        await tm.create_task(
            _make_task(task_id="P0:T-1", local_task_id="T-1", status=TaskStatus.QUEUED)
        )
        await tm.create_task(
            _make_task(task_id="P0:T-2", local_task_id="T-2", status=TaskStatus.BACKLOG)
        )
        await tm.create_task(
            _make_task(task_id="P0:T-3", local_task_id="T-3", status=TaskStatus.QUEUED)
        )

        ready = await tm.get_ready_tasks()
        assert len(ready) == 2
        assert all(t.status == TaskStatus.QUEUED for t in ready)

    async def test_get_ready_tasks_limit(self, session_factory) -> None:
        """get_ready_tasks respects the limit parameter."""
        tm = TaskManager(session_factory)
        for i in range(5):
            await tm.create_task(
                _make_task(
                    task_id=f"P0:T-{i}",
                    local_task_id=f"T-{i}",
                    status=TaskStatus.QUEUED,
                )
            )

        ready = await tm.get_ready_tasks(limit=2)
        assert len(ready) == 2

    async def test_count_running_by_project(self, session_factory) -> None:
        """Count running tasks per project."""
        tm = TaskManager(session_factory)
        await tm.create_task(
            _make_task(
                task_id="P0:T-1", project_id="P0", local_task_id="T-1", status=TaskStatus.RUNNING
            )
        )
        await tm.create_task(
            _make_task(
                task_id="P0:T-2", project_id="P0", local_task_id="T-2", status=TaskStatus.QUEUED
            )
        )
        await tm.create_task(
            _make_task(
                task_id="P1:T-1", project_id="P1", local_task_id="T-1", status=TaskStatus.RUNNING
            )
        )

        assert await tm.count_running_by_project("P0") == 1
        assert await tm.count_running_by_project("P1") == 1
        assert await tm.count_running_by_project("P99") == 0

    async def test_mark_running_as_failed(self, session_factory) -> None:
        """Startup recovery: all RUNNING tasks become FAILED."""
        tm = TaskManager(session_factory)
        await tm.create_task(
            _make_task(
                task_id="P0:T-1", local_task_id="T-1", status=TaskStatus.RUNNING,
            )
        )
        await tm.create_task(
            _make_task(
                task_id="P0:T-2", local_task_id="T-2", status=TaskStatus.RUNNING,
            )
        )
        await tm.create_task(
            _make_task(
                task_id="P0:T-3", local_task_id="T-3", status=TaskStatus.QUEUED,
            )
        )

        count = await tm.mark_running_as_failed()
        assert count == 2

        # Verify statuses
        t1 = await tm.get_task("P0:T-1")
        assert t1.status == TaskStatus.FAILED
        assert t1.execution is not None
        assert "crash" in t1.execution.error_summary.lower()

        t3 = await tm.get_task("P0:T-3")
        assert t3.status == TaskStatus.QUEUED  # Untouched

    async def test_mark_running_as_failed_none(self, session_factory) -> None:
        """No-op when no tasks are running."""
        tm = TaskManager(session_factory)
        await tm.create_task(
            _make_task(task_id="P0:T-1", local_task_id="T-1", status=TaskStatus.QUEUED)
        )
        count = await tm.mark_running_as_failed()
        assert count == 0


# ---------------------------------------------------------------------------
# Update task
# ---------------------------------------------------------------------------


class TestUpdateTask:
    """Tests for TaskManager.update_task."""

    async def test_update_task_fields(self, session_factory) -> None:
        """update_task persists arbitrary changes."""
        tm = TaskManager(session_factory)
        task = _make_task(title="Original")
        await tm.create_task(task)

        task.title = "Updated"
        task.description = "New description"
        await tm.update_task(task)

        fetched = await tm.get_task("P0:T-P0-1")
        assert fetched.title == "Updated"
        assert fetched.description == "New description"

    async def test_update_nonexistent_raises(self, session_factory) -> None:
        """Updating a non-existent task raises ValueError."""
        tm = TaskManager(session_factory)
        task = _make_task(task_id="P0:T-nope")
        with pytest.raises(ValueError, match="not found"):
            await tm.update_task(task)


# ---------------------------------------------------------------------------
# Transition map completeness
# ---------------------------------------------------------------------------


class TestTransitionMap:
    """Verify the VALID_TRANSITIONS map covers all states."""

    def test_all_statuses_have_entry(self) -> None:
        """Every TaskStatus should have a key in VALID_TRANSITIONS."""
        for status in TaskStatus:
            assert status in VALID_TRANSITIONS, f"Missing entry for {status}"

    def test_done_is_terminal(self) -> None:
        """DONE should have no outgoing transitions."""
        assert VALID_TRANSITIONS[TaskStatus.DONE] == set()
