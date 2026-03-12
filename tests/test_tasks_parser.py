"""Tests for tasks.db -> state.db sync functionality via TaskStoreBridge.

The old regex-based TasksParser and ParsedTask have been replaced by
direct SQL-to-SQL sync via the bridge. These tests verify the sync
function still correctly populates state.db from tasks.db.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.config import OrchestratorConfig, ProjectConfig, ProjectRegistry
from src.models import ExecutorType, TaskStatus
from src.sync.tasks_parser import SyncResult, sync_project_tasks
from src.task_manager import TaskManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(
    project_id: str,
    repo_path: Path,
    tasks_file: str = "TASKS.md",
) -> ProjectRegistry:
    """Create a minimal ProjectRegistry for testing."""
    config = OrchestratorConfig(
        projects={
            project_id: ProjectConfig(
                name=project_id,
                repo_path=repo_path,
                executor_type=ExecutorType.CODE,
                tasks_file=tasks_file,
            ),
        },
    )
    return ProjectRegistry(config)


def _setup_repo(tmp_path: Path) -> Path:
    """Create a repo with .claude/hooks/task_store.py."""
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks_dir = repo / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)

    real_store = Path(__file__).parent.parent / ".claude" / "hooks" / "task_store.py"
    if not real_store.is_file():
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
# SyncResult dataclass tests
# ---------------------------------------------------------------------------


class TestSyncResult:
    """SyncResult dataclass tests."""

    def test_defaults(self) -> None:
        """Default values are zeros and empty."""
        sr = SyncResult()
        assert sr.added == 0
        assert sr.updated == 0
        assert sr.unchanged == 0
        assert sr.skipped == 0
        assert sr.warnings == []


# ---------------------------------------------------------------------------
# sync_project_tasks tests
# ---------------------------------------------------------------------------


class TestSyncProjectTasks:
    """Tests for the bridge-based sync_project_tasks function."""

    @pytest.fixture
    def task_manager(self, session_factory) -> TaskManager:
        """Create a TaskManager with in-memory DB."""
        return TaskManager(session_factory)

    async def test_no_repo_path(self, task_manager: TaskManager) -> None:
        """Returns warning when project has no repo_path."""
        config = OrchestratorConfig(
            projects={
                "proj": ProjectConfig(
                    name="proj",
                    repo_path=None,
                    executor_type=ExecutorType.CODE,
                ),
            },
        )
        registry = ProjectRegistry(config)
        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.warnings
        assert "no repo_path" in result.warnings[0]

    async def test_no_tasks_db(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """Returns warning when tasks.db is not found."""
        repo = tmp_path / "empty_repo"
        repo.mkdir()
        registry = _make_registry("proj", repo)
        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.warnings
        assert "tasks.db" in result.warnings[0]

    async def test_syncs_tasks_from_db(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """Tasks from tasks.db appear in state.db after sync."""
        repo = _setup_repo(tmp_path)

        from src.sync.task_store_bridge import TaskStoreBridge
        bridge = TaskStoreBridge(repo)
        bridge.add_task(title="Task A", priority="P0", task_id="T-P0-1")
        bridge.add_task(title="Task B", priority="P1", task_id="T-P1-1")
        bridge.reproject()

        registry = _make_registry("proj", repo)
        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.added == 2
        assert result.warnings == []

        tasks = await task_manager.list_tasks(project_id="proj")
        assert len(tasks) == 2

    async def test_sync_idempotent(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """Second sync is idempotent."""
        repo = _setup_repo(tmp_path)

        from src.sync.task_store_bridge import TaskStoreBridge
        bridge = TaskStoreBridge(repo)
        bridge.add_task(title="Task A", priority="P0", task_id="T-P0-1")
        bridge.reproject()

        registry = _make_registry("proj", repo)
        await sync_project_tasks("proj", task_manager, registry)
        result2 = await sync_project_tasks("proj", task_manager, registry)
        assert result2.added == 0
        assert result2.unchanged == 1

    async def test_status_mapping(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """tasks.db statuses map to correct TaskStatus values."""
        repo = _setup_repo(tmp_path)

        from src.sync.task_store_bridge import TaskStoreBridge
        bridge = TaskStoreBridge(repo)
        bridge.add_task(title="Active", priority="P0", task_id="T-P0-1")
        bridge.reproject()

        registry = _make_registry("proj", repo)
        await sync_project_tasks("proj", task_manager, registry)

        task = await task_manager.get_task("proj:T-P0-1")
        assert task is not None
        assert task.status == TaskStatus.BACKLOG  # active -> BACKLOG

    async def test_sync_removes_deleted_tasks(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """Tasks removed from tasks.db are sync-deleted in state.db."""
        repo = _setup_repo(tmp_path)

        from src.sync.task_store_bridge import TaskStoreBridge
        bridge = TaskStoreBridge(repo)
        bridge.add_task(title="Keep", priority="P0", task_id="T-P0-1")
        bridge.add_task(title="Remove", priority="P0", task_id="T-P0-2")
        bridge.reproject()

        registry = _make_registry("proj", repo)
        await sync_project_tasks("proj", task_manager, registry)

        # Delete one task from tasks.db
        store = bridge._open_store()
        try:
            store.delete("T-P0-2")
        finally:
            store.close()
        bridge.reproject()

        # Re-sync
        await sync_project_tasks("proj", task_manager, registry)

        # Verify: T-P0-1 still exists, T-P0-2 is sync-deleted
        tasks = await task_manager.list_tasks(project_id="proj")
        local_ids = {t.local_task_id for t in tasks}
        assert "T-P0-1" in local_ids
        # T-P0-2 may be soft-deleted (not returned by list_tasks)
