"""Tests for src.task_generator -- deterministic proposal-to-tasks.db pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.enrichment import ProposedTask
from src.task_generator import (
    AllocatedTask,
    _allocate_ids,
    _build_full_task_block,
    _generate_diff,
    _resolve_dependencies,
    _validate_proposals,
    extract_proposals_from_plan,
    process_proposals,
)
from src.task_generator import (
    _detect_cycles_in_allocated as _detect_cycles,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proposal(
    title: str = "Test task",
    description: str = "A test task description",
    priority: str = "P1",
    complexity: str = "M",
    dependencies: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> ProposedTask:
    """Create a ProposedTask for testing."""
    return ProposedTask(
        title=title,
        description=description,
        suggested_priority=priority,
        suggested_complexity=complexity,
        dependencies=dependencies or [],
        acceptance_criteria=acceptance_criteria or [],
    )


def _make_mock_bridge(
    existing_ids: set[str] | None = None,
    id_counter: int = 1,
) -> MagicMock:
    """Create a mock TaskStoreBridge for testing.

    Args:
        existing_ids: Set of existing task IDs to return from get_all_task_ids.
        id_counter: Starting counter for generate_next_task_id.
    """
    bridge = MagicMock()
    bridge.get_all_task_ids.return_value = existing_ids or set()

    # Track ID generation to return sequential IDs
    counter = {"value": id_counter}

    def gen_id(priority: str) -> str:
        task_id = f"T-{priority}-{counter['value']}"
        counter["value"] += 1
        return task_id

    bridge.generate_next_task_id.side_effect = gen_id
    return bridge


# ---------------------------------------------------------------------------
# _validate_proposals
# ---------------------------------------------------------------------------


class TestValidateProposals:
    """Tests for proposal validation."""

    def test_valid_proposals(self) -> None:
        """Valid proposals return None."""
        proposals = [_make_proposal(title="Task A"), _make_proposal(title="Task B")]
        assert _validate_proposals(proposals) is None

    def test_empty_list_valid(self) -> None:
        """Empty list is valid."""
        assert _validate_proposals([]) is None

    def test_too_many_proposals(self) -> None:
        """More than MAX_TASKS_PER_PLAN (10) proposals rejected."""
        proposals = [_make_proposal(title=f"Task {i}") for i in range(11)]
        error = _validate_proposals(proposals)
        assert error is not None
        assert "Too many" in error
        assert "11" in error

    def test_exactly_max_valid(self) -> None:
        """Exactly MAX_TASKS_PER_PLAN (10) is valid."""
        proposals = [_make_proposal(title=f"Task {i}") for i in range(10)]
        assert _validate_proposals(proposals) is None

    def test_empty_title_rejected(self) -> None:
        """Empty title rejected."""
        proposals = [_make_proposal(title="  ")]
        error = _validate_proposals(proposals)
        assert error is not None
        assert "empty title" in error

    def test_empty_description_rejected(self) -> None:
        """Empty description rejected."""
        proposals = [_make_proposal(description="  ")]
        error = _validate_proposals(proposals)
        assert error is not None
        assert "empty description" in error


# ---------------------------------------------------------------------------
# _allocate_ids
# ---------------------------------------------------------------------------


class TestAllocateIds:
    """Tests for ID allocation via bridge."""

    def test_allocates_sequential_ids(self) -> None:
        """IDs are sequential within priority level."""
        proposals = [
            _make_proposal(title="A", priority="P1"),
            _make_proposal(title="B", priority="P1"),
        ]
        bridge = _make_mock_bridge(id_counter=11)
        ids, title_map = _allocate_ids(proposals, bridge)
        assert ids == ["T-P1-11", "T-P1-12"]
        assert title_map["A"] == "T-P1-11"
        assert title_map["B"] == "T-P1-12"

    def test_allocates_across_priorities(self) -> None:
        """IDs allocated correctly across different priorities."""
        proposals = [
            _make_proposal(title="A", priority="P0"),
            _make_proposal(title="B", priority="P1"),
            _make_proposal(title="C", priority="P2"),
        ]
        bridge = _make_mock_bridge(id_counter=2)
        ids, _ = _allocate_ids(proposals, bridge)
        assert ids[0] == "T-P0-2"
        assert ids[1] == "T-P1-3"
        assert ids[2] == "T-P2-4"

    def test_no_collisions_within_batch(self) -> None:
        """Multiple tasks at same priority don't collide."""
        proposals = [
            _make_proposal(title="A", priority="P0"),
            _make_proposal(title="B", priority="P0"),
            _make_proposal(title="C", priority="P0"),
        ]
        bridge = _make_mock_bridge(id_counter=2)
        ids, _ = _allocate_ids(proposals, bridge)
        assert len(set(ids)) == 3  # all unique


# ---------------------------------------------------------------------------
# _resolve_dependencies
# ---------------------------------------------------------------------------


class TestResolveDependencies:
    """Tests for dependency resolution."""

    def test_existing_id_reference(self) -> None:
        """Reference to existing task ID resolves."""
        proposals = [_make_proposal(dependencies=["T-P0-1"])]
        existing = {"T-P0-1", "T-P1-10"}
        resolved, error = _resolve_dependencies(proposals, existing, {})
        assert error is None
        assert resolved == [["T-P0-1"]]

    def test_title_reference(self) -> None:
        """Reference to another proposal by title resolves."""
        proposals = [
            _make_proposal(title="Task A"),
            _make_proposal(title="Task B", dependencies=["Task A"]),
        ]
        title_to_id = {"Task A": "T-P1-11", "Task B": "T-P1-12"}
        resolved, error = _resolve_dependencies(proposals, set(), title_to_id)
        assert error is None
        assert resolved[0] == []
        assert resolved[1] == ["T-P1-11"]

    def test_invalid_id_reference_rejected(self) -> None:
        """Reference to non-existent task ID rejected."""
        proposals = [_make_proposal(dependencies=["T-P0-99"])]
        existing = {"T-P0-1"}
        _, error = _resolve_dependencies(proposals, existing, {})
        assert error is not None
        assert "non-existent" in error

    def test_invalid_title_reference_rejected(self) -> None:
        """Reference to non-existent title rejected."""
        proposals = [_make_proposal(dependencies=["Unknown Task"])]
        _, error = _resolve_dependencies(proposals, set(), {})
        assert error is not None
        assert "neither a valid task ID" in error

    def test_empty_dependencies_ok(self) -> None:
        """Task with no dependencies resolves."""
        proposals = [_make_proposal(dependencies=[])]
        resolved, error = _resolve_dependencies(proposals, set(), {})
        assert error is None
        assert resolved == [[]]

    def test_blank_dependency_skipped(self) -> None:
        """Blank/empty dependency strings are skipped."""
        proposals = [_make_proposal(dependencies=["", "  "])]
        resolved, error = _resolve_dependencies(proposals, set(), {})
        assert error is None
        assert resolved == [[]]


# ---------------------------------------------------------------------------
# _detect_cycles
# ---------------------------------------------------------------------------


class TestDetectCycles:
    """Tests for cycle detection."""

    def test_no_cycle(self) -> None:
        """Linear dependency chain has no cycle."""
        tasks = [
            AllocatedTask(
                task_id="T-P1-1", title="A", description="d",
                priority="P1", complexity="M", depends_on=[],
                acceptance_criteria=[], parent_task_id="T-P0-1",
            ),
            AllocatedTask(
                task_id="T-P1-2", title="B", description="d",
                priority="P1", complexity="M", depends_on=["T-P1-1"],
                acceptance_criteria=[], parent_task_id="T-P0-1",
            ),
            AllocatedTask(
                task_id="T-P1-3", title="C", description="d",
                priority="P1", complexity="M", depends_on=["T-P1-2"],
                acceptance_criteria=[], parent_task_id="T-P0-1",
            ),
        ]
        assert _detect_cycles(tasks) is None

    def test_simple_cycle(self) -> None:
        """A -> B -> A cycle detected."""
        tasks = [
            AllocatedTask(
                task_id="T-P1-1", title="A", description="d",
                priority="P1", complexity="M", depends_on=["T-P1-2"],
                acceptance_criteria=[], parent_task_id="T-P0-1",
            ),
            AllocatedTask(
                task_id="T-P1-2", title="B", description="d",
                priority="P1", complexity="M", depends_on=["T-P1-1"],
                acceptance_criteria=[], parent_task_id="T-P0-1",
            ),
        ]
        error = _detect_cycles(tasks)
        assert error is not None
        assert "Circular dependency" in error

    def test_self_cycle(self) -> None:
        """Self-referencing dependency detected."""
        tasks = [
            AllocatedTask(
                task_id="T-P1-1", title="A", description="d",
                priority="P1", complexity="M", depends_on=["T-P1-1"],
                acceptance_criteria=[], parent_task_id="T-P0-1",
            ),
        ]
        error = _detect_cycles(tasks)
        assert error is not None
        assert "Circular dependency" in error

    def test_external_deps_ignored(self) -> None:
        """Dependencies on external (non-allocated) tasks are ignored."""
        tasks = [
            AllocatedTask(
                task_id="T-P1-1", title="A", description="d",
                priority="P1", complexity="M", depends_on=["T-P0-99"],
                acceptance_criteria=[], parent_task_id="T-P0-1",
            ),
        ]
        assert _detect_cycles(tasks) is None


# ---------------------------------------------------------------------------
# _build_full_task_block
# ---------------------------------------------------------------------------


class TestBuildFullTaskBlock:
    """Tests for task block formatting."""

    def test_basic_block(self) -> None:
        """Block includes all metadata fields."""
        task = AllocatedTask(
            task_id="T-P1-5", title="My Task", description="Do the thing",
            priority="P1", complexity="M", depends_on=["T-P0-1"],
            acceptance_criteria=["It works", "Tests pass"],
            parent_task_id="T-P0-1",
        )
        block = _build_full_task_block(task)
        assert "#### T-P1-5: My Task" in block
        assert "- **Priority**: P1" in block
        assert "- **Complexity**: M (1-2 sessions)" in block
        assert "- **Depends on**: T-P0-1" in block
        assert "- **Description**: Do the thing" in block
        assert "  1. It works" in block
        assert "  2. Tests pass" in block

    def test_no_deps_shows_none(self) -> None:
        """No dependencies shows 'None'."""
        task = AllocatedTask(
            task_id="T-P1-5", title="My Task", description="Do it",
            priority="P1", complexity="S", depends_on=[],
            acceptance_criteria=[], parent_task_id="T-P0-1",
        )
        block = _build_full_task_block(task)
        assert "- **Depends on**: None" in block

    def test_no_acceptance_criteria(self) -> None:
        """No ACs means no AC section in block."""
        task = AllocatedTask(
            task_id="T-P1-5", title="My Task", description="Do it",
            priority="P1", complexity="S", depends_on=[],
            acceptance_criteria=[], parent_task_id="T-P0-1",
        )
        block = _build_full_task_block(task)
        assert "Acceptance Criteria" not in block


# ---------------------------------------------------------------------------
# _generate_diff
# ---------------------------------------------------------------------------


class TestGenerateDiff:
    """Tests for diff generation."""

    def test_diff_includes_all_tasks(self) -> None:
        """Diff shows all allocated tasks with + prefix."""
        tasks = [
            AllocatedTask(
                task_id="T-P1-11", title="Task A", description="Do A",
                priority="P1", complexity="M", depends_on=[],
                acceptance_criteria=["AC1"], parent_task_id="T-P0-1",
            ),
            AllocatedTask(
                task_id="T-P1-12", title="Task B", description="Do B",
                priority="P1", complexity="S", depends_on=["T-P1-11"],
                acceptance_criteria=[], parent_task_id="T-P0-1",
            ),
        ]
        diff = _generate_diff(tasks, "T-P0-1")
        assert "2 tasks from T-P0-1" in diff
        assert "+ #### T-P1-11: Task A" in diff
        assert "+ #### T-P1-12: Task B" in diff
        assert "Dependency graph additions:" in diff
        assert "T-P1-11 depends on None" in diff
        assert "T-P1-12 depends on T-P1-11" in diff


# ---------------------------------------------------------------------------
# process_proposals (integration with mock bridge)
# ---------------------------------------------------------------------------


class TestProcessProposals:
    """Integration tests for the full pipeline."""

    def test_happy_path(self) -> None:
        """Full pipeline succeeds with valid proposals."""
        proposals = [
            _make_proposal(
                title="Add feature X",
                description="Implement feature X for module Y",
                priority="P1",
                acceptance_criteria=["Feature X works", "Tests pass"],
            ),
            _make_proposal(
                title="Test feature X",
                description="Add tests for feature X",
                priority="P1",
                dependencies=["Add feature X"],
                acceptance_criteria=["All tests green"],
            ),
        ]
        bridge = _make_mock_bridge(existing_ids={"T-P0-1", "T-P1-10"}, id_counter=11)
        result = process_proposals(proposals, bridge, "T-P0-1")
        assert result.success
        assert len(result.allocated_tasks) == 2
        assert result.allocated_tasks[0].task_id == "T-P1-11"
        assert result.allocated_tasks[1].task_id == "T-P1-12"
        assert result.allocated_tasks[1].depends_on == ["T-P1-11"]
        assert result.diff_text  # non-empty

    def test_empty_proposals(self) -> None:
        """Empty proposals list returns success with no tasks."""
        bridge = _make_mock_bridge()
        result = process_proposals([], bridge, "T-P0-1")
        assert result.success
        assert result.allocated_tasks == []

    def test_too_many_rejected(self) -> None:
        """More than 10 proposals rejected."""
        proposals = [_make_proposal(title=f"Task {i}") for i in range(11)]
        bridge = _make_mock_bridge()
        result = process_proposals(proposals, bridge, "T-P0-1")
        assert not result.success
        assert "Too many" in (result.error or "")

    def test_cycle_rejected(self) -> None:
        """Circular dependencies rejected."""
        proposals = [
            _make_proposal(title="Task A", dependencies=["Task B"]),
            _make_proposal(title="Task B", dependencies=["Task A"]),
        ]
        bridge = _make_mock_bridge(id_counter=11)
        result = process_proposals(proposals, bridge, "T-P0-1")
        assert not result.success
        assert "Circular" in (result.error or "")

    def test_invalid_dep_rejected(self) -> None:
        """Reference to non-existent task rejected."""
        proposals = [
            _make_proposal(title="Task A", dependencies=["T-P0-99"]),
        ]
        bridge = _make_mock_bridge(existing_ids={"T-P0-1"}, id_counter=11)
        result = process_proposals(proposals, bridge, "T-P0-1")
        assert not result.success
        assert "non-existent" in (result.error or "")

    def test_dep_on_existing_task(self) -> None:
        """Dependency on existing task in tasks.db works."""
        proposals = [
            _make_proposal(title="Task A", dependencies=["T-P0-1"]),
        ]
        bridge = _make_mock_bridge(existing_ids={"T-P0-1", "T-P1-10"}, id_counter=11)
        result = process_proposals(proposals, bridge, "T-P0-1")
        assert result.success
        assert result.allocated_tasks[0].depends_on == ["T-P0-1"]

    def test_mixed_priorities(self) -> None:
        """Proposals with different priorities get correct IDs."""
        proposals = [
            _make_proposal(title="P0 task", priority="P0"),
            _make_proposal(title="P2 task", priority="P2"),
        ]
        bridge = _make_mock_bridge(existing_ids={"T-P0-1", "T-P1-10"}, id_counter=2)
        result = process_proposals(proposals, bridge, "T-P0-1")
        assert result.success
        assert result.allocated_tasks[0].task_id == "T-P0-2"
        assert result.allocated_tasks[1].task_id == "T-P2-3"


# ---------------------------------------------------------------------------
# extract_proposals_from_plan
# ---------------------------------------------------------------------------


class TestExtractProposalsFromPlan:
    """Tests for plan JSON extraction."""

    def test_extracts_proposals(self) -> None:
        """Proposals extracted from valid plan_json."""
        plan_json = '{"plan": "test", "steps": [], "acceptance_criteria": [], "proposed_tasks": [{"title": "Task A", "description": "Do A"}]}'
        proposals = extract_proposals_from_plan(plan_json)
        assert len(proposals) == 1
        assert proposals[0].title == "Task A"

    def test_no_proposals(self) -> None:
        """Empty proposed_tasks returns empty list."""
        plan_json = '{"plan": "test", "steps": [], "acceptance_criteria": [], "proposed_tasks": []}'
        assert extract_proposals_from_plan(plan_json) == []

    def test_none_plan_json(self) -> None:
        """None plan_json returns empty list."""
        assert extract_proposals_from_plan(None) == []

    def test_invalid_json(self) -> None:
        """Invalid JSON returns empty list."""
        assert extract_proposals_from_plan("{broken") == []

    def test_missing_proposed_tasks_key(self) -> None:
        """Missing proposed_tasks key returns empty list."""
        plan_json = '{"plan": "test", "steps": []}'
        assert extract_proposals_from_plan(plan_json) == []

    def test_invalid_proposal_skipped(self) -> None:
        """Invalid individual proposals are skipped."""
        plan_json = '{"proposed_tasks": [{"title": "Good", "description": "ok"}, {"bad": true}]}'
        proposals = extract_proposals_from_plan(plan_json)
        assert len(proposals) == 1
        assert proposals[0].title == "Good"
