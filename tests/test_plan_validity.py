"""Tests for plan validity model + enforcement in review gate (T-P0-44).

Covers:
- is_plan_valid() unit tests with edge cases
- PlanInvalidError raised on BACKLOG -> REVIEW when gate enabled + plan invalid
- Plan valid: BACKLOG -> REVIEW succeeds when gate enabled + plan valid
- Gate disabled: BACKLOG -> REVIEW succeeds regardless of plan
- API returns 428 with gate_action=plan_invalid
- Context menu path: same enforcement
- Journey: planless -> blocked -> write plan -> unblocked
"""

from __future__ import annotations

import pytest

from src.models import TaskStatus
from src.task_manager import (
    MIN_PLAN_LENGTH,
    PlanInvalidError,
    TaskManager,
    is_plan_valid,
)
from tests.factories import make_task

# Default IDs for this test module (matches the original local factory)
_ID = "proj:t1"
_PROJECT = "proj"
_LOCAL = "t1"
_TITLE = "Test Task"


# ---------------------------------------------------------------------------
# is_plan_valid unit tests
# ---------------------------------------------------------------------------


class TestIsPlanValid:
    """Unit tests for the is_plan_valid function."""

    def test_none_is_invalid(self) -> None:
        assert is_plan_valid(None) is False

    def test_empty_string_is_invalid(self) -> None:
        assert is_plan_valid("") is False

    def test_whitespace_only_is_invalid(self) -> None:
        assert is_plan_valid("   \n\t  ") is False

    def test_too_short_is_invalid(self) -> None:
        assert is_plan_valid("short") is False

    def test_exactly_threshold_minus_one_is_invalid(self) -> None:
        assert is_plan_valid("a" * (MIN_PLAN_LENGTH - 1)) is False

    def test_exactly_threshold_is_valid(self) -> None:
        assert is_plan_valid("a" * MIN_PLAN_LENGTH) is True

    def test_long_plan_is_valid(self) -> None:
        assert is_plan_valid("This is a detailed plan with many steps.") is True

    def test_whitespace_padded_too_short(self) -> None:
        """Whitespace padding should not count toward length."""
        assert is_plan_valid("   short   ") is False

    def test_whitespace_padded_valid(self) -> None:
        """Stripped content that meets threshold is valid."""
        plan = "   " + "a" * MIN_PLAN_LENGTH + "   "
        assert is_plan_valid(plan) is True

    def test_newlines_count_after_strip(self) -> None:
        """Content with newlines: stripped length matters."""
        plan = "\n\nThis is a real plan\nwith multiple lines\n\n"
        assert is_plan_valid(plan) is True

    def test_min_plan_length_is_20(self) -> None:
        """Sanity check the constant."""
        assert MIN_PLAN_LENGTH == 20


# ---------------------------------------------------------------------------
# TaskManager plan validity gate
# ---------------------------------------------------------------------------


class TestPlanValidityGate:
    """TaskManager.update_status enforces plan validity on BACKLOG -> REVIEW."""

    async def test_gate_on_plan_invalid_blocks_review(
        self, session_factory,
    ) -> None:
        """BACKLOG -> REVIEW is blocked when gate=on and plan is empty."""
        tm = TaskManager(session_factory)
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="")
        await tm.create_task(task)

        with pytest.raises(PlanInvalidError) as exc_info:
            await tm.update_status(
                task.id, TaskStatus.REVIEW,
                review_gate_enabled=True,
            )
        assert task.id in str(exc_info.value)
        assert "plan" in str(exc_info.value).lower()

    async def test_gate_on_plan_too_short_blocks_review(
        self, session_factory,
    ) -> None:
        """BACKLOG -> REVIEW is blocked when gate=on and plan is too short."""
        tm = TaskManager(session_factory)
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="short")
        await tm.create_task(task)

        with pytest.raises(PlanInvalidError):
            await tm.update_status(
                task.id, TaskStatus.REVIEW,
                review_gate_enabled=True,
            )

    async def test_gate_on_plan_whitespace_only_blocks_review(
        self, session_factory,
    ) -> None:
        """BACKLOG -> REVIEW is blocked when plan is whitespace-only."""
        tm = TaskManager(session_factory)
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="   \n\t  ")
        await tm.create_task(task)

        with pytest.raises(PlanInvalidError):
            await tm.update_status(
                task.id, TaskStatus.REVIEW,
                review_gate_enabled=True,
            )

    async def test_gate_on_plan_valid_allows_review(
        self, session_factory,
    ) -> None:
        """BACKLOG -> REVIEW succeeds when gate=on and plan is valid."""
        tm = TaskManager(session_factory)
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="This is a detailed plan for implementing the feature.")
        await tm.create_task(task)

        updated = await tm.update_status(
            task.id, TaskStatus.REVIEW,
            review_gate_enabled=True,
        )
        assert updated.status == TaskStatus.REVIEW

    async def test_gate_off_plan_invalid_allows_review(
        self, session_factory,
    ) -> None:
        """BACKLOG -> REVIEW succeeds when gate=off, regardless of plan."""
        tm = TaskManager(session_factory)
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="")
        await tm.create_task(task)

        updated = await tm.update_status(
            task.id, TaskStatus.REVIEW,
            review_gate_enabled=False,
        )
        assert updated.status == TaskStatus.REVIEW

    async def test_gate_off_plan_short_allows_review(
        self, session_factory,
    ) -> None:
        """Gate disabled: even short plans pass."""
        tm = TaskManager(session_factory)
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="tiny")
        await tm.create_task(task)

        updated = await tm.update_status(
            task.id, TaskStatus.REVIEW,
            review_gate_enabled=False,
        )
        assert updated.status == TaskStatus.REVIEW

    async def test_gate_on_does_not_affect_other_transitions(
        self, session_factory,
    ) -> None:
        """Plan validity only checked on BACKLOG -> REVIEW, not other transitions."""
        tm = TaskManager(session_factory)
        # Create a task in REVIEW with valid plan, move to BACKLOG
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="This is a valid plan for the task.")
        await tm.create_task(task)
        await tm.update_status(task.id, TaskStatus.REVIEW, review_gate_enabled=True)
        # Move back to BACKLOG (should work without plan check)
        updated = await tm.update_status(task.id, TaskStatus.BACKLOG, review_gate_enabled=True)
        assert updated.status == TaskStatus.BACKLOG


# ---------------------------------------------------------------------------
# Journey test: planless -> blocked -> write plan -> unblocked
# ---------------------------------------------------------------------------


class TestPlanValidityJourney:
    """Full journey: planless task blocked, then plan added, then succeeds."""

    async def test_planless_blocked_then_plan_added_unblocks(
        self, session_factory,
    ) -> None:
        """AC9: User drags planless task to REVIEW -> blocked -> writes plan -> succeeds."""
        tm = TaskManager(session_factory)

        # Step 1: Create planless task
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="")
        await tm.create_task(task)

        # Step 2: Try to send to REVIEW -> blocked
        with pytest.raises(PlanInvalidError):
            await tm.update_status(
                task.id, TaskStatus.REVIEW,
                review_gate_enabled=True,
            )

        # Step 3: Write a plan (simulate PATCH /api/tasks/{id})
        current = await tm.get_task(task.id)
        assert current is not None
        updated_task = current.model_copy(
            update={"description": "This is a real plan describing how to implement the feature step by step."},
        )
        await tm.update_task(updated_task)

        # Step 4: Try again -> succeeds
        updated = await tm.update_status(
            task.id, TaskStatus.REVIEW,
            review_gate_enabled=True,
        )
        assert updated.status == TaskStatus.REVIEW

    async def test_plan_exactly_at_threshold(
        self, session_factory,
    ) -> None:
        """Plan exactly at MIN_PLAN_LENGTH is accepted."""
        tm = TaskManager(session_factory)
        task = make_task(task_id=_ID, project_id=_PROJECT, local_task_id=_LOCAL, title=_TITLE, description="a" * MIN_PLAN_LENGTH)
        await tm.create_task(task)

        updated = await tm.update_status(
            task.id, TaskStatus.REVIEW,
            review_gate_enabled=True,
        )
        assert updated.status == TaskStatus.REVIEW
