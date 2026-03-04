"""Tests for soft-delete sync with deleted_source tracking (T-P0-43).

Covers:
- deleted_source column on TaskRow
- UpsertResult.SKIPPED_DELETED for user-deleted tasks
- Resurrection of sync-deleted tasks
- delete_task() sets deleted_source='user'
- sync_mark_removed() marks tasks as sync-deleted
- SyncResult.skipped field
- Journey: user-delete stays deleted through sync; sync-delete resurrects
"""

from __future__ import annotations

from pathlib import Path

from src.config import OrchestratorConfig, ProjectConfig, ProjectRegistry
from src.db import TaskRow, get_session
from src.models import ExecutorType, Task, TaskStatus
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import TaskManager, UpsertResult

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


# ---------------------------------------------------------------------------
# TaskManager.delete_task sets deleted_source
# ---------------------------------------------------------------------------


class TestDeleteTaskSource:
    """Verify delete_task() sets deleted_source='user'."""

    async def test_delete_sets_user_source(self, session_factory) -> None:
        """delete_task() should set deleted_source='user'."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        await tm.delete_task("P0:T-P0-1")

        # Check DB row directly
        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            assert row is not None
            assert row.is_deleted is True
            assert row.deleted_source == "user"


# ---------------------------------------------------------------------------
# UpsertResult.SKIPPED_DELETED
# ---------------------------------------------------------------------------


class TestUpsertSkipsUserDeleted:
    """upsert_task() should skip user-deleted tasks."""

    async def test_upsert_skips_user_deleted(self, session_factory) -> None:
        """User-deleted task should return SKIPPED_DELETED on upsert."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        await tm.delete_task("P0:T-P0-1")

        result = await tm.upsert_task(_make_task(title="Updated"))
        assert result == UpsertResult.skipped_deleted

        # Task should still be deleted
        assert await tm.get_task("P0:T-P0-1") is None

    async def test_upsert_resurrects_sync_deleted(self, session_factory) -> None:
        """Sync-deleted task should be resurrected on upsert."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())

        # Manually mark as sync-deleted
        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            row.is_deleted = True
            row.deleted_source = "sync"

        result = await tm.upsert_task(_make_task(title="Resurrected"))
        assert result == UpsertResult.resurrected

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.title == "Resurrected"

    async def test_upsert_resurrects_legacy_deleted(self, session_factory) -> None:
        """Legacy deleted task (deleted_source=NULL) should be resurrected."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())

        # Manually mark as deleted with no source (legacy)
        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            row.is_deleted = True
            row.deleted_source = None

        result = await tm.upsert_task(_make_task(title="Legacy resurrect"))
        assert result == UpsertResult.resurrected

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.title == "Legacy resurrect"

    async def test_resurrection_clears_deleted_source(self, session_factory) -> None:
        """After resurrection, deleted_source should be cleared."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())

        # Sync-delete
        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            row.is_deleted = True
            row.deleted_source = "sync"

        await tm.upsert_task(_make_task())

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            assert row.is_deleted is False
            assert row.deleted_source is None


# ---------------------------------------------------------------------------
# sync_mark_removed
# ---------------------------------------------------------------------------


class TestSyncMarkRemoved:
    """Verify sync_mark_removed() marks missing tasks as sync-deleted."""

    async def test_mark_removed_tasks(self, session_factory) -> None:
        """Tasks not in parsed_ids should be sync-deleted."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1"))
        await tm.create_task(_make_task(task_id="P0:T-P0-2", local_task_id="T-P0-2"))
        await tm.create_task(_make_task(task_id="P0:T-P0-3", local_task_id="T-P0-3"))

        count = await tm.sync_mark_removed("P0", {"P0:T-P0-1"})
        assert count == 2

        # T-P0-1 still visible
        assert await tm.get_task("P0:T-P0-1") is not None
        # T-P0-2 and T-P0-3 sync-deleted
        assert await tm.get_task("P0:T-P0-2") is None
        assert await tm.get_task("P0:T-P0-3") is None

        # Verify deleted_source
        async with get_session(session_factory) as session:
            row2 = await session.get(TaskRow, "P0:T-P0-2")
            assert row2.deleted_source == "sync"
            row3 = await session.get(TaskRow, "P0:T-P0-3")
            assert row3.deleted_source == "sync"

    async def test_mark_removed_skips_already_deleted(self, session_factory) -> None:
        """Already-deleted tasks should not be double-deleted."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1"))
        await tm.create_task(_make_task(task_id="P0:T-P0-2", local_task_id="T-P0-2"))

        # User-delete T-P0-2
        await tm.delete_task("P0:T-P0-2")

        count = await tm.sync_mark_removed("P0", {"P0:T-P0-1"})
        assert count == 0  # T-P0-2 already deleted, not counted

    async def test_mark_removed_does_not_touch_other_projects(self, session_factory) -> None:
        """sync_mark_removed should only affect the specified project."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-P0-1", project_id="P0", local_task_id="T-P0-1"))
        await tm.create_task(_make_task(task_id="P1:T-P0-1", project_id="P1", local_task_id="T-P0-1"))

        count = await tm.sync_mark_removed("P0", set())
        assert count == 1

        # P1 task unaffected
        assert await tm.get_task("P1:T-P0-1") is not None


# ---------------------------------------------------------------------------
# SyncResult.skipped
# ---------------------------------------------------------------------------


class TestSyncSkippedCount:
    """Verify sync counts skipped-deleted tasks."""

    async def test_sync_counts_skipped(self, session_factory, tmp_path) -> None:
        """Sync should count user-deleted tasks as skipped."""
        tm = TaskManager(session_factory)

        # Create and user-delete a task
        await tm.create_task(_make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1"))
        await tm.delete_task("P0:T-P0-1")

        # Write TASKS.md with the same task ID
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "## Active Tasks\n\n#### T-P0-1: Deleted task\n- stuff\n",
            encoding="utf-8",
        )

        registry = _make_registry("P0", tmp_path)
        result = await sync_project_tasks("P0", tm, registry)

        assert result.skipped == 1
        assert result.added == 0


# ---------------------------------------------------------------------------
# Journey: user-delete survives sync, sync-delete resurrects
# ---------------------------------------------------------------------------


class TestDeleteSyncJourney:
    """End-to-end journey tests for delete + sync interactions."""

    async def test_user_delete_survives_sync(self, session_factory, tmp_path) -> None:
        """User deletes task via UI -> sync runs -> task stays deleted."""
        tm = TaskManager(session_factory)
        registry = _make_registry("P0", tmp_path)

        # Step 1: Initial sync creates the task
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "## Active Tasks\n\n#### T-P0-1: My task\n- description\n",
            encoding="utf-8",
        )
        result = await sync_project_tasks("P0", tm, registry)
        assert result.added == 1

        # Step 2: User deletes via UI
        await tm.delete_task("P0:T-P0-1")
        assert await tm.get_task("P0:T-P0-1") is None

        # Step 3: Sync runs again (task still in TASKS.md)
        result2 = await sync_project_tasks("P0", tm, registry)
        assert result2.skipped == 1
        assert result2.added == 0

        # Task should STILL be deleted
        assert await tm.get_task("P0:T-P0-1") is None

    async def test_sync_delete_then_readd_resurrects(self, session_factory, tmp_path) -> None:
        """Task removed from TASKS.md -> sync-deleted -> re-added -> resurrects."""
        tm = TaskManager(session_factory)
        registry = _make_registry("P0", tmp_path)
        tasks_md = tmp_path / "TASKS.md"

        # Step 1: Initial sync creates the task
        tasks_md.write_text(
            "## Active Tasks\n\n#### T-P0-1: My task\n- description\n",
            encoding="utf-8",
        )
        result = await sync_project_tasks("P0", tm, registry)
        assert result.added == 1

        # Step 2: Remove task from TASKS.md -> sync marks as sync-deleted
        tasks_md.write_text(
            "## Active Tasks\n\n",
            encoding="utf-8",
        )
        await sync_project_tasks("P0", tm, registry)
        assert await tm.get_task("P0:T-P0-1") is None

        # Verify it was sync-deleted
        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            assert row.is_deleted is True
            assert row.deleted_source == "sync"

        # Step 3: Re-add task to TASKS.md -> should resurrect
        tasks_md.write_text(
            "## Active Tasks\n\n#### T-P0-1: My task restored\n- new description\n",
            encoding="utf-8",
        )
        result3 = await sync_project_tasks("P0", tm, registry)
        assert result3.updated == 1  # resurrected counts as updated

        task = await tm.get_task("P0:T-P0-1")
        assert task is not None
        assert task.title == "My task restored"


# ---------------------------------------------------------------------------
# DB migration test
# ---------------------------------------------------------------------------


class TestDeletedSourceMigration:
    """Verify deleted_source column is added via migration."""

    async def test_deleted_source_column_exists(self, session_factory) -> None:
        """TaskRow should have deleted_source column defaulting to NULL."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            assert row is not None
            assert row.deleted_source is None

    async def test_migration_adds_deleted_source(self) -> None:
        """_migrate_missing_columns adds deleted_source to existing tables."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from src.db import init_db

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        await init_db(engine)

        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(tasks)"))
            columns = {row[1] for row in result.fetchall()}
            assert "deleted_source" in columns

        await engine.dispose()
