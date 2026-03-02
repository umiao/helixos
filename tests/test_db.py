"""Tests for database layer in src/db.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from src.db import TaskRow, get_session, task_dict_to_row_kwargs, task_row_to_dict

# ---------------------------------------------------------------------------
# Table creation (handled by conftest async_engine fixture)
# ---------------------------------------------------------------------------


class TestInitDb:
    """Database initialization tests."""

    async def test_tables_created(self, async_engine) -> None:
        """Tables should exist after init_db runs via fixture."""
        from sqlalchemy import inspect

        async with async_engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert "tasks" in table_names
        assert "dependencies" in table_names


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


class TestTaskCrud:
    """Basic CRUD operations on TaskRow."""

    def _make_task_data(self, task_id: str = "P0:T-P0-1") -> dict:
        """Build a dict of task data for testing."""
        now = datetime.now(UTC).isoformat()
        return {
            "id": task_id,
            "project_id": "P0",
            "local_task_id": "T-P0-1",
            "title": "Test task",
            "description": "A test task",
            "status": "backlog",
            "executor_type": "code",
            "depends_on": [],
            "review": None,
            "execution": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }

    async def test_insert_and_read(self, session_factory) -> None:
        """Insert a TaskRow and read it back."""
        data = self._make_task_data()
        kwargs = task_dict_to_row_kwargs(data)

        async with get_session(session_factory) as session:
            row = TaskRow(**kwargs)
            session.add(row)

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            assert row is not None
            assert row.title == "Test task"
            assert row.status == "backlog"

    async def test_update_status(self, session_factory) -> None:
        """Update a task's status."""
        data = self._make_task_data()
        kwargs = task_dict_to_row_kwargs(data)

        async with get_session(session_factory) as session:
            row = TaskRow(**kwargs)
            session.add(row)

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            row.status = "queued"

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            assert row.status == "queued"

    async def test_delete(self, session_factory) -> None:
        """Delete a task row."""
        data = self._make_task_data()
        kwargs = task_dict_to_row_kwargs(data)

        async with get_session(session_factory) as session:
            session.add(TaskRow(**kwargs))

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            await session.delete(row)

        async with get_session(session_factory) as session:
            row = await session.get(TaskRow, "P0:T-P0-1")
            assert row is None

    async def test_multiple_tasks(self, session_factory) -> None:
        """Insert and query multiple tasks."""
        from sqlalchemy import select

        for i in range(3):
            data = self._make_task_data(f"P0:T-P0-{i}")
            data["local_task_id"] = f"T-P0-{i}"
            data["title"] = f"Task {i}"
            kwargs = task_dict_to_row_kwargs(data)
            async with get_session(session_factory) as session:
                session.add(TaskRow(**kwargs))

        async with get_session(session_factory) as session:
            result = await session.execute(
                select(TaskRow).where(TaskRow.project_id == "P0")
            )
            rows = result.scalars().all()
            assert len(rows) == 3


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


class TestConversionHelpers:
    """Tests for task_row_to_dict and task_dict_to_row_kwargs."""

    def test_row_to_dict_basic(self) -> None:
        """Convert a TaskRow to a dict."""
        row = TaskRow(
            id="P0:T-1",
            project_id="P0",
            local_task_id="T-1",
            title="Test",
            description="desc",
            status="backlog",
            executor_type="code",
            depends_on_json="[]",
            review_json=None,
            execution_json=None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            completed_at=None,
        )
        d = task_row_to_dict(row)
        assert d["id"] == "P0:T-1"
        assert d["depends_on"] == []
        assert d["review"] is None
        assert d["execution"] is None

    def test_row_to_dict_with_json(self) -> None:
        """Convert a TaskRow with JSON fields populated."""
        review_data = {"rounds_total": 2, "rounds_completed": 1, "reviews": []}
        row = TaskRow(
            id="P0:T-2",
            project_id="P0",
            local_task_id="T-2",
            title="Test 2",
            description="",
            status="review",
            executor_type="agent",
            depends_on_json=json.dumps(["P1:T-1"]),
            review_json=json.dumps(review_data),
            execution_json=None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            completed_at=None,
        )
        d = task_row_to_dict(row)
        assert d["depends_on"] == ["P1:T-1"]
        assert d["review"]["rounds_total"] == 2

    def test_dict_to_row_kwargs(self) -> None:
        """Convert a Task-like dict to row kwargs."""
        data = {
            "id": "P0:T-1",
            "project_id": "P0",
            "local_task_id": "T-1",
            "title": "Test",
            "description": "",
            "status": "backlog",
            "executor_type": "code",
            "depends_on": ["P1:T-1"],
            "review": None,
            "execution": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "completed_at": None,
        }
        kwargs = task_dict_to_row_kwargs(data)
        assert kwargs["depends_on_json"] == json.dumps(["P1:T-1"])
        assert kwargs["review_json"] is None
