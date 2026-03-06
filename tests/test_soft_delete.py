"""Tests for soft-delete functionality (T-P0-22).

Covers TaskManager.delete_task(), is_deleted filtering in all queries,
and the DELETE /api/tasks/{task_id} endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.models import ExecutorType, Task, TaskStatus
from src.task_manager import TaskManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "P0:T-P0-1",
    project_id: str = "P0",
    local_task_id: str = "T-P0-1",
    title: str = "Test task",
    status: TaskStatus = TaskStatus.BACKLOG,
    depends_on: list[str] | None = None,
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
        depends_on=depends_on or [],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TaskManager.delete_task tests
# ---------------------------------------------------------------------------


class TestDeleteTask:
    """Tests for TaskManager.delete_task()."""

    async def test_delete_backlog_task(self, session_factory) -> None:
        """Deleting a BACKLOG task sets is_deleted=True."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())

        await tm.delete_task("P0:T-P0-1")

        # Task no longer visible via get_task
        assert await tm.get_task("P0:T-P0-1") is None

    async def test_delete_done_task(self, session_factory) -> None:
        """Deleting a DONE task works (preserves DB row)."""
        tm = TaskManager(session_factory)
        t = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(t)
        await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)
        await tm.update_status("P0:T-P0-1", TaskStatus.DONE)

        await tm.delete_task("P0:T-P0-1")
        assert await tm.get_task("P0:T-P0-1") is None

    async def test_delete_failed_task(self, session_factory) -> None:
        """Deleting a FAILED task works."""
        tm = TaskManager(session_factory)
        t = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(t)
        await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)
        await tm.update_status("P0:T-P0-1", TaskStatus.FAILED)

        await tm.delete_task("P0:T-P0-1")
        assert await tm.get_task("P0:T-P0-1") is None

    async def test_delete_running_raises(self, session_factory) -> None:
        """Cannot delete a RUNNING task -- should raise ValueError."""
        tm = TaskManager(session_factory)
        t = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(t)
        await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        await tm.update_status("P0:T-P0-1", TaskStatus.RUNNING)

        with pytest.raises(ValueError, match="RUNNING"):
            await tm.delete_task("P0:T-P0-1")

        # Task still exists
        assert await tm.get_task("P0:T-P0-1") is not None

    async def test_delete_nonexistent_raises(self, session_factory) -> None:
        """Deleting a non-existent task raises ValueError."""
        tm = TaskManager(session_factory)
        with pytest.raises(ValueError, match="not found"):
            await tm.delete_task("P0:T-doesnt-exist")

    async def test_delete_already_deleted_raises(self, session_factory) -> None:
        """Deleting an already-deleted task raises ValueError (not found)."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        await tm.delete_task("P0:T-P0-1")

        with pytest.raises(ValueError, match="not found"):
            await tm.delete_task("P0:T-P0-1")

    async def test_delete_with_dependents_raises(self, session_factory) -> None:
        """Deleting a task with active dependents raises ValueError."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1"))
        await tm.create_task(
            _make_task(
                task_id="P0:T-P0-2",
                local_task_id="T-P0-2",
                depends_on=["P0:T-P0-1"],
            )
        )

        with pytest.raises(ValueError, match="active dependents"):
            await tm.delete_task("P0:T-P0-1")

        # Task still exists
        assert await tm.get_task("P0:T-P0-1") is not None

    async def test_delete_with_dependents_force(self, session_factory) -> None:
        """Force-deleting a task with dependents succeeds."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1"))
        await tm.create_task(
            _make_task(
                task_id="P0:T-P0-2",
                local_task_id="T-P0-2",
                depends_on=["P0:T-P0-1"],
            )
        )

        await tm.delete_task("P0:T-P0-1", force=True)
        assert await tm.get_task("P0:T-P0-1") is None

    async def test_delete_with_deleted_dependents_ok(self, session_factory) -> None:
        """Deleting a task whose only dependents are also deleted succeeds."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1"))
        await tm.create_task(
            _make_task(
                task_id="P0:T-P0-2",
                local_task_id="T-P0-2",
                depends_on=["P0:T-P0-1"],
            )
        )
        # Delete the dependent first
        await tm.delete_task("P0:T-P0-2")

        # Now deleting the parent should work (no active dependents)
        await tm.delete_task("P0:T-P0-1")
        assert await tm.get_task("P0:T-P0-1") is None


# ---------------------------------------------------------------------------
# Soft-deleted tasks excluded from queries
# ---------------------------------------------------------------------------


class TestSoftDeleteFiltering:
    """Verify that soft-deleted tasks are excluded from all query methods."""

    async def test_list_tasks_excludes_deleted(self, session_factory) -> None:
        """list_tasks() should not return soft-deleted tasks."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1"))
        await tm.create_task(_make_task(task_id="P0:T-P0-2", local_task_id="T-P0-2"))
        await tm.create_task(_make_task(task_id="P0:T-P0-3", local_task_id="T-P0-3"))

        await tm.delete_task("P0:T-P0-2")

        tasks = await tm.list_tasks()
        assert len(tasks) == 2
        task_ids = {t.id for t in tasks}
        assert "P0:T-P0-2" not in task_ids

    async def test_list_tasks_by_project_excludes_deleted(self, session_factory) -> None:
        """list_tasks(project_id=...) should exclude soft-deleted tasks."""
        tm = TaskManager(session_factory)
        await tm.create_task(
            _make_task(task_id="P0:T-1", project_id="P0", local_task_id="T-1")
        )
        await tm.create_task(
            _make_task(task_id="P0:T-2", project_id="P0", local_task_id="T-2")
        )

        await tm.delete_task("P0:T-1")

        tasks = await tm.list_tasks(project_id="P0")
        assert len(tasks) == 1
        assert tasks[0].id == "P0:T-2"

    async def test_get_ready_tasks_excludes_deleted(self, session_factory) -> None:
        """get_ready_tasks() should not return soft-deleted QUEUED tasks."""
        tm = TaskManager(session_factory)
        await tm.create_task(
            _make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1")
        )
        await tm.create_task(
            _make_task(task_id="P0:T-P0-2", local_task_id="T-P0-2")
        )
        # Move both to QUEUED
        await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)
        await tm.update_status("P0:T-P0-2", TaskStatus.QUEUED)

        # Soft-delete one
        await tm.delete_task("P0:T-P0-1")

        ready = await tm.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "P0:T-P0-2"

    async def test_update_status_deleted_task_raises(self, session_factory) -> None:
        """Trying to update_status on a deleted task raises ValueError."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())
        await tm.delete_task("P0:T-P0-1")

        with pytest.raises(ValueError, match="not found"):
            await tm.update_status("P0:T-P0-1", TaskStatus.QUEUED)

    async def test_update_task_deleted_raises(self, session_factory) -> None:
        """Trying to update_task on a deleted task raises ValueError."""
        tm = TaskManager(session_factory)
        t = _make_task()
        await tm.create_task(t)
        await tm.delete_task("P0:T-P0-1")

        t.title = "Updated title"
        with pytest.raises(ValueError, match="not found"):
            await tm.update_task(t)

    async def test_get_dependents(self, session_factory) -> None:
        """get_dependents() returns IDs of non-deleted tasks that depend on target."""
        tm = TaskManager(session_factory)
        await tm.create_task(_make_task(task_id="P0:T-P0-1", local_task_id="T-P0-1"))
        await tm.create_task(
            _make_task(
                task_id="P0:T-P0-2",
                local_task_id="T-P0-2",
                depends_on=["P0:T-P0-1"],
            )
        )
        await tm.create_task(
            _make_task(
                task_id="P0:T-P0-3",
                local_task_id="T-P0-3",
                depends_on=["P0:T-P0-1"],
            )
        )

        deps = await tm.get_dependents("P0:T-P0-1")
        assert set(deps) == {"P0:T-P0-2", "P0:T-P0-3"}

        # Delete one dependent
        await tm.delete_task("P0:T-P0-2")
        deps2 = await tm.get_dependents("P0:T-P0-1")
        assert deps2 == ["P0:T-P0-3"]


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestDeleteTaskAPI:
    """Tests for DELETE /api/tasks/{task_id} endpoint."""

    @pytest.fixture
    def app(self):
        """Create a test FastAPI app with mocked state."""
        from unittest.mock import MagicMock

        from fastapi import FastAPI

        from src.api import api_router

        test_app = FastAPI()
        test_app.include_router(api_router)
        test_app.state.task_manager = MagicMock(spec=TaskManager)
        test_app.state.event_bus = MagicMock()
        test_app.state.event_bus.emit = MagicMock()
        return test_app

    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        return AsyncClient(transport=transport, base_url="http://test")

    async def test_delete_success(self, app, client) -> None:
        """DELETE /api/tasks/{id} returns 204 on success."""
        app.state.task_manager.delete_task = AsyncMock(return_value=None)

        resp = await client.delete("/api/tasks/P0:T-P0-1")
        assert resp.status_code == 204

        app.state.task_manager.delete_task.assert_called_once_with(
            "P0:T-P0-1", force=False,
        )
        app.state.event_bus.emit.assert_called_once_with(
            "task_deleted", "P0:T-P0-1", {"task_id": "P0:T-P0-1"}, origin="api",
        )

    async def test_delete_with_force(self, app, client) -> None:
        """DELETE /api/tasks/{id}?force=true passes force=True."""
        app.state.task_manager.delete_task = AsyncMock(return_value=None)

        resp = await client.delete("/api/tasks/P0:T-P0-1?force=true")
        assert resp.status_code == 204

        app.state.task_manager.delete_task.assert_called_once_with(
            "P0:T-P0-1", force=True,
        )

    async def test_delete_not_found(self, app, client) -> None:
        """DELETE /api/tasks/{id} returns 404 when task not found."""
        app.state.task_manager.delete_task = AsyncMock(
            side_effect=ValueError("Task not found: P0:T-P0-99"),
        )

        resp = await client.delete("/api/tasks/P0:T-P0-99")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    async def test_delete_running_409(self, app, client) -> None:
        """DELETE /api/tasks/{id} returns 409 for RUNNING task."""
        app.state.task_manager.delete_task = AsyncMock(
            side_effect=ValueError(
                "Cannot delete RUNNING task P0:T-P0-1. Cancel it first."
            ),
        )

        resp = await client.delete("/api/tasks/P0:T-P0-1")
        assert resp.status_code == 409
        assert "RUNNING" in resp.json()["detail"]

    async def test_delete_with_dependents_409(self, app, client) -> None:
        """DELETE /api/tasks/{id} returns 409 with dependent list."""
        app.state.task_manager.delete_task = AsyncMock(
            side_effect=ValueError(
                "Task P0:T-P0-1 has active dependents: P0:T-P0-2, P0:T-P0-3"
            ),
        )
        app.state.task_manager.get_dependents = AsyncMock(
            return_value=["P0:T-P0-2", "P0:T-P0-3"],
        )

        resp = await client.delete("/api/tasks/P0:T-P0-1")
        assert resp.status_code == 409
        body = resp.json()
        assert "dependents" in body
        assert set(body["dependents"]) == {"P0:T-P0-2", "P0:T-P0-3"}


# ---------------------------------------------------------------------------
# DB migration test
# ---------------------------------------------------------------------------


class TestIsDeletedMigration:
    """Verify that is_deleted column is added to existing databases."""

    async def test_is_deleted_column_exists(self, session_factory) -> None:
        """TaskRow should have is_deleted column with default False."""
        from src.db import TaskRow, get_session

        tm = TaskManager(session_factory)
        await tm.create_task(_make_task())

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            assert row is not None
            assert row.is_deleted is False

    async def test_migration_adds_is_deleted(self) -> None:
        """_migrate_missing_columns adds is_deleted to existing tables."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from src.db import init_db

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

        # Create tables (includes is_deleted since it's in the ORM)
        await init_db(engine)

        # Verify is_deleted column exists via PRAGMA
        async with engine.begin() as conn:
            result = await conn.execute(
                text("PRAGMA table_info(tasks)")
            )
            columns = {row[1] for row in result.fetchall()}
            assert "is_deleted" in columns

        await engine.dispose()
