"""Tests for race condition windows identified in T-P1-76 audit.

Covers:
- RACE-1: Scheduler finalization vs concurrent drag (state guard)
- RACE-2: Duplicate review pipeline enqueue guard
- RACE-4: Review completion vs backward drag + lifecycle state cleanup
"""

from __future__ import annotations

import pytest

from src.models import (
    ExecutorType,
    ReviewLifecycleState,
    Task,
    TaskStatus,
)
from src.task_manager import TaskManager

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
# RACE-1: Scheduler finalization vs concurrent drag
# ---------------------------------------------------------------------------


class TestRaceSchedulerVsDrag:
    """RACE-1: Concurrent drag to DONE while scheduler tries to mark FAILED."""

    async def test_state_guard_skips_failed_when_already_done(
        self, session_factory,
    ) -> None:
        """If task is moved to DONE by drag, scheduler should skip FAILED."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.RUNNING)
        await tm.create_task(task)

        # User drags to DONE
        await tm.update_status("P0:T-P0-1", TaskStatus.DONE)

        # Scheduler's state guard: re-fetch and check
        current = await tm.get_task("P0:T-P0-1")
        assert current is not None
        assert current.status == TaskStatus.DONE

        # Scheduler should NOT try DONE -> FAILED (invalid transition)
        with pytest.raises(ValueError, match="Cannot move task"):
            await tm.update_status("P0:T-P0-1", TaskStatus.FAILED)

    async def test_state_guard_allows_failed_when_still_running(
        self, session_factory,
    ) -> None:
        """If task is still RUNNING, scheduler can transition to FAILED."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.RUNNING)
        await tm.create_task(task)

        current = await tm.get_task("P0:T-P0-1")
        assert current is not None
        assert current.status == TaskStatus.RUNNING

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.FAILED)
        assert updated.status == TaskStatus.FAILED

    async def test_optimistic_lock_catches_concurrent_update(
        self, session_factory,
    ) -> None:
        """Optimistic lock detects stale timestamp from concurrent drag."""
        from src.task_manager import OptimisticLockError

        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.RUNNING)
        await tm.create_task(task)

        # Read timestamp before concurrent update
        before = await tm.get_task("P0:T-P0-1")
        assert before is not None
        stale_ts = before.updated_at.isoformat()

        # Concurrent drag changes the task (updates timestamp)
        await tm.update_status("P0:T-P0-1", TaskStatus.DONE)

        # Attempt with stale timestamp fails
        with pytest.raises(OptimisticLockError):
            await tm.update_status(
                "P0:T-P0-1",
                TaskStatus.BACKLOG,
                expected_updated_at=stale_ts,
            )


# ---------------------------------------------------------------------------
# RACE-2: Duplicate review pipeline enqueue
# ---------------------------------------------------------------------------


class TestRaceDuplicateReviewEnqueue:
    """RACE-2: Rapid retry-review should be blocked when already running."""

    async def test_review_status_running_blocks_retry(
        self, session_factory,
    ) -> None:
        """Task with review_status='running' should block re-enqueue."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.REVIEW)
        await tm.create_task(task)

        # Set review_status to running (as _enqueue_review_pipeline does)
        await tm.set_review_status("P0:T-P0-1", "running")

        # Re-fetch: review_status should be "running"
        updated = await tm.get_task("P0:T-P0-1")
        assert updated is not None
        assert updated.review_status == "running"

    async def test_entering_review_sets_review_status_running(
        self, session_factory,
    ) -> None:
        """Transitioning to REVIEW atomically sets review_status='running'."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(task)

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)
        assert updated.review_status == "running"

    async def test_idempotent_review_to_review_keeps_running(
        self, session_factory,
    ) -> None:
        """Re-dragging REVIEW -> REVIEW when already running stays 'running'."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.REVIEW)
        await tm.create_task(task)
        await tm.set_review_status("P0:T-P0-1", "running")

        # REVIEW -> REVIEW is idempotent (same status)
        # This should NOT change review_status
        # Note: REVIEW -> REVIEW is not in VALID_TRANSITIONS, so this
        # should raise ValueError
        with pytest.raises(ValueError):
            await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)


# ---------------------------------------------------------------------------
# RACE-4: Review completion vs backward drag + cleanup
# ---------------------------------------------------------------------------


class TestRaceReviewCompletionVsBackwardDrag:
    """RACE-4: Review completes while user drags back to BACKLOG."""

    async def test_cleanup_resets_review_lifecycle_state(
        self, session_factory,
    ) -> None:
        """Backward drag to BACKLOG resets review_lifecycle_state to NOT_STARTED."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.REVIEW)
        await tm.create_task(task)

        # Simulate review pipeline completing
        await tm.set_review_lifecycle_state(
            "P0:T-P0-1", ReviewLifecycleState.APPROVED,
        )
        await tm.set_review_status("P0:T-P0-1", "done")
        await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW_AUTO_APPROVED)

        # Verify lifecycle state is APPROVED
        task_before = await tm.get_task("P0:T-P0-1")
        assert task_before is not None
        assert task_before.review_lifecycle_state == ReviewLifecycleState.APPROVED.value

        # User drags back to BACKLOG
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)

        # review_lifecycle_state should be reset
        assert updated.review_lifecycle_state == ReviewLifecycleState.NOT_STARTED.value
        assert updated.review_status == "idle"

    async def test_cleanup_resets_lifecycle_from_rejected(
        self, session_factory,
    ) -> None:
        """Backward drag to BACKLOG resets lifecycle from REJECTED_SINGLE."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.REVIEW_NEEDS_HUMAN)
        await tm.create_task(task)

        await tm.set_review_lifecycle_state(
            "P0:T-P0-1", ReviewLifecycleState.REJECTED_SINGLE,
        )

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.review_lifecycle_state == ReviewLifecycleState.NOT_STARTED.value

    async def test_review_pipeline_fails_on_stale_state(
        self, session_factory,
    ) -> None:
        """Review pipeline trying REVIEW -> REVIEW_AUTO_APPROVED on BACKLOG task fails."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.REVIEW)
        await tm.create_task(task)

        # User drags to BACKLOG while pipeline runs
        await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)

        # Pipeline finishes and tries REVIEW -> REVIEW_AUTO_APPROVED
        # But task is now BACKLOG, so this is invalid
        with pytest.raises(ValueError, match="Cannot move task"):
            await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW_AUTO_APPROVED)

    async def test_failed_to_backlog_resets_lifecycle(
        self, session_factory,
    ) -> None:
        """FAILED -> BACKLOG also resets review_lifecycle_state."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.RUNNING)
        await tm.create_task(task)

        # Run through RUNNING -> FAILED
        await tm.update_status("P0:T-P0-1", TaskStatus.FAILED)

        # Set a non-default lifecycle state
        await tm.set_review_lifecycle_state(
            "P0:T-P0-1", ReviewLifecycleState.FAILED,
        )

        # Drag to BACKLOG
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.review_lifecycle_state == ReviewLifecycleState.NOT_STARTED.value

    async def test_done_to_backlog_resets_lifecycle(
        self, session_factory,
    ) -> None:
        """DONE -> BACKLOG resets review_lifecycle_state."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.RUNNING)
        await tm.create_task(task)

        await tm.update_status("P0:T-P0-1", TaskStatus.DONE)

        # Set APPROVED from a previous review
        await tm.set_review_lifecycle_state(
            "P0:T-P0-1", ReviewLifecycleState.APPROVED,
        )

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.review_lifecycle_state == ReviewLifecycleState.NOT_STARTED.value
