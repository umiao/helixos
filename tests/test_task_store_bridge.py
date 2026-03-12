"""Tests for src.sync.task_store_bridge -- SQL-to-SQL sync bridge."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.models import TaskStatus
from src.sync.task_store_bridge import (
    _FORWARD_STATUS_MAP,
    _REVERSE_STATUS_MAP,
    BridgeTask,
    TaskStoreBridge,
    _load_task_store_module,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_repo(tmp_path: Path) -> Path:
    """Create a mock repo with .claude/hooks/task_store.py and tasks.db.

    Copies the real task_store.py from the shared hooks directory.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks_dir = repo / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)

    # Copy real task_store.py
    real_store = Path(__file__).parent.parent / ".claude" / "hooks" / "task_store.py"
    if not real_store.is_file():
        # Try the shared template location
        real_store = (
            Path(__file__).parent.parent.parent
            / "claude-code-project-template"
            / "shared"
            / "hooks"
            / "task_store.py"
        )
    if not real_store.is_file():
        pytest.skip("task_store.py not found for integration test")

    shutil.copy2(str(real_store), str(hooks_dir / "task_store.py"))
    return repo


# ---------------------------------------------------------------------------
# Status mapping tests
# ---------------------------------------------------------------------------


class TestStatusMappings:
    """Forward and reverse status mappings are consistent."""

    def test_forward_map_covers_all_db_statuses(self) -> None:
        """All tasks.db statuses have a mapping."""
        assert "active" in _FORWARD_STATUS_MAP
        assert "in_progress" in _FORWARD_STATUS_MAP
        assert "completed" in _FORWARD_STATUS_MAP
        assert "blocked" in _FORWARD_STATUS_MAP

    def test_reverse_map_covers_all_task_statuses(self) -> None:
        """All TaskStatus values have a reverse mapping."""
        for status in TaskStatus:
            assert status in _REVERSE_STATUS_MAP, f"Missing reverse mapping for {status}"

    def test_forward_then_reverse_round_trips(self) -> None:
        """Forward then reverse mapping produces valid DB status."""
        for _db_status, task_status in _FORWARD_STATUS_MAP.items():
            reverse = _REVERSE_STATUS_MAP[task_status]
            # The reverse may not equal original (e.g. BACKLOG->active is fine)
            assert reverse in _FORWARD_STATUS_MAP


class TestBridgeTask:
    """BridgeTask dataclass tests."""

    def test_defaults(self) -> None:
        """Default values are correct."""
        bt = BridgeTask(
            local_task_id="T-P0-1",
            title="Test",
            description="desc",
            status=TaskStatus.BACKLOG,
            complexity="S",
        )
        assert bt.depends_on == []

    def test_with_depends(self) -> None:
        """depends_on is set correctly."""
        bt = BridgeTask(
            local_task_id="T-P0-1",
            title="Test",
            description="desc",
            status=TaskStatus.BACKLOG,
            complexity="S",
            depends_on=["T-P0-2"],
        )
        assert bt.depends_on == ["T-P0-2"]


# ---------------------------------------------------------------------------
# Module loader tests
# ---------------------------------------------------------------------------


class TestLoadTaskStoreModule:
    """Tests for importlib-based module loading."""

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """FileNotFoundError when task_store.py is not found."""
        with pytest.raises(FileNotFoundError, match="task_store.py not found"):
            _load_task_store_module(tmp_path)

    def test_loads_real_module(self, tmp_path: Path) -> None:
        """Successfully loads task_store module."""
        repo = _setup_repo(tmp_path)
        module = _load_task_store_module(repo)
        assert hasattr(module, "TaskStore")


# ---------------------------------------------------------------------------
# TaskStoreBridge integration tests
# ---------------------------------------------------------------------------


class TestBridgeForwardSync:
    """Forward sync: tasks.db -> BridgeTask list."""

    def test_read_empty_db(self, tmp_path: Path) -> None:
        """Empty database returns empty list."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)
        assert bridge.read_all_tasks() == []

    def test_read_tasks_with_status_mapping(self, tmp_path: Path) -> None:
        """Tasks read from DB have correct status mapping."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        # Add tasks via bridge
        bridge.add_task(title="Active task", priority="P0")
        bridge.add_task(title="Blocked task", priority="P1")

        tasks = bridge.read_all_tasks()
        assert len(tasks) == 2
        # All new tasks default to 'active' -> BACKLOG
        assert all(t.status == TaskStatus.BACKLOG for t in tasks)

    def test_read_completed_task_maps_to_done(self, tmp_path: Path) -> None:
        """Completed task maps to TaskStatus.DONE."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        task_id = bridge.add_task(title="Test task", priority="P0")

        # Mark as completed via direct store access
        store = bridge._open_store()
        try:
            store.update(task_id, status="completed")
        finally:
            store.close()

        tasks = bridge.read_all_tasks()
        completed = [t for t in tasks if t.local_task_id == task_id]
        assert len(completed) == 1
        assert completed[0].status == TaskStatus.DONE


class TestBridgeReverseSync:
    """Reverse sync: state.db -> tasks.db."""

    def test_add_task(self, tmp_path: Path) -> None:
        """Add task creates entry in tasks.db."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        task_id = bridge.add_task(
            title="New task",
            priority="P0",
            complexity="M",
            description="Test description",
        )
        assert task_id.startswith("T-P0-")

        tasks = bridge.read_all_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "New task"
        assert tasks[0].complexity == "M"

    def test_add_task_with_explicit_id(self, tmp_path: Path) -> None:
        """Add task with explicit ID uses that ID."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        task_id = bridge.add_task(
            title="Explicit ID task",
            priority="P0",
            task_id="T-P0-42",
        )
        assert task_id == "T-P0-42"

    def test_update_task_title(self, tmp_path: Path) -> None:
        """Update title in tasks.db."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        task_id = bridge.add_task(title="Old title", priority="P0")
        result = bridge.update_task_title(task_id, "New title")
        assert result is True

        tasks = bridge.read_all_tasks()
        assert tasks[0].title == "New title"

    def test_update_task_title_not_found(self, tmp_path: Path) -> None:
        """Update title returns False for non-existent task."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        result = bridge.update_task_title("T-P0-999", "New title")
        assert result is False

    def test_update_task_status(self, tmp_path: Path) -> None:
        """Update status maps TaskStatus to tasks.db status."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        task_id = bridge.add_task(title="Test", priority="P0")
        result = bridge.update_task_status(task_id, TaskStatus.RUNNING)
        assert result is True

        # Verify via direct read
        store = bridge._open_store()
        try:
            task = store.get(task_id)
            assert task is not None
            assert task.status == "in_progress"
        finally:
            store.close()


class TestBridgeIdAllocation:
    """ID allocation tests."""

    def test_generate_next_id(self, tmp_path: Path) -> None:
        """Generates sequential IDs."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        id1 = bridge.generate_next_task_id("P0")
        id2 = bridge.generate_next_task_id("P0")
        assert id1 != id2
        assert id1.startswith("T-P0-")
        assert id2.startswith("T-P0-")

    def test_get_all_task_ids(self, tmp_path: Path) -> None:
        """Returns all existing task IDs."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        id1 = bridge.add_task(title="A", priority="P0")
        id2 = bridge.add_task(title="B", priority="P1")

        ids = bridge.get_all_task_ids()
        assert id1 in ids
        assert id2 in ids

    def test_get_all_task_ids_empty(self, tmp_path: Path) -> None:
        """Empty DB returns empty set."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)
        assert bridge.get_all_task_ids() == set()


class TestBridgeReproject:
    """TASKS.md projection tests."""

    def test_reproject_creates_file(self, tmp_path: Path) -> None:
        """Reproject creates TASKS.md from tasks.db."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        bridge.add_task(title="Test task", priority="P0")
        bridge.reproject()

        tasks_md = repo / "TASKS.md"
        assert tasks_md.is_file()
        content = tasks_md.read_text(encoding="utf-8")
        assert "Test task" in content
        assert "T-P0-" in content

    def test_reproject_updates_existing(self, tmp_path: Path) -> None:
        """Reproject updates existing TASKS.md."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)

        bridge.add_task(title="First task", priority="P0")
        bridge.reproject()

        bridge.add_task(title="Second task", priority="P1")
        bridge.reproject()

        content = (repo / "TASKS.md").read_text(encoding="utf-8")
        assert "First task" in content
        assert "Second task" in content


class TestBridgeClose:
    """Close is a no-op but should not error."""

    def test_close_noop(self, tmp_path: Path) -> None:
        """Close does not raise."""
        repo = _setup_repo(tmp_path)
        bridge = TaskStoreBridge(repo)
        bridge.close()
        # Should still work after close (new connection per operation)
        assert bridge.read_all_tasks() == []
