"""Tests for bidirectional state transitions + concurrency control (T-P0-23).

Covers:
- All backward transitions in VALID_TRANSITIONS
- RUNNING -> anywhere (except DONE/FAILED) blocked with clear message
- DONE -> BACKLOG/QUEUED resets fields per cleanup matrix
- Backward transitions with optional reason
- Optimistic concurrency control via expected_updated_at
- Existing forward transitions still work
- User-friendly error messages
"""

from __future__ import annotations

import pytest

from src.models import ExecutionState, ExecutorType, Task, TaskStatus
from src.task_manager import (
    VALID_TRANSITIONS,
    OptimisticLockError,
    ReviewGateBlockedError,
    TaskManager,
    _build_transition_error,
)

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
# Backward transition validity tests
# ---------------------------------------------------------------------------


class TestBackwardTransitions:
    """All backward transitions specified in the transition table work."""

    async def test_review_to_backlog(self, session_factory) -> None:
        """REVIEW -> BACKLOG is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.REVIEW))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG

    async def test_review_auto_approved_to_backlog(self, session_factory) -> None:
        """REVIEW_AUTO_APPROVED -> BACKLOG is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.REVIEW_AUTO_APPROVED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG

    async def test_review_needs_human_to_backlog(self, session_factory) -> None:
        """REVIEW_NEEDS_HUMAN -> BACKLOG is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.REVIEW_NEEDS_HUMAN))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG

    async def test_queued_to_backlog(self, session_factory) -> None:
        """QUEUED -> BACKLOG is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG

    async def test_queued_to_review(self, session_factory) -> None:
        """QUEUED -> REVIEW is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)
        assert updated.status == TaskStatus.REVIEW

    async def test_failed_to_backlog(self, session_factory) -> None:
        """FAILED -> BACKLOG is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.FAILED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG

    async def test_done_to_backlog(self, session_factory) -> None:
        """DONE -> BACKLOG (reopen) is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.DONE))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG

    async def test_done_to_queued(self, session_factory) -> None:
        """DONE -> QUEUED (reopen, skip review) is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.DONE))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_blocked_to_backlog(self, session_factory) -> None:
        """BLOCKED -> BACKLOG is valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.BLOCKED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG


# ---------------------------------------------------------------------------
# RUNNING restrictions
# ---------------------------------------------------------------------------


class TestRunningRestrictions:
    """RUNNING -> anywhere (except DONE/FAILED) is blocked."""

    async def test_running_to_done_valid(self, session_factory) -> None:
        """RUNNING -> DONE is still valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.DONE)
        assert updated.status == TaskStatus.DONE

    async def test_running_to_failed_valid(self, session_factory) -> None:
        """RUNNING -> FAILED is still valid."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.FAILED)
        assert updated.status == TaskStatus.FAILED

    async def test_running_to_backlog_blocked(self, session_factory) -> None:
        """RUNNING -> BACKLOG is blocked with clear message."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        with pytest.raises(ValueError, match="currently running"):
            await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)

    async def test_running_to_queued_blocked(self, session_factory) -> None:
        """RUNNING -> QUEUED is blocked with clear message."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        with pytest.raises(ValueError, match="currently running"):
            await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)

    async def test_running_to_review_blocked(self, session_factory) -> None:
        """RUNNING -> REVIEW is blocked with clear message."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        with pytest.raises(ValueError, match="currently running"):
            await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)

    async def test_running_to_blocked_blocked(self, session_factory) -> None:
        """RUNNING -> BLOCKED is blocked with clear message."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        with pytest.raises(ValueError, match="currently running"):
            await tm.update_status("P0:T-P0-1", TaskStatus.BLOCKED)


# ---------------------------------------------------------------------------
# Timestamp cleanup matrix
# ---------------------------------------------------------------------------


class TestTimestampCleanup:
    """Backward transitions clean up fields per the cleanup matrix."""

    async def test_done_to_backlog_clears_completed_and_execution(
        self, session_factory
    ) -> None:
        """DONE -> BACKLOG clears completed_at and execution_json."""
        tm = TaskManager(session_factory)
        task = _make_task(
            status=TaskStatus.DONE,
            execution=ExecutionState(result="success"),
        )
        task.completed_at = task.created_at  # Set a completed_at
        await tm.create_task(task)

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG
        assert updated.completed_at is None
        assert updated.execution is None

    async def test_done_to_queued_clears_completed_and_execution(
        self, session_factory
    ) -> None:
        """DONE -> QUEUED clears completed_at and execution state."""
        tm = TaskManager(session_factory)
        task = _make_task(
            status=TaskStatus.DONE,
            execution=ExecutionState(result="success"),
        )
        task.completed_at = task.created_at
        await tm.create_task(task)

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED
        assert updated.completed_at is None
        assert updated.execution is None

    async def test_failed_to_queued_clears_execution(
        self, session_factory
    ) -> None:
        """FAILED -> QUEUED clears execution state."""
        tm = TaskManager(session_factory)
        task = _make_task(
            status=TaskStatus.FAILED,
            execution=ExecutionState(
                result="failed",
                error_summary="Something broke",
            ),
        )
        await tm.create_task(task)

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED
        assert updated.execution is None

    async def test_failed_to_backlog_clears_all(
        self, session_factory
    ) -> None:
        """FAILED -> BACKLOG clears completed_at, execution, error_summary."""
        tm = TaskManager(session_factory)
        task = _make_task(
            status=TaskStatus.FAILED,
            execution=ExecutionState(
                result="failed",
                error_summary="Something broke",
                error_type="NON_ZERO_EXIT",
            ),
        )
        await tm.create_task(task)

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG
        assert updated.completed_at is None
        assert updated.execution is None

    async def test_queued_to_review_no_cleanup(
        self, session_factory
    ) -> None:
        """QUEUED -> REVIEW does NOT clear any fields."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)
        assert updated.status == TaskStatus.REVIEW
        # updated_at should change (it's the transition timestamp)
        assert updated.updated_at is not None

    async def test_queued_to_backlog_clears_execution(
        self, session_factory
    ) -> None:
        """QUEUED -> BACKLOG clears execution state (if any)."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        assert updated.status == TaskStatus.BACKLOG
        assert updated.execution is None


# ---------------------------------------------------------------------------
# Reason field
# ---------------------------------------------------------------------------


class TestReasonField:
    """Optional reason on backward transitions is accepted and logged."""

    async def test_backward_with_reason(self, session_factory) -> None:
        """Backward transition with reason succeeds."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))
        updated = await tm.update_status(
            "P0:T-P0-1",
            TaskStatus.BACKLOG,
            reason="Requirements changed",
        )
        assert updated.status == TaskStatus.BACKLOG

    async def test_backward_with_empty_reason(self, session_factory) -> None:
        """Backward transition with empty reason succeeds."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))
        updated = await tm.update_status(
            "P0:T-P0-1",
            TaskStatus.BACKLOG,
            reason="",
        )
        assert updated.status == TaskStatus.BACKLOG

    async def test_forward_with_reason(self, session_factory) -> None:
        """Forward transition with reason also succeeds (no-op for reason)."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.BACKLOG))
        updated = await tm.update_status(
            "P0:T-P0-1",
            TaskStatus.QUEUED,
            reason="Ready to go",
        )
        assert updated.status == TaskStatus.QUEUED


# ---------------------------------------------------------------------------
# Optimistic locking
# ---------------------------------------------------------------------------


class TestOptimisticLocking:
    """Concurrent edits detected via expected_updated_at."""

    async def test_matching_updated_at_succeeds(self, session_factory) -> None:
        """Transition succeeds when expected_updated_at matches."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(task)

        # Get current updated_at
        fetched = await tm.get_task("P0:T-P0-1")
        assert fetched is not None
        expected = fetched.updated_at.isoformat()

        updated = await tm.update_status(
            "P0:T-P0-1",
            TaskStatus.QUEUED,
            expected_updated_at=expected,
        )
        assert updated.status == TaskStatus.QUEUED

    async def test_mismatched_updated_at_raises(self, session_factory) -> None:
        """Transition fails when expected_updated_at does not match."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.BACKLOG))

        with pytest.raises(OptimisticLockError, match="just updated"):
            await tm.update_status(
                "P0:T-P0-1",
                TaskStatus.QUEUED,
                expected_updated_at="2000-01-01T00:00:00+00:00",
            )

    async def test_no_expected_updated_at_skips_check(self, session_factory) -> None:
        """Without expected_updated_at, no locking check is done."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.BACKLOG))
        # Should succeed regardless of updated_at value
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_concurrent_edit_scenario(self, session_factory) -> None:
        """Simulate two users trying to transition the same task."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.BACKLOG))

        fetched = await tm.get_task("P0:T-P0-1")
        assert fetched is not None
        original_updated_at = fetched.updated_at.isoformat()

        # User A succeeds
        await tm.update_status(
            "P0:T-P0-1",
            TaskStatus.QUEUED,
            expected_updated_at=original_updated_at,
        )

        # User B fails with the stale timestamp
        with pytest.raises(OptimisticLockError):
            await tm.update_status(
                "P0:T-P0-1",
                TaskStatus.RUNNING,
                expected_updated_at=original_updated_at,
            )


# ---------------------------------------------------------------------------
# Error messages
# ---------------------------------------------------------------------------


class TestErrorMessages:
    """User-friendly error messages for invalid transitions."""

    def test_running_error_message(self) -> None:
        """Error from RUNNING includes 'currently running'."""
        msg = _build_transition_error(
            TaskStatus.RUNNING, TaskStatus.BACKLOG, "test-id"
        )
        assert "currently running" in msg
        assert "cancel" in msg.lower()

    def test_invalid_skip_error_lists_valid_targets(self) -> None:
        """Error for invalid skip lists valid target statuses."""
        msg = _build_transition_error(
            TaskStatus.BACKLOG, TaskStatus.RUNNING, "test-id"
        )
        assert "queued" in msg.lower() or "review" in msg.lower()
        assert "Valid targets" in msg

    def test_optimistic_lock_error_message(self) -> None:
        """OptimisticLockError has helpful message."""
        err = OptimisticLockError("test-id")
        assert "just updated" in str(err)
        assert "test-id" in err.task_id


# ---------------------------------------------------------------------------
# Existing forward transitions still work
# ---------------------------------------------------------------------------


class TestForwardTransitionsUnchanged:
    """Verify that existing forward transitions are not broken."""

    async def test_backlog_to_review(self, session_factory) -> None:
        """BACKLOG -> REVIEW still works."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)
        assert updated.status == TaskStatus.REVIEW

    async def test_backlog_to_queued(self, session_factory) -> None:
        """BACKLOG -> QUEUED still works."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_review_to_auto_approved(self, session_factory) -> None:
        """REVIEW -> REVIEW_AUTO_APPROVED still works."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.REVIEW))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW_AUTO_APPROVED)
        assert updated.status == TaskStatus.REVIEW_AUTO_APPROVED

    async def test_queued_to_running(self, session_factory) -> None:
        """QUEUED -> RUNNING still works and initializes execution state."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)
        assert updated.status == TaskStatus.RUNNING
        assert updated.execution is not None

    async def test_running_to_done_sets_completed(self, session_factory) -> None:
        """RUNNING -> DONE still sets completed_at."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.RUNNING))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.DONE)
        assert updated.status == TaskStatus.DONE
        assert updated.completed_at is not None

    async def test_failed_to_queued(self, session_factory) -> None:
        """FAILED -> QUEUED (retry) still works."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.FAILED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_review_gate_still_works(self, session_factory) -> None:
        """Review gate still blocks BACKLOG -> QUEUED."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        with pytest.raises(ReviewGateBlockedError):
            await tm.update_status(
                "P0:T-P0-1",
                TaskStatus.QUEUED,
                review_gate_enabled=True,
            )

    async def test_queued_to_blocked(self, session_factory) -> None:
        """QUEUED -> BLOCKED still works."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.BLOCKED)
        assert updated.status == TaskStatus.BLOCKED


# ---------------------------------------------------------------------------
# Transition table coverage
# ---------------------------------------------------------------------------


class TestTransitionTableCoverage:
    """Ensure the transition table matches the spec exactly."""

    def test_backlog_targets(self) -> None:
        """BACKLOG can go to REVIEW or QUEUED."""
        assert VALID_TRANSITIONS[TaskStatus.BACKLOG] == {
            TaskStatus.REVIEW, TaskStatus.QUEUED,
        }

    def test_review_targets(self) -> None:
        """REVIEW can go to REVIEW_AUTO_APPROVED, REVIEW_NEEDS_HUMAN, or BACKLOG."""
        assert VALID_TRANSITIONS[TaskStatus.REVIEW] == {
            TaskStatus.REVIEW_AUTO_APPROVED,
            TaskStatus.REVIEW_NEEDS_HUMAN,
            TaskStatus.BACKLOG,
        }

    def test_review_auto_approved_targets(self) -> None:
        """REVIEW_AUTO_APPROVED can go to QUEUED or BACKLOG."""
        assert VALID_TRANSITIONS[TaskStatus.REVIEW_AUTO_APPROVED] == {
            TaskStatus.QUEUED, TaskStatus.BACKLOG,
        }

    def test_review_needs_human_targets(self) -> None:
        """REVIEW_NEEDS_HUMAN can go to QUEUED or BACKLOG."""
        assert VALID_TRANSITIONS[TaskStatus.REVIEW_NEEDS_HUMAN] == {
            TaskStatus.QUEUED, TaskStatus.BACKLOG,
        }

    def test_queued_targets(self) -> None:
        """QUEUED can go to RUNNING, BLOCKED, BACKLOG, or REVIEW."""
        assert VALID_TRANSITIONS[TaskStatus.QUEUED] == {
            TaskStatus.RUNNING, TaskStatus.BLOCKED,
            TaskStatus.BACKLOG, TaskStatus.REVIEW,
        }

    def test_running_targets(self) -> None:
        """RUNNING can only go to DONE or FAILED."""
        assert VALID_TRANSITIONS[TaskStatus.RUNNING] == {
            TaskStatus.DONE, TaskStatus.FAILED,
        }

    def test_failed_targets(self) -> None:
        """FAILED can go to QUEUED, BLOCKED, or BACKLOG."""
        assert VALID_TRANSITIONS[TaskStatus.FAILED] == {
            TaskStatus.QUEUED, TaskStatus.BLOCKED, TaskStatus.BACKLOG,
        }

    def test_done_targets(self) -> None:
        """DONE can go to BACKLOG or QUEUED (reopen)."""
        assert VALID_TRANSITIONS[TaskStatus.DONE] == {
            TaskStatus.BACKLOG, TaskStatus.QUEUED,
        }

    def test_blocked_targets(self) -> None:
        """BLOCKED can go to QUEUED or BACKLOG."""
        assert VALID_TRANSITIONS[TaskStatus.BLOCKED] == {
            TaskStatus.QUEUED, TaskStatus.BACKLOG,
        }


# ---------------------------------------------------------------------------
# Full lifecycle scenarios
# ---------------------------------------------------------------------------


class TestLifecycleScenarios:
    """End-to-end lifecycle paths including backward transitions."""

    async def test_forward_backward_forward(self, session_factory) -> None:
        """BACKLOG -> QUEUED -> BACKLOG -> QUEUED -> RUNNING -> DONE."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())

        # Forward
        await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        # Backward
        await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        # Forward again
        await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.DONE)
        assert updated.status == TaskStatus.DONE
        assert updated.completed_at is not None

    async def test_done_reopen_cycle(self, session_factory) -> None:
        """DONE -> BACKLOG -> REVIEW -> ... -> DONE (reopen and redo)."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.DONE))

        # Reopen
        await tm.update_status("P0:T-P0-1", TaskStatus.BACKLOG)
        # Go through review
        await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)
        await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW_AUTO_APPROVED)
        await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.DONE)
        assert updated.status == TaskStatus.DONE

    async def test_failed_retry_success(self, session_factory) -> None:
        """FAILED -> QUEUED -> RUNNING -> DONE (retry succeeds)."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.FAILED))

        await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)
        updated = await tm.update_status("P0:T-P0-1", TaskStatus.DONE)
        assert updated.status == TaskStatus.DONE

    async def test_queued_send_back_to_review(self, session_factory) -> None:
        """QUEUED -> REVIEW (send back for review)."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(status=TaskStatus.QUEUED))

        updated = await tm.update_status("P0:T-P0-1", TaskStatus.REVIEW)
        assert updated.status == TaskStatus.REVIEW
