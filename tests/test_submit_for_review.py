"""Tests for T-P2-174: Atomic submit-for-review endpoint.

Covers:
  - POST /api/tasks/{id}/submit-for-review atomically updates fields + transitions
  - Title and description updates applied before status transition
  - Works with no field changes (just status transition)
  - 404 for missing task
  - plan_json sync when description is updated
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import Base
from src.events import EventBus
from src.process_manager import ProcessStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager
from tests.factories import make_config, make_task

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_engine():
    """In-memory async engine for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(test_engine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
async def event_bus() -> EventBus:
    """EventBus instance."""
    return EventBus()


@pytest.fixture
async def test_app(tmp_path: Path, session_factory, event_bus):
    """Create a test FastAPI app with mocked lifespan services."""
    from fastapi import FastAPI

    from src.api import api_router
    from src.config import ProjectRegistry
    from src.env_loader import EnvLoader
    from src.events import sse_router
    from src.history_writer import HistoryWriter

    config = make_config(tmp_path)
    tm = TaskManager(session_factory)
    registry = ProjectRegistry(config)

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    env_loader = EnvLoader(env_path)

    history_writer = HistoryWriter(session_factory)

    scheduler = MagicMock(spec=Scheduler)
    scheduler.cancel_task = AsyncMock(return_value=None)
    scheduler.is_project_paused = MagicMock(return_value=False)
    scheduler.pause_project = AsyncMock()
    scheduler.resume_project = AsyncMock()
    scheduler.force_tick = AsyncMock()

    # Review gate disabled by default
    scheduler.is_review_gate_enabled = MagicMock(return_value=False)
    scheduler.enable_review_gate = AsyncMock()
    scheduler.disable_review_gate = AsyncMock()

    app = FastAPI(title="HelixOS Test", version="0.1.0")
    app.include_router(sse_router)
    app.include_router(api_router)

    app.state.config = config
    app.state.task_manager = tm
    app.state.registry = registry
    app.state.env_loader = env_loader
    app.state.event_bus = event_bus
    app.state.scheduler = scheduler
    app.state.review_pipeline = None
    app.state.history_writer = history_writer
    app.state.engine = None

    mock_pm = MagicMock()
    mock_pm.status.return_value = ProcessStatus(running=False)
    app.state.process_manager = mock_pm

    yield app


@pytest.fixture
async def client(test_app) -> AsyncClient:
    """httpx AsyncClient for the test app."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def tm(test_app) -> TaskManager:
    """TaskManager from the test app."""
    return test_app.state.task_manager


# ===========================================================================
# Tests
# ===========================================================================


class TestSubmitForReview:
    """POST /api/tasks/{id}/submit-for-review endpoint tests."""

    async def test_atomic_update_title_and_transition(self, client, tm):
        """Title is updated and task transitions to REVIEW atomically."""
        task = make_task(
            task_id="proj-a:T-P0-1",
            project_id="proj-a",
            title="Old Title",
            description="A sufficiently long plan description for review.",
        )
        await tm.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-1/submit-for-review",
            json={"title": "New Title"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Title"
        assert data["status"] == "review"

    async def test_atomic_update_description_and_transition(self, client, tm):
        """Description is updated and task transitions to REVIEW atomically."""
        task = make_task(
            task_id="proj-a:T-P0-2",
            project_id="proj-a",
            description="Short old desc that is long enough.",
        )
        await tm.create_task(task)

        new_desc = "A brand new detailed description for review submission."
        resp = await client.post(
            "/api/tasks/proj-a:T-P0-2/submit-for-review",
            json={"description": new_desc},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["description"] == new_desc
        assert data["status"] == "review"

    async def test_no_fields_just_transition(self, client, tm):
        """Empty body still transitions to REVIEW."""
        task = make_task(
            task_id="proj-a:T-P0-3",
            project_id="proj-a",
            description="This description is definitely long enough.",
        )
        await tm.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-3/submit-for-review",
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "review"

    async def test_404_for_missing_task(self, client):
        """Returns 404 for nonexistent task."""
        resp = await client.post(
            "/api/tasks/proj-a:T-P0-999/submit-for-review",
            json={},
        )
        assert resp.status_code == 404

    async def test_both_title_and_description_updated(self, client, tm):
        """Both title and description updated atomically."""
        task = make_task(
            task_id="proj-a:T-P0-4",
            project_id="proj-a",
            title="Old",
            description="Old description that is quite long enough.",
        )
        await tm.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-4/submit-for-review",
            json={
                "title": "Updated Title",
                "description": "Updated description that passes plan validity.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated Title"
        assert data["description"] == "Updated description that passes plan validity."
        assert data["status"] == "review"

    async def test_plan_json_sync_on_description_update(self, client, tm):
        """When plan_json exists, description update syncs through plan_json."""
        import json

        plan_data = {"plan": "original plan text", "tasks": []}
        task = make_task(
            task_id="proj-a:T-P0-5",
            project_id="proj-a",
            description="original plan text that is long.",
        )
        task.plan_json = json.dumps(plan_data)
        await tm.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-5/submit-for-review",
            json={"description": "updated plan text that is long enough."},
        )
        assert resp.status_code == 200

        # Verify plan_json was updated
        updated = await tm.get_task("proj-a:T-P0-5")
        assert updated is not None
        parsed = json.loads(updated.plan_json)
        assert parsed["plan"] == "updated plan text that is long enough."
