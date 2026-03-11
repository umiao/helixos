"""Tests for plan state machine (set_plan_state + VALID_PLAN_TRANSITIONS).

Covers AC9 from T-P0-134:
- Every valid transition
- Every invalid transition (raises ValueError)
- Field invariants for each state
- generation_id mismatch discard
"""

from __future__ import annotations

import json

import pytest

from src.models import PlanStatus
from src.task_manager import VALID_PLAN_TRANSITIONS, TaskManager
from tests.factories import make_task

PLAN_JSON_WITH_PROPOSALS = json.dumps({
    "steps": [{"title": "Step 1", "description": "Do stuff"}],
    "proposed_tasks": [
        {"title": "Sub-task 1", "description": "Subtask desc"},
    ],
})

PLAN_JSON_NO_PROPOSALS = json.dumps({
    "steps": [{"title": "Step 1", "description": "Do stuff"}],
    "proposed_tasks": [],
})


# ---------------------------------------------------------------------------
# Transition map tests
# ---------------------------------------------------------------------------


class TestValidPlanTransitions:
    """Verify VALID_PLAN_TRANSITIONS covers all expected transitions."""

    def test_none_can_go_to_generating(self) -> None:
        """NONE -> GENERATING is valid."""
        assert PlanStatus.GENERATING in VALID_PLAN_TRANSITIONS[PlanStatus.NONE]

    def test_none_cannot_go_to_ready(self) -> None:
        """NONE -> READY is invalid."""
        assert PlanStatus.READY not in VALID_PLAN_TRANSITIONS[PlanStatus.NONE]

    def test_generating_targets(self) -> None:
        """GENERATING can go to READY, FAILED, or NONE."""
        targets = VALID_PLAN_TRANSITIONS[PlanStatus.GENERATING]
        assert targets == {PlanStatus.READY, PlanStatus.FAILED, PlanStatus.NONE}

    def test_ready_targets(self) -> None:
        """READY can go to GENERATING, DECOMPOSED, or NONE."""
        targets = VALID_PLAN_TRANSITIONS[PlanStatus.READY]
        assert targets == {PlanStatus.GENERATING, PlanStatus.DECOMPOSED, PlanStatus.NONE}

    def test_failed_targets(self) -> None:
        """FAILED can go to GENERATING or NONE."""
        targets = VALID_PLAN_TRANSITIONS[PlanStatus.FAILED]
        assert targets == {PlanStatus.GENERATING, PlanStatus.NONE}

    def test_decomposed_targets(self) -> None:
        """DECOMPOSED can go to GENERATING or NONE."""
        targets = VALID_PLAN_TRANSITIONS[PlanStatus.DECOMPOSED]
        assert targets == {PlanStatus.GENERATING, PlanStatus.NONE}

    def test_all_states_have_entries(self) -> None:
        """Every PlanStatus value has an entry in the transition map."""
        for status in PlanStatus:
            assert status in VALID_PLAN_TRANSITIONS


# ---------------------------------------------------------------------------
# set_plan_state valid transitions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestSetPlanStateValid:
    """Test all valid plan state transitions via set_plan_state."""

    async def test_none_to_generating(self, session_factory) -> None:
        """NONE -> GENERATING: preserves description, sets generation_id."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(description="old desc"))

        await tm.set_plan_state(
            "P0:T-P0-1", "generating", plan_generation_id="gen-1",
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "generating"
        assert task.plan_generation_id == "gen-1"
        assert task.description == "old desc"  # Preserved during generation
        assert task.plan_json is None
        assert task.has_proposed_tasks is False

    async def test_generating_to_ready_with_proposals(self, session_factory) -> None:
        """GENERATING -> READY: sets plan data, computes has_proposed_tasks=True."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))

        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            plan_generation_id="gen-1",
            description="Plan description text here.",
            plan_json=PLAN_JSON_WITH_PROPOSALS,
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "ready"
        assert task.has_proposed_tasks is True
        assert task.description == "Plan description text here."
        assert task.plan_json == PLAN_JSON_WITH_PROPOSALS
        assert task.plan_generation_id == "gen-1"

    async def test_generating_to_ready_no_proposals(self, session_factory) -> None:
        """GENERATING -> READY: has_proposed_tasks=False when no proposals."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))

        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Some plan text for display.",
            plan_json=PLAN_JSON_NO_PROPOSALS,
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.has_proposed_tasks is False

    async def test_generating_to_failed(self, session_factory) -> None:
        """GENERATING -> FAILED: clears plan_json, preserves description."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))

        await tm.set_plan_state(
            "P0:T-P0-1", "failed", description="Error context here",
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "failed"
        assert task.plan_json is None
        assert task.description == "Error context here"

    async def test_generating_to_none(self, session_factory) -> None:
        """GENERATING -> NONE: clears everything."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(
            plan_status="generating",
            plan_generation_id="old-gen",
        ))

        await tm.set_plan_state("P0:T-P0-1", "none")

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "none"
        assert task.plan_json is None
        assert task.description == ""
        assert task.has_proposed_tasks is False
        assert task.plan_generation_id is None

    async def test_ready_to_decomposed(self, session_factory) -> None:
        """READY -> DECOMPOSED: preserves all fields."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Plan with proposed tasks.",
            plan_json=PLAN_JSON_WITH_PROPOSALS,
            plan_generation_id="gen-1",
        )

        await tm.set_plan_state("P0:T-P0-1", "decomposed")

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "decomposed"
        assert task.has_proposed_tasks is True
        assert task.plan_json == PLAN_JSON_WITH_PROPOSALS
        assert task.description == "Plan with proposed tasks."

    async def test_ready_to_none(self, session_factory) -> None:
        """READY -> NONE: clears everything."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Plan text for testing.",
            plan_json=PLAN_JSON_WITH_PROPOSALS,
        )

        await tm.set_plan_state("P0:T-P0-1", "none")

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "none"
        assert task.plan_json is None
        assert task.description == ""
        assert task.has_proposed_tasks is False

    async def test_ready_to_generating(self, session_factory) -> None:
        """READY -> GENERATING: regeneration preserves description, clears plan_json."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Old plan text for display.",
            plan_json=PLAN_JSON_WITH_PROPOSALS,
            plan_generation_id="gen-1",
        )

        await tm.set_plan_state(
            "P0:T-P0-1", "generating", plan_generation_id="gen-2",
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "generating"
        assert task.plan_generation_id == "gen-2"
        assert task.plan_json is None
        # Description is preserved so UI can show old summary during regeneration (T-P0-166)
        assert task.description == "Old plan text for display."

    async def test_failed_to_generating(self, session_factory) -> None:
        """FAILED -> GENERATING: retry clears and sets new generation_id."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state("P0:T-P0-1", "failed")

        await tm.set_plan_state(
            "P0:T-P0-1", "generating", plan_generation_id="gen-retry",
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "generating"
        assert task.plan_generation_id == "gen-retry"

    async def test_failed_to_none(self, session_factory) -> None:
        """FAILED -> NONE: reset after failure."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state("P0:T-P0-1", "failed")

        await tm.set_plan_state("P0:T-P0-1", "none")

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "none"

    async def test_decomposed_to_generating(self, session_factory) -> None:
        """DECOMPOSED -> GENERATING: re-generate after decomposition."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Plan ready for decompose.",
            plan_json=PLAN_JSON_WITH_PROPOSALS,
        )
        await tm.set_plan_state("P0:T-P0-1", "decomposed")

        await tm.set_plan_state(
            "P0:T-P0-1", "generating", plan_generation_id="gen-new",
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "generating"

    async def test_decomposed_to_none(self, session_factory) -> None:
        """DECOMPOSED -> NONE: reset after decomposition."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Plan ready for decompose.",
            plan_json=PLAN_JSON_NO_PROPOSALS,
        )
        await tm.set_plan_state("P0:T-P0-1", "decomposed")

        await tm.set_plan_state("P0:T-P0-1", "none")

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "none"


# ---------------------------------------------------------------------------
# set_plan_state invalid transitions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestSetPlanStateInvalid:
    """Test that invalid transitions raise ValueError."""

    async def test_none_to_ready_raises(self, session_factory) -> None:
        """NONE -> READY is invalid (must go through GENERATING)."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task())

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state(
                "P0:T-P0-1", "ready",
                description="plan text", plan_json="{}",
            )

    async def test_none_to_failed_raises(self, session_factory) -> None:
        """NONE -> FAILED is invalid."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task())

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state("P0:T-P0-1", "failed")

    async def test_none_to_decomposed_raises(self, session_factory) -> None:
        """NONE -> DECOMPOSED is invalid."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task())

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state("P0:T-P0-1", "decomposed")

    async def test_ready_to_failed_raises(self, session_factory) -> None:
        """READY -> FAILED is invalid (must go through GENERATING)."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Ready plan text for test.",
            plan_json=PLAN_JSON_NO_PROPOSALS,
        )

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state("P0:T-P0-1", "failed")

    async def test_failed_to_ready_raises(self, session_factory) -> None:
        """FAILED -> READY is invalid (must go through GENERATING)."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state("P0:T-P0-1", "failed")

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state(
                "P0:T-P0-1", "ready",
                description="plan text", plan_json="{}",
            )

    async def test_failed_to_decomposed_raises(self, session_factory) -> None:
        """FAILED -> DECOMPOSED is invalid."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state("P0:T-P0-1", "failed")

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state("P0:T-P0-1", "decomposed")

    async def test_decomposed_to_ready_raises(self, session_factory) -> None:
        """DECOMPOSED -> READY is invalid."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Ready plan for decompose.",
            plan_json=PLAN_JSON_NO_PROPOSALS,
        )
        await tm.set_plan_state("P0:T-P0-1", "decomposed")

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state(
                "P0:T-P0-1", "ready",
                description="plan", plan_json="{}",
            )

    async def test_decomposed_to_failed_raises(self, session_factory) -> None:
        """DECOMPOSED -> FAILED is invalid."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Ready plan for decompose.",
            plan_json=PLAN_JSON_NO_PROPOSALS,
        )
        await tm.set_plan_state("P0:T-P0-1", "decomposed")

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state("P0:T-P0-1", "failed")

    async def test_generating_to_decomposed_raises(self, session_factory) -> None:
        """GENERATING -> DECOMPOSED is invalid."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))

        with pytest.raises(ValueError, match="Invalid plan transition"):
            await tm.set_plan_state("P0:T-P0-1", "decomposed")


# ---------------------------------------------------------------------------
# Field invariants
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestSetPlanStateInvariants:
    """Test field invariant enforcement per state."""

    async def test_ready_requires_plan_json(self, session_factory) -> None:
        """READY state requires plan_json -- omitting it raises."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))

        with pytest.raises(ValueError, match="requires plan_json and description"):
            await tm.set_plan_state(
                "P0:T-P0-1", "ready",
                description="Some description here.",
                # plan_json omitted
            )

    async def test_ready_requires_description(self, session_factory) -> None:
        """READY state requires description -- omitting it raises."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))

        with pytest.raises(ValueError, match="requires plan_json and description"):
            await tm.set_plan_state(
                "P0:T-P0-1", "ready",
                plan_json=PLAN_JSON_NO_PROPOSALS,
                # description omitted
            )

    async def test_none_clears_generation_id(self, session_factory) -> None:
        """Transitioning to NONE clears plan_generation_id."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))
        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            plan_generation_id="gen-1",
            description="Plan with generation ID.",
            plan_json=PLAN_JSON_NO_PROPOSALS,
        )

        await tm.set_plan_state("P0:T-P0-1", "none")

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_generation_id is None

    async def test_complexity_override(self, session_factory) -> None:
        """Complexity can be overridden during transition."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))

        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Large plan text description.",
            plan_json=PLAN_JSON_WITH_PROPOSALS,
            complexity="L",
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.complexity == "L"

    async def test_replan_attempt_override(self, session_factory) -> None:
        """Replan attempt counter can be set during transition."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task())

        await tm.set_plan_state(
            "P0:T-P0-1", "generating",
            plan_generation_id="gen-replan",
            replan_attempt=3,
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.replan_attempt == 3

    async def test_has_proposed_tasks_malformed_json(self, session_factory) -> None:
        """Malformed plan_json sets has_proposed_tasks=False (no crash)."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_status="generating"))

        await tm.set_plan_state(
            "P0:T-P0-1", "ready",
            description="Plan with bad JSON data.",
            plan_json="not valid json at all",
        )

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.has_proposed_tasks is False

    async def test_task_not_found_raises(self, session_factory) -> None:
        """set_plan_state raises ValueError for non-existent task."""
        tm = TaskManager(session_factory)

        with pytest.raises(ValueError, match="Task not found"):
            await tm.set_plan_state("nonexistent", "generating")

    async def test_deleted_task_raises(self, session_factory) -> None:
        """set_plan_state raises ValueError for soft-deleted task."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task())
        await tm.delete_task("P0:T-P0-1")

        with pytest.raises(ValueError, match="Task not found"):
            await tm.set_plan_state("P0:T-P0-1", "generating")

    async def test_failed_preserves_description_no_override(
        self, session_factory,
    ) -> None:
        """FAILED preserves existing description if no override provided."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task())
        await tm.set_plan_state(
            "P0:T-P0-1", "generating", plan_generation_id="g1",
        )

        # No description override -- preserves empty string from GENERATING
        await tm.set_plan_state("P0:T-P0-1", "failed")

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_status == "failed"
        # description was cleared by GENERATING -> stays empty
        assert task.description == ""


# ---------------------------------------------------------------------------
# New fields in row_to_dict / dict_to_row_kwargs
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestNewFieldsRoundTrip:
    """Verify plan_generation_id and has_proposed_tasks survive DB round-trip."""

    async def test_generation_id_persists(self, session_factory) -> None:
        """plan_generation_id round-trips through create -> get."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(plan_generation_id="my-gen-id"))

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.plan_generation_id == "my-gen-id"

    async def test_has_proposed_tasks_default(self, session_factory) -> None:
        """has_proposed_tasks defaults to False on new tasks."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task())

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.has_proposed_tasks is False

    async def test_has_proposed_tasks_true_roundtrip(self, session_factory) -> None:
        """has_proposed_tasks=True round-trips through create -> get."""
        tm = TaskManager(session_factory)
        await tm.create_task(make_task(has_proposed_tasks=True))

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.has_proposed_tasks is True
