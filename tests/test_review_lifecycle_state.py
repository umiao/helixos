"""Tests for ReviewLifecycleState enum, transitions, and persistence.

Covers T-P0-40: Define Canonical ReviewLifecycleState enum in backend.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db import ReviewHistoryRow, TaskRow, get_session
from src.history_writer import HistoryWriter
from src.models import (
    REVIEW_LIFECYCLE_TRANSITIONS,
    LLMReview,
    ReviewLifecycleState,
    Task,
)
from src.task_manager import TaskManager

# ------------------------------------------------------------------
# Enum value tests
# ------------------------------------------------------------------


class TestReviewLifecycleStateEnum:
    """Verify the enum has all required values."""

    def test_all_states_defined(self) -> None:
        """All 7 states from the AC are present."""
        expected = {
            "not_started", "running", "partial", "failed",
            "rejected_single", "rejected_consensus", "approved",
        }
        actual = {s.value for s in ReviewLifecycleState}
        assert actual == expected

    def test_is_str_enum(self) -> None:
        """ReviewLifecycleState values are strings (StrEnum)."""
        for state in ReviewLifecycleState:
            assert isinstance(state, str)
            assert state == state.value

    def test_default_is_not_started(self) -> None:
        """Default lifecycle state should be NOT_STARTED."""
        assert ReviewLifecycleState.NOT_STARTED == "not_started"


# ------------------------------------------------------------------
# State machine transition tests
# ------------------------------------------------------------------


class TestLifecycleTransitions:
    """Verify the state machine transition map is complete and correct."""

    def test_all_states_have_transitions(self) -> None:
        """Every state appears as a key in the transition map."""
        for state in ReviewLifecycleState:
            assert state in REVIEW_LIFECYCLE_TRANSITIONS, (
                f"Missing transition entry for {state}"
            )

    def test_not_started_can_only_go_to_running(self) -> None:
        """NOT_STARTED -> RUNNING is the only valid transition."""
        assert REVIEW_LIFECYCLE_TRANSITIONS[ReviewLifecycleState.NOT_STARTED] == {
            ReviewLifecycleState.RUNNING,
        }

    def test_running_has_all_terminal_transitions(self) -> None:
        """RUNNING can transition to any terminal/error state."""
        expected = {
            ReviewLifecycleState.APPROVED,
            ReviewLifecycleState.PARTIAL,
            ReviewLifecycleState.FAILED,
            ReviewLifecycleState.REJECTED_SINGLE,
            ReviewLifecycleState.REJECTED_CONSENSUS,
        }
        assert REVIEW_LIFECYCLE_TRANSITIONS[ReviewLifecycleState.RUNNING] == expected

    def test_error_states_can_retry(self) -> None:
        """PARTIAL, FAILED, REJECTED_* can transition back to RUNNING."""
        retry_states = [
            ReviewLifecycleState.PARTIAL,
            ReviewLifecycleState.FAILED,
            ReviewLifecycleState.REJECTED_SINGLE,
            ReviewLifecycleState.REJECTED_CONSENSUS,
        ]
        for state in retry_states:
            assert ReviewLifecycleState.RUNNING in REVIEW_LIFECYCLE_TRANSITIONS[state], (
                f"{state} should be able to transition to RUNNING (retry)"
            )

    def test_all_non_running_states_can_reset(self) -> None:
        """All terminal states can reset to NOT_STARTED (backward drag)."""
        resettable = [
            ReviewLifecycleState.PARTIAL,
            ReviewLifecycleState.FAILED,
            ReviewLifecycleState.REJECTED_SINGLE,
            ReviewLifecycleState.REJECTED_CONSENSUS,
            ReviewLifecycleState.APPROVED,
        ]
        for state in resettable:
            assert ReviewLifecycleState.NOT_STARTED in REVIEW_LIFECYCLE_TRANSITIONS[state], (
                f"{state} should be able to reset to NOT_STARTED"
            )

    def test_approved_can_re_review(self) -> None:
        """APPROVED can transition to RUNNING (re-review after feedback)."""
        assert ReviewLifecycleState.RUNNING in REVIEW_LIFECYCLE_TRANSITIONS[
            ReviewLifecycleState.APPROVED
        ]

    def test_no_self_transitions(self) -> None:
        """No state should transition to itself."""
        for state, targets in REVIEW_LIFECYCLE_TRANSITIONS.items():
            assert state not in targets, f"{state} should not transition to itself"


# ------------------------------------------------------------------
# Task model integration tests
# ------------------------------------------------------------------


class TestTaskModelLifecycleState:
    """Verify lifecycle state is part of the Task model."""

    def test_task_default_lifecycle_state(self) -> None:
        """New Task has NOT_STARTED lifecycle state by default."""
        task = Task(
            id="test-1",
            project_id="proj",
            local_task_id="T-1",
            title="Test",
            executor_type="code",
        )
        assert task.review_lifecycle_state == ReviewLifecycleState.NOT_STARTED

    def test_task_lifecycle_state_serializable(self) -> None:
        """Lifecycle state serializes to string in model_dump."""
        task = Task(
            id="test-1",
            project_id="proj",
            local_task_id="T-1",
            title="Test",
            executor_type="code",
            review_lifecycle_state=ReviewLifecycleState.APPROVED,
        )
        data = task.model_dump(mode="json")
        assert data["review_lifecycle_state"] == "approved"


# ------------------------------------------------------------------
# DB persistence tests (TaskRow)
# ------------------------------------------------------------------


class TestTaskRowLifecycleState:
    """Verify lifecycle state is persisted on TaskRow."""

    @pytest.mark.asyncio
    async def test_taskrow_default_lifecycle_state(self, session_factory) -> None:
        """New TaskRow has 'not_started' lifecycle state by default."""
        async with get_session(session_factory) as session:
            row = TaskRow(
                id="test-1",
                project_id="proj",
                local_task_id="T-1",
                title="Test",
                description="",
                status="backlog",
                executor_type="code",
                depends_on_json="[]",
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            )
            session.add(row)

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "test-1")
            assert row is not None
            assert row.review_lifecycle_state == "not_started"

    @pytest.mark.asyncio
    async def test_taskrow_lifecycle_state_persists(self, session_factory) -> None:
        """Setting lifecycle state persists across sessions."""
        async with get_session(session_factory) as session:
            row = TaskRow(
                id="test-2",
                project_id="proj",
                local_task_id="T-2",
                title="Test",
                description="",
                status="backlog",
                executor_type="code",
                depends_on_json="[]",
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
                review_lifecycle_state="approved",
            )
            session.add(row)

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "test-2")
            assert row is not None
            assert row.review_lifecycle_state == "approved"


# ------------------------------------------------------------------
# DB persistence tests (ReviewHistoryRow)
# ------------------------------------------------------------------


class TestReviewHistoryRowLifecycleState:
    """Verify lifecycle state is persisted on ReviewHistoryRow."""

    @pytest.mark.asyncio
    async def test_review_history_default_lifecycle_state(self, session_factory) -> None:
        """New ReviewHistoryRow has 'not_started' lifecycle state by default."""
        async with get_session(session_factory) as session:
            row = ReviewHistoryRow(
                task_id="test-1",
                round_number=1,
                reviewer_model="claude-sonnet-4-5",
                reviewer_focus="feasibility",
                verdict="approve",
                summary="Looks good",
                suggestions_json="[]",
                timestamp=datetime.now(UTC).isoformat(),
            )
            session.add(row)

        async with get_session(session_factory) as session:
            from sqlalchemy import select
            stmt = select(ReviewHistoryRow).where(
                ReviewHistoryRow.task_id == "test-1",
            )
            result = await session.execute(stmt)
            row = result.scalar_one()
            assert row.lifecycle_state == "not_started"

    @pytest.mark.asyncio
    async def test_review_history_lifecycle_state_persists(self, session_factory) -> None:
        """Setting lifecycle state on ReviewHistoryRow persists."""
        async with get_session(session_factory) as session:
            row = ReviewHistoryRow(
                task_id="test-2",
                round_number=1,
                reviewer_model="claude-sonnet-4-5",
                reviewer_focus="feasibility",
                verdict="reject",
                summary="Issues found",
                suggestions_json="[]",
                lifecycle_state="rejected_single",
                timestamp=datetime.now(UTC).isoformat(),
            )
            session.add(row)

        async with get_session(session_factory) as session:
            from sqlalchemy import select
            stmt = select(ReviewHistoryRow).where(
                ReviewHistoryRow.task_id == "test-2",
            )
            result = await session.execute(stmt)
            row = result.scalar_one()
            assert row.lifecycle_state == "rejected_single"


# ------------------------------------------------------------------
# TaskManager.set_review_lifecycle_state tests
# ------------------------------------------------------------------


class TestTaskManagerSetLifecycleState:
    """Verify TaskManager.set_review_lifecycle_state persists correctly."""

    @pytest.mark.asyncio
    async def test_set_lifecycle_state(self, session_factory) -> None:
        """set_review_lifecycle_state updates the task row."""
        tm = TaskManager(session_factory)
        await tm.create_task(Task(
            id="tm-1",
            project_id="proj",
            local_task_id="T-1",
            title="Test task",
            executor_type="code",
        ))

        await tm.set_review_lifecycle_state("tm-1", ReviewLifecycleState.RUNNING)
        fetched = await tm.get_task("tm-1")
        assert fetched is not None
        assert fetched.review_lifecycle_state == ReviewLifecycleState.RUNNING

    @pytest.mark.asyncio
    async def test_set_lifecycle_state_updates_timestamp(self, session_factory) -> None:
        """set_review_lifecycle_state updates updated_at."""
        tm = TaskManager(session_factory)
        task = await tm.create_task(Task(
            id="tm-2",
            project_id="proj",
            local_task_id="T-2",
            title="Test task",
            executor_type="code",
        ))
        original_updated = task.updated_at

        await tm.set_review_lifecycle_state("tm-2", ReviewLifecycleState.APPROVED)
        fetched = await tm.get_task("tm-2")
        assert fetched is not None
        assert fetched.updated_at >= original_updated

    @pytest.mark.asyncio
    async def test_set_lifecycle_state_not_found(self, session_factory) -> None:
        """set_review_lifecycle_state raises ValueError for missing task."""
        tm = TaskManager(session_factory)
        with pytest.raises(ValueError, match="Task not found"):
            await tm.set_review_lifecycle_state(
                "nonexistent", ReviewLifecycleState.RUNNING,
            )

    @pytest.mark.asyncio
    async def test_set_lifecycle_state_soft_deleted(self, session_factory) -> None:
        """set_review_lifecycle_state raises ValueError for soft-deleted task."""
        tm = TaskManager(session_factory)
        await tm.create_task(Task(
            id="tm-3",
            project_id="proj",
            local_task_id="T-3",
            title="Test task",
            executor_type="code",
        ))
        await tm.delete_task("tm-3")

        with pytest.raises(ValueError, match="Task not found"):
            await tm.set_review_lifecycle_state(
                "tm-3", ReviewLifecycleState.RUNNING,
            )


# ------------------------------------------------------------------
# HistoryWriter lifecycle_state tests
# ------------------------------------------------------------------


class TestHistoryWriterLifecycleState:
    """Verify HistoryWriter passes lifecycle_state through correctly."""

    @pytest.mark.asyncio
    async def test_write_review_with_lifecycle_state(self, session_factory) -> None:
        """write_review persists lifecycle_state on the row."""
        hw = HistoryWriter(session_factory)
        review = LLMReview(
            model="claude-sonnet-4-5",
            focus="feasibility",
            verdict="approve",
            summary="LGTM",
            suggestions=[],
            timestamp=datetime.now(UTC),
        )

        await hw.write_review(
            task_id="hw-1",
            round_number=1,
            review=review,
            lifecycle_state=ReviewLifecycleState.APPROVED,
        )

        entries = await hw.get_reviews("hw-1")
        assert len(entries) == 1
        assert entries[0]["lifecycle_state"] == "approved"

    @pytest.mark.asyncio
    async def test_write_review_default_lifecycle_state(self, session_factory) -> None:
        """write_review defaults lifecycle_state to NOT_STARTED."""
        hw = HistoryWriter(session_factory)
        review = LLMReview(
            model="claude-sonnet-4-5",
            focus="feasibility",
            verdict="reject",
            summary="Issues",
            suggestions=["fix it"],
            timestamp=datetime.now(UTC),
        )

        await hw.write_review(
            task_id="hw-2",
            round_number=1,
            review=review,
        )

        entries = await hw.get_reviews("hw-2")
        assert len(entries) == 1
        assert entries[0]["lifecycle_state"] == "not_started"

    @pytest.mark.asyncio
    async def test_get_reviews_includes_lifecycle_state(self, session_factory) -> None:
        """get_reviews returns lifecycle_state in each entry dict."""
        hw = HistoryWriter(session_factory)
        review = LLMReview(
            model="claude-sonnet-4-5",
            focus="adversarial",
            verdict="reject",
            summary="Security issues",
            suggestions=["fix XSS"],
            timestamp=datetime.now(UTC),
        )

        await hw.write_review(
            task_id="hw-3",
            round_number=1,
            review=review,
            lifecycle_state=ReviewLifecycleState.REJECTED_SINGLE,
        )

        entries = await hw.get_reviews("hw-3")
        assert "lifecycle_state" in entries[0]
        assert entries[0]["lifecycle_state"] == "rejected_single"


# ------------------------------------------------------------------
# Invariant: NOT_STARTED hides consensus/verdict/cost
# ------------------------------------------------------------------


class TestNotStartedInvariant:
    """When lifecycle_state is NOT_STARTED, no meaningful review data exists."""

    def test_not_started_is_default(self) -> None:
        """A new task starts with NOT_STARTED and no review data."""
        task = Task(
            id="inv-1",
            project_id="proj",
            local_task_id="T-1",
            title="Test",
            executor_type="code",
        )
        assert task.review_lifecycle_state == ReviewLifecycleState.NOT_STARTED
        assert task.review is None
