"""Tests for database layer in src/db.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.db import (
    ProjectSettingsRow,
    TaskRow,
    get_session,
    init_db,
    task_dict_to_row_kwargs,
    task_row_to_dict,
)

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


# ---------------------------------------------------------------------------
# Column migration tests
# ---------------------------------------------------------------------------


class TestMigrateColumns:
    """Tests for _migrate_missing_columns in init_db()."""

    async def test_init_db_adds_missing_column_to_existing_table(self) -> None:
        """init_db must add columns missing from an existing table.

        Simulates a stale state.db created before T-P0-18 that lacks the
        review_gate_enabled column on project_settings.
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

        # Step 1: Create project_settings with only project_id (simulating old schema)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE project_settings ("
                    "  project_id VARCHAR(32) PRIMARY KEY"
                    ")"
                )
            )

        # Step 2: Run init_db -- it should add the missing columns
        await init_db(engine)

        # Step 3: Verify both execution_paused and review_gate_enabled exist
        async with engine.begin() as conn:
            cols = await conn.run_sync(
                lambda c: {
                    col["name"] for col in sa_inspect(c).get_columns("project_settings")
                }
            )

        assert "execution_paused" in cols, "execution_paused column not migrated"
        assert "review_gate_enabled" in cols, "review_gate_enabled column not migrated"

        await engine.dispose()

    async def test_init_db_idempotent_on_complete_schema(self) -> None:
        """Calling init_db twice on a complete schema must not error."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

        await init_db(engine)
        await init_db(engine)  # second call must be a no-op

        # Verify tables still exist and are usable
        async with engine.begin() as conn:
            tables = await conn.run_sync(
                lambda c: sa_inspect(c).get_table_names()
            )
        assert "tasks" in tables
        assert "project_settings" in tables

        await engine.dispose()

    async def test_migrate_missing_columns_preserves_existing_data(self) -> None:
        """Existing rows must survive migration with correct defaults for new cols."""
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

        # Step 1: Create project_settings with only project_id
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE project_settings ("
                    "  project_id VARCHAR(32) PRIMARY KEY"
                    ")"
                )
            )
            # Insert a row before migration
            await conn.execute(
                text("INSERT INTO project_settings (project_id) VALUES ('proj1')")
            )

        # Step 2: Run init_db to add missing columns
        await init_db(engine)

        # Step 3: Verify the pre-existing row survived with correct defaults
        from src.db import create_session_factory

        factory = create_session_factory(engine)
        async with get_session(factory) as session:
            row = await session.get(ProjectSettingsRow, "proj1")
            assert row is not None, "Pre-existing row was lost during migration"
            assert row.project_id == "proj1"
            # Default for execution_paused is False (0), review_gate_enabled is True (1)
            assert row.execution_paused is False, "execution_paused default should be False"
            assert row.review_gate_enabled is True, "review_gate_enabled default should be True"

        await engine.dispose()
