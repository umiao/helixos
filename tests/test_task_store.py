"""Unit tests for task_store.py -- SQLite-backed task management."""

import sys
from pathlib import Path

import pytest

# Add shared hooks to path
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "claude-code-project-template"
        / "shared"
        / "hooks"
    ),
)
from task_store import TaskStore


@pytest.fixture
def store():
    """Create an in-memory TaskStore for testing."""
    s = TaskStore(":memory:")
    yield s
    s.close()


# --- CRUD Tests ---


class TestCRUD:
    """Test basic create/read/update/delete operations."""

    def test_add_task(self, store: TaskStore) -> None:
        task = store.add(title="Fix the bug", priority="P0", complexity="M")
        assert task.id.startswith("T-P0-")
        assert task.title == "Fix the bug"
        assert task.status == "active"
        assert task.priority == "P0"
        assert task.complexity == "M"

    def test_add_task_with_description(self, store: TaskStore) -> None:
        task = store.add(
            title="Add feature",
            description="- **Description**: Do something\n- **AC**:\n  1. First\n  2. Second",
        )
        assert "Do something" in task.description

    def test_get_task(self, store: TaskStore) -> None:
        added = store.add(title="Test task")
        fetched = store.get(added.id)
        assert fetched is not None
        assert fetched.id == added.id
        assert fetched.title == "Test task"

    def test_get_nonexistent(self, store: TaskStore) -> None:
        assert store.get("T-P0-999") is None

    def test_update_status(self, store: TaskStore) -> None:
        task = store.add(title="Do something")
        updated = store.update(task.id, status="in_progress")
        assert updated is not None
        assert updated.status == "in_progress"

    def test_update_completed_sets_date(self, store: TaskStore) -> None:
        task = store.add(title="Complete me")
        updated = store.update(task.id, status="completed")
        assert updated is not None
        assert updated.completed_at is not None

    def test_update_uncomplete_clears_date(self, store: TaskStore) -> None:
        task = store.add(title="Complete me")
        store.update(task.id, status="completed")
        updated = store.update(task.id, status="active")
        assert updated is not None
        assert updated.completed_at is None

    def test_update_nonexistent(self, store: TaskStore) -> None:
        assert store.update("T-P0-999", title="Ghost") is None

    def test_update_multiple_fields(self, store: TaskStore) -> None:
        task = store.add(title="Original", priority="P2")
        updated = store.update(task.id, title="New Title", priority="P0")
        assert updated is not None
        assert updated.title == "New Title"
        assert updated.priority == "P0"

    def test_delete_task(self, store: TaskStore) -> None:
        task = store.add(title="Delete me")
        assert store.delete(task.id) is True
        assert store.get(task.id) is None

    def test_delete_nonexistent(self, store: TaskStore) -> None:
        assert store.delete("T-P0-999") is False

    def test_delete_removes_dependencies(self, store: TaskStore) -> None:
        t1 = store.add(title="Upstream")
        t2 = store.add(title="Downstream")
        store.add_dependency(t2.id, t1.id)
        store.delete(t1.id)
        # Downstream should have no deps
        fetched = store.get(t2.id)
        assert fetched is not None
        assert fetched.depends_on == []


# --- ID Generation Tests ---


class TestIDGeneration:
    """Test auto-generated task IDs."""

    def test_sequential_ids(self, store: TaskStore) -> None:
        t1 = store.add(title="First", priority="P0")
        t2 = store.add(title="Second", priority="P0")
        # Extract numbers
        num1 = int(t1.id.split("-")[2])
        num2 = int(t2.id.split("-")[2])
        assert num2 == num1 + 1

    def test_ids_across_priorities(self, store: TaskStore) -> None:
        t1 = store.add(title="P0 task", priority="P0")
        t2 = store.add(title="P1 task", priority="P1")
        # Both should get unique numbers (global counter)
        num1 = int(t1.id.split("-")[2])
        num2 = int(t2.id.split("-")[2])
        assert num1 != num2

    def test_explicit_id(self, store: TaskStore) -> None:
        task = store.add(title="Explicit", task_id="T-P0-999")
        assert task.id == "T-P0-999"

    def test_explicit_id_updates_counter(self, store: TaskStore) -> None:
        store.add(title="High ID", task_id="T-P0-500")
        # Next auto-generated should be > 500
        t2 = store.add(title="Auto", priority="P0")
        num = int(t2.id.split("-")[2])
        assert num > 500


# --- List Tests ---


class TestList:
    """Test task listing and filtering."""

    def test_list_all(self, store: TaskStore) -> None:
        store.add(title="T1")
        store.add(title="T2")
        tasks = store.list_tasks()
        assert len(tasks) == 2

    def test_list_by_status(self, store: TaskStore) -> None:
        t1 = store.add(title="Active")
        t2 = store.add(title="In Progress")
        store.update(t2.id, status="in_progress")
        active = store.list_tasks(status="active")
        assert len(active) == 1
        assert active[0].id == t1.id

    def test_list_by_priority(self, store: TaskStore) -> None:
        store.add(title="P0", priority="P0")
        store.add(title="P2", priority="P2")
        p0_tasks = store.list_tasks(priority="P0")
        assert len(p0_tasks) == 1
        assert p0_tasks[0].priority == "P0"

    def test_list_ordered_by_sort_order(self, store: TaskStore) -> None:
        store.add(title="First", priority="P0")
        store.add(title="Second", priority="P0")
        tasks = store.list_tasks(priority="P0")
        assert tasks[0].title == "First"
        assert tasks[1].title == "Second"


# --- Dependency Tests ---


class TestDependencies:
    """Test dependency management."""

    def test_add_dependency(self, store: TaskStore) -> None:
        t1 = store.add(title="Upstream")
        t2 = store.add(title="Downstream")
        assert store.add_dependency(t2.id, t1.id) is True
        fetched = store.get(t2.id)
        assert fetched is not None
        assert t1.id in fetched.depends_on

    def test_add_dependency_nonexistent(self, store: TaskStore) -> None:
        t1 = store.add(title="Real task")
        assert store.add_dependency(t1.id, "T-P0-999") is False

    def test_remove_dependency(self, store: TaskStore) -> None:
        t1 = store.add(title="Upstream")
        t2 = store.add(title="Downstream")
        store.add_dependency(t2.id, t1.id)
        assert store.remove_dependency(t2.id, t1.id) is True
        fetched = store.get(t2.id)
        assert fetched is not None
        assert fetched.depends_on == []

    def test_dependency_with_add(self, store: TaskStore) -> None:
        t1 = store.add(title="Upstream")
        t2 = store.add(title="Downstream", depends_on=[t1.id])
        fetched = store.get(t2.id)
        assert fetched is not None
        assert t1.id in fetched.depends_on


# --- Sort Order Tests ---


class TestSortOrder:
    """Test task ordering within priority groups."""

    def test_sort_order_increment(self, store: TaskStore) -> None:
        t1 = store.add(title="First", priority="P0")
        t2 = store.add(title="Second", priority="P0")
        assert t2.sort_order > t1.sort_order
        assert t2.sort_order - t1.sort_order == 100

    def test_reorder_after(self, store: TaskStore) -> None:
        t1 = store.add(title="A", priority="P0")
        store.add(title="B", priority="P0")
        t3 = store.add(title="C", priority="P0")
        # Move C after A (between A and B)
        assert store.reorder(t3.id, after=t1.id) is True
        tasks = store.list_tasks(priority="P0")
        assert [t.title for t in tasks] == ["A", "C", "B"]

    def test_reorder_to_beginning(self, store: TaskStore) -> None:
        store.add(title="A", priority="P0")
        t2 = store.add(title="B", priority="P0")
        # Move B to beginning
        assert store.reorder(t2.id, after=None) is True
        tasks = store.list_tasks(priority="P0")
        assert [t.title for t in tasks] == ["B", "A"]

    def test_reorder_cross_priority_fails(self, store: TaskStore) -> None:
        t1 = store.add(title="P0 task", priority="P0")
        t2 = store.add(title="P1 task", priority="P1")
        assert store.reorder(t2.id, after=t1.id) is False


# --- Projection Tests ---


class TestProjection:
    """Test deterministic TASKS.md generation."""

    def test_empty_projection(self, store: TaskStore) -> None:
        md = store.project()
        assert "# Task Backlog" in md
        assert "## In Progress" in md
        assert "## Active Tasks" in md
        assert "## Completed Tasks" in md

    def test_projection_includes_active_tasks(self, store: TaskStore) -> None:
        store.add(title="Fix bug", priority="P0")
        md = store.project()
        assert "#### T-P0-" in md
        assert "Fix bug" in md
        assert "### P0 -- Must Have" in md

    def test_projection_includes_in_progress(self, store: TaskStore) -> None:
        t = store.add(title="Working on this")
        store.update(t.id, status="in_progress")
        md = store.project()
        assert "## In Progress" in md
        assert "Working on this" in md

    def test_projection_includes_completed(self, store: TaskStore) -> None:
        t = store.add(title="Done task")
        store.update(t.id, status="completed")
        md = store.project()
        assert "## Completed Tasks" in md
        assert "Done task" in md
        assert "- [x] **" in md  # oneliner format

    def test_projection_deterministic(self, store: TaskStore) -> None:
        store.add(title="Task A", priority="P0")
        store.add(title="Task B", priority="P0")
        store.add(title="Task C", priority="P1")
        md1 = store.project()
        md2 = store.project()
        assert md1 == md2

    def test_projection_order_within_priority(self, store: TaskStore) -> None:
        store.add(title="First P0", priority="P0")
        store.add(title="Second P0", priority="P0")
        md = store.project()
        first_pos = md.index("First P0")
        second_pos = md.index("Second P0")
        assert first_pos < second_pos

    def test_projection_auto_generated_header(self, store: TaskStore) -> None:
        md = store.project()
        assert "Auto-generated from .claude/tasks.db" in md

    def test_projection_shows_archive_count(self, store: TaskStore) -> None:
        # Add tasks, complete and archive them
        for i in range(25):
            t = store.add(title=f"Task {i}", priority="P0")
            store.update(t.id, status="completed")
        store.archive(max_completed=20, keep_completed=5)
        md = store.project()
        assert "20 completed tasks archived" in md


# --- Archival Tests ---


class TestArchival:
    """Test task archival."""

    def test_no_archive_below_threshold(self, store: TaskStore) -> None:
        for i in range(5):
            t = store.add(title=f"Task {i}")
            store.update(t.id, status="completed")
        assert store.archive(max_completed=20, keep_completed=5) == 0

    def test_archive_above_threshold(self, store: TaskStore) -> None:
        for i in range(25):
            t = store.add(title=f"Task {i}", priority="P0")
            store.update(t.id, status="completed")
        archived = store.archive(max_completed=20, keep_completed=5)
        assert archived == 20  # 25 - 5

    def test_archive_preserves_deps_snapshot(self, store: TaskStore) -> None:
        t1 = store.add(title="Upstream", priority="P0")
        t2 = store.add(title="Downstream", priority="P0", depends_on=[t1.id])
        store.update(t1.id, status="completed")
        store.update(t2.id, status="completed")

        # Add more to trigger archival
        for i in range(25):
            t = store.add(title=f"Filler {i}", priority="P0")
            store.update(t.id, status="completed")

        store.archive(max_completed=20, keep_completed=5)
        archived = store.list_archived()
        # Find the downstream task
        downstream = next((a for a in archived if a.title == "Downstream"), None)
        assert downstream is not None
        assert t1.id in downstream.depends_on_snapshot

    def test_archive_cleans_dep_table(self, store: TaskStore) -> None:
        t1 = store.add(title="Upstream", priority="P0")
        store.update(t1.id, status="completed")
        for i in range(25):
            t = store.add(title=f"Filler {i}", priority="P0")
            store.update(t.id, status="completed")

        store.archive(max_completed=20, keep_completed=5)
        # Check t1 is gone from tasks table
        assert store.get(t1.id) is None

    def test_active_tasks_unaffected_by_archive(self, store: TaskStore) -> None:
        active = store.add(title="Still active", priority="P1")
        for i in range(25):
            t = store.add(title=f"Done {i}", priority="P0")
            store.update(t.id, status="completed")
        store.archive(max_completed=20, keep_completed=5)
        assert store.get(active.id) is not None


# --- Batch Tests ---


class TestBatch:
    """Test batch/atomic operations."""

    def test_batch_add(self, store: TaskStore) -> None:
        results = store.batch([
            {"cmd": "add", "title": "Task A", "priority": "P0"},
            {"cmd": "add", "title": "Task B", "priority": "P1"},
        ])
        assert len(results) == 2
        assert results[0]["ok"] is True
        assert results[1]["ok"] is True
        assert len(store.list_tasks()) == 2

    def test_batch_last_ref(self, store: TaskStore) -> None:
        t1 = store.add(title="Existing")
        results = store.batch([
            {"cmd": "add", "title": "New task", "priority": "P0"},
            {"cmd": "depend", "id": "$LAST", "on": t1.id},
        ])
        assert results[0]["ok"] is True
        assert results[1]["ok"] is True
        new_id = results[0]["id"]
        fetched = store.get(new_id)
        assert fetched is not None
        assert t1.id in fetched.depends_on

    def test_batch_unknown_command(self, store: TaskStore) -> None:
        results = store.batch([
            {"cmd": "unknown_cmd"},
        ])
        assert results[0]["ok"] is False

    def test_batch_add_and_update(self, store: TaskStore) -> None:
        results = store.batch([
            {"cmd": "add", "title": "New task", "priority": "P0"},
            {"cmd": "update", "id": "$LAST", "status": "in_progress"},
        ])
        assert results[0]["ok"] is True
        assert results[1]["ok"] is True
        task = store.get(results[0]["id"])
        assert task is not None
        assert task.status == "in_progress"


# --- Import Tests ---


SAMPLE_TASKS_MD = """\
# Task Backlog

> **Convention**: Pick tasks from top of Active.

## In Progress
<!-- Only ONE task here at a time. Focus. -->

#### T-P0-10: Fix critical auth bug
- **Priority**: P0
- **Complexity**: M
- **Depends on**: None
- **Description**: Auth tokens expire incorrectly
- **Acceptance Criteria**:
  1. Tokens last 24h
  2. Refresh works

## Active Tasks

### P0 -- Must Have (core functionality)

#### T-P0-11: Add rate limiting
- **Priority**: P0
- **Complexity**: S
- **Depends on**: T-P0-10
- **Description**: Add rate limiting to API endpoints

### P1 -- Should Have (agentic intelligence)

### P2 -- Nice to Have

#### T-P2-12: Update docs
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Update API documentation

### P3 -- Stretch Goals

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 5 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P0-9: Initial setup -- 2026-03-01
- Set up project structure and CI

- [x] **2026-03-01** -- T-P1-8: Add logging. Added structured logging
"""


class TestImport:
    """Test TASKS.md import and round-trip verification."""

    def test_import_parses_tasks(self, store: TaskStore) -> None:
        parsed = store.import_from_markdown(SAMPLE_TASKS_MD)
        ids = {p.id for p in parsed}
        assert "T-P0-10" in ids  # in_progress
        assert "T-P0-11" in ids  # active
        assert "T-P2-12" in ids  # active
        assert "T-P0-9" in ids   # completed
        assert "T-P1-8" in ids   # completed (oneliner)

    def test_import_status_mapping(self, store: TaskStore) -> None:
        store.import_from_markdown(SAMPLE_TASKS_MD)
        assert store.get("T-P0-10").status == "in_progress"
        assert store.get("T-P0-11").status == "active"
        assert store.get("T-P2-12").status == "active"
        assert store.get("T-P0-9").status == "completed"
        assert store.get("T-P1-8").status == "completed"

    def test_import_dependencies(self, store: TaskStore) -> None:
        store.import_from_markdown(SAMPLE_TASKS_MD)
        t11 = store.get("T-P0-11")
        assert t11 is not None
        assert "T-P0-10" in t11.depends_on

    def test_import_completed_dates(self, store: TaskStore) -> None:
        store.import_from_markdown(SAMPLE_TASKS_MD)
        t9 = store.get("T-P0-9")
        assert t9 is not None
        assert t9.completed_at == "2026-03-01"

    def test_import_preserves_sort_order(self, store: TaskStore) -> None:
        store.import_from_markdown(SAMPLE_TASKS_MD)
        # In-progress task should come first
        t10 = store.get("T-P0-10")
        t11 = store.get("T-P0-11")
        assert t10 is not None
        assert t11 is not None
        assert t10.sort_order < t11.sort_order

    def test_import_updates_id_counters(self, store: TaskStore) -> None:
        store.import_from_markdown(SAMPLE_TASKS_MD)
        # Next auto-generated ID should be > 12
        t = store.add(title="New after import", priority="P0")
        num = int(t.id.split("-")[2])
        assert num > 12

    def test_import_round_trip_verify(self, store: TaskStore) -> None:
        store.import_from_markdown(SAMPLE_TASKS_MD)
        diffs = store.verify_import(SAMPLE_TASKS_MD)
        assert diffs == [], f"Round-trip verification failed: {diffs}"

    def test_import_replaces_existing(self, store: TaskStore) -> None:
        # Add a task first
        store.add(title="Old task", task_id="T-P0-999")
        # Import should clear it
        store.import_from_markdown(SAMPLE_TASKS_MD)
        assert store.get("T-P0-999") is None

    def test_import_real_tasks_md(self, store: TaskStore) -> None:
        """Test import with the actual helixos TASKS.md fixture."""
        fixture = Path(__file__).parent / "fixtures" / "sample_tasks.md"
        if not fixture.exists():
            pytest.skip("sample_tasks.md fixture not found")
        content = fixture.read_text(encoding="utf-8")
        parsed = store.import_from_markdown(content)
        assert len(parsed) > 0
        diffs = store.verify_import(content)
        assert diffs == [], f"Real TASKS.md round-trip failed: {diffs}"


# --- Projection Hash Tests ---


class TestProjectionHash:
    """Test projection hash tracking."""

    def test_hash_initially_none(self, store: TaskStore) -> None:
        assert store.get_projection_hash() is None

    def test_hash_set_and_get(self, store: TaskStore) -> None:
        store.set_projection_hash("abc123")
        assert store.get_projection_hash() == "abc123"

    def test_hash_update(self, store: TaskStore) -> None:
        store.set_projection_hash("first")
        store.set_projection_hash("second")
        assert store.get_projection_hash() == "second"


# --- Edge Cases ---


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_description(self, store: TaskStore) -> None:
        t = store.add(title="No desc")
        assert t.description == ""

    def test_multiline_description(self, store: TaskStore) -> None:
        desc = "Line 1\nLine 2\n- Bullet 1\n- Bullet 2"
        t = store.add(title="Multi", description=desc)
        fetched = store.get(t.id)
        assert fetched is not None
        assert fetched.description == desc

    def test_special_characters_in_title(self, store: TaskStore) -> None:
        t = store.add(title='Fix "quotes" & <angle> brackets')
        fetched = store.get(t.id)
        assert fetched is not None
        assert fetched.title == 'Fix "quotes" & <angle> brackets'

    def test_self_dependency_prevented(self, store: TaskStore) -> None:
        t = store.add(title="Self")
        # SQLite CHECK constraint prevents self-dependency
        with pytest.raises(ValueError, match="Self-dependency"):
            store.add_dependency(t.id, t.id)

    def test_concurrent_sort_orders(self, store: TaskStore) -> None:
        """Tasks added to different priorities get independent sort orders."""
        t1 = store.add(title="P0", priority="P0")
        t2 = store.add(title="P1", priority="P1")
        # Both start at sort_order 100 (independent per priority)
        assert t1.sort_order == 100
        assert t2.sort_order == 100
