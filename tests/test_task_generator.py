"""Tests for src.task_generator -- deterministic proposal-to-TASKS.md pipeline."""

from __future__ import annotations

from pathlib import Path

from src.enrichment import ProposedTask
from src.task_generator import (
    AllocatedTask,
    _allocate_ids,
    _build_full_task_block,
    _detect_cycles,
    _generate_diff,
    _resolve_dependencies,
    _scan_existing_task_ids,
    _validate_proposals,
    extract_proposals_from_plan,
    process_proposals,
    write_allocated_tasks,
)
from src.tasks_writer import TasksWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_TASKS_MD = """\
# Task Backlog

## Active Tasks

### P0 -- Must Have

#### T-P0-1: Existing task one
- **Priority**: P0
- **Description**: Already exists

### P1 -- Should Have

#### T-P1-10: Existing task two
- **Priority**: P1
- **Description**: Another existing task

## Completed Tasks
"""


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
        """More than MAX_TASKS_PER_PLAN proposals rejected."""
        proposals = [_make_proposal(title=f"Task {i}") for i in range(9)]
        error = _validate_proposals(proposals)
        assert error is not None
        assert "Too many" in error
        assert "9" in error

    def test_exactly_max_valid(self) -> None:
        """Exactly MAX_TASKS_PER_PLAN is valid."""
        proposals = [_make_proposal(title=f"Task {i}") for i in range(8)]
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
# _scan_existing_task_ids
# ---------------------------------------------------------------------------


class TestScanExistingIds:
    """Tests for existing task ID scanning."""

    def test_finds_all_ids(self) -> None:
        """All task IDs found in content."""
        ids = _scan_existing_task_ids(MINIMAL_TASKS_MD)
        assert "T-P0-1" in ids
        assert "T-P1-10" in ids

    def test_empty_content(self) -> None:
        """Empty content returns empty set."""
        assert _scan_existing_task_ids("") == set()


# ---------------------------------------------------------------------------
# _allocate_ids
# ---------------------------------------------------------------------------


class TestAllocateIds:
    """Tests for ID allocation."""

    def test_allocates_sequential_ids(self) -> None:
        """IDs are sequential within priority level."""
        proposals = [
            _make_proposal(title="A", priority="P1"),
            _make_proposal(title="B", priority="P1"),
        ]
        ids, title_map = _allocate_ids(proposals, MINIMAL_TASKS_MD)
        # T-P1-10 exists, so next should be T-P1-11, then T-P1-12
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
        ids, _ = _allocate_ids(proposals, MINIMAL_TASKS_MD)
        assert ids[0] == "T-P0-2"   # after T-P0-1
        assert ids[1] == "T-P1-11"  # after T-P1-10
        assert ids[2] == "T-P2-0"   # first P2

    def test_no_collisions_within_batch(self) -> None:
        """Multiple tasks at same priority don't collide."""
        proposals = [
            _make_proposal(title="A", priority="P0"),
            _make_proposal(title="B", priority="P0"),
            _make_proposal(title="C", priority="P0"),
        ]
        ids, _ = _allocate_ids(proposals, MINIMAL_TASKS_MD)
        assert len(set(ids)) == 3  # all unique
        assert ids == ["T-P0-2", "T-P0-3", "T-P0-4"]


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
# process_proposals (integration)
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
        result = process_proposals(proposals, MINIMAL_TASKS_MD, "T-P0-1")
        assert result.success
        assert len(result.allocated_tasks) == 2
        assert result.allocated_tasks[0].task_id == "T-P1-11"
        assert result.allocated_tasks[1].task_id == "T-P1-12"
        assert result.allocated_tasks[1].depends_on == ["T-P1-11"]
        assert result.diff_text  # non-empty

    def test_empty_proposals(self) -> None:
        """Empty proposals list returns success with no tasks."""
        result = process_proposals([], MINIMAL_TASKS_MD, "T-P0-1")
        assert result.success
        assert result.allocated_tasks == []

    def test_too_many_rejected(self) -> None:
        """More than 8 proposals rejected."""
        proposals = [_make_proposal(title=f"Task {i}") for i in range(9)]
        result = process_proposals(proposals, MINIMAL_TASKS_MD, "T-P0-1")
        assert not result.success
        assert "Too many" in (result.error or "")

    def test_cycle_rejected(self) -> None:
        """Circular dependencies rejected."""
        proposals = [
            _make_proposal(title="Task A", dependencies=["Task B"]),
            _make_proposal(title="Task B", dependencies=["Task A"]),
        ]
        result = process_proposals(proposals, MINIMAL_TASKS_MD, "T-P0-1")
        assert not result.success
        assert "Circular" in (result.error or "")

    def test_invalid_dep_rejected(self) -> None:
        """Reference to non-existent task rejected."""
        proposals = [
            _make_proposal(title="Task A", dependencies=["T-P0-99"]),
        ]
        result = process_proposals(proposals, MINIMAL_TASKS_MD, "T-P0-1")
        assert not result.success
        assert "non-existent" in (result.error or "")

    def test_dep_on_existing_task(self) -> None:
        """Dependency on existing task in TASKS.md works."""
        proposals = [
            _make_proposal(title="Task A", dependencies=["T-P0-1"]),
        ]
        result = process_proposals(proposals, MINIMAL_TASKS_MD, "T-P0-1")
        assert result.success
        assert result.allocated_tasks[0].depends_on == ["T-P0-1"]

    def test_mixed_priorities(self) -> None:
        """Proposals with different priorities get correct IDs."""
        proposals = [
            _make_proposal(title="P0 task", priority="P0"),
            _make_proposal(title="P2 task", priority="P2"),
        ]
        result = process_proposals(proposals, MINIMAL_TASKS_MD, "T-P0-1")
        assert result.success
        assert result.allocated_tasks[0].task_id == "T-P0-2"
        assert result.allocated_tasks[1].task_id == "T-P2-0"


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


# ---------------------------------------------------------------------------
# write_allocated_tasks
# ---------------------------------------------------------------------------


class TestWriteAllocatedTasks:
    """Tests for writing allocated tasks to TASKS.md."""

    def test_writes_tasks_to_file(self, tmp_path: Path) -> None:
        """Tasks written to TASKS.md file."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(MINIMAL_TASKS_MD, encoding="utf-8")

        writer = TasksWriter(tasks_md)
        tasks = [
            AllocatedTask(
                task_id="T-P1-11", title="New Task A",
                description="Implement A",
                priority="P1", complexity="M",
                depends_on=["T-P0-1"],
                acceptance_criteria=["Works", "Tested"],
                parent_task_id="T-P0-1",
            ),
            AllocatedTask(
                task_id="T-P1-12", title="New Task B",
                description="Implement B",
                priority="P1", complexity="S",
                depends_on=["T-P1-11"],
                acceptance_criteria=["Works"],
                parent_task_id="T-P0-1",
            ),
        ]

        result = write_allocated_tasks(writer, tasks)
        assert result.success
        assert result.written_ids == ["T-P1-11", "T-P1-12"]

        # Verify content
        content = tasks_md.read_text(encoding="utf-8")
        assert "#### T-P1-11: New Task A" in content
        assert "#### T-P1-12: New Task B" in content
        assert "- **Priority**: P1" in content
        assert "- **Depends on**: T-P0-1" in content
        assert "- **Depends on**: T-P1-11" in content
        assert "  1. Works" in content
        assert "  2. Tested" in content

    def test_backup_created(self, tmp_path: Path) -> None:
        """Backup file created before write."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(MINIMAL_TASKS_MD, encoding="utf-8")

        writer = TasksWriter(tasks_md)
        tasks = [
            AllocatedTask(
                task_id="T-P1-11", title="Task A",
                description="Do A", priority="P1", complexity="M",
                depends_on=[], acceptance_criteria=[],
                parent_task_id="T-P0-1",
            ),
        ]

        result = write_allocated_tasks(writer, tasks)
        assert result.success
        assert (tmp_path / "TASKS.md.bak").is_file()

    def test_empty_list_noop(self, tmp_path: Path) -> None:
        """Empty task list is a no-op."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(MINIMAL_TASKS_MD, encoding="utf-8")

        writer = TasksWriter(tasks_md)
        result = write_allocated_tasks(writer, [])
        assert result.success
        assert result.written_ids == []

    def test_missing_file_fails(self, tmp_path: Path) -> None:
        """Missing TASKS.md file returns error."""
        tasks_md = tmp_path / "TASKS.md"
        writer = TasksWriter(tasks_md)
        tasks = [
            AllocatedTask(
                task_id="T-P1-1", title="Task A",
                description="Do A", priority="P1", complexity="M",
                depends_on=[], acceptance_criteria=[],
                parent_task_id="T-P0-1",
            ),
        ]
        result = write_allocated_tasks(writer, tasks)
        assert not result.success
        assert "not found" in (result.error or "")

    def test_tasks_inserted_in_active_section(self, tmp_path: Path) -> None:
        """New tasks inserted before the Completed Tasks section."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(MINIMAL_TASKS_MD, encoding="utf-8")

        writer = TasksWriter(tasks_md)
        tasks = [
            AllocatedTask(
                task_id="T-P1-11", title="New One",
                description="Test", priority="P1", complexity="S",
                depends_on=[], acceptance_criteria=[],
                parent_task_id="T-P0-1",
            ),
        ]

        write_allocated_tasks(writer, tasks)

        content = tasks_md.read_text(encoding="utf-8")
        # New task should appear before ## Completed Tasks
        active_idx = content.index("## Active Tasks")
        new_task_idx = content.index("T-P1-11")
        completed_idx = content.index("## Completed Tasks")
        assert active_idx < new_task_idx < completed_idx
