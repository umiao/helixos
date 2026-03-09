"""Tests for T-P0-24: Review gate UX -- PATCH task fields + 428 modal flow.

Verifies:
- PATCH /api/tasks/{id} endpoint for updating title/description
- 428 response includes gate_action and task_id for frontend modal
- BACKLOG -> REVIEW works with gate enabled (the modal submit flow)
- Gate OFF -> direct BACKLOG -> QUEUED works
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import Base
from src.events import EventBus
from src.models import Task
from src.process_manager import ProcessStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager
from tests.factories import make_config, make_task

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def test_engine():
    """In-memory async engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def test_session_factory(
    test_engine,
) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to in-memory engine."""
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
async def test_app(tmp_path: Path, test_session_factory):
    """Test FastAPI app with review gate enabled by default."""
    from fastapi import FastAPI

    from src.api import api_router
    from src.config import ProjectRegistry
    from src.env_loader import EnvLoader
    from src.events import sse_router
    from src.history_writer import HistoryWriter

    config = make_config(tmp_path)
    task_manager = TaskManager(test_session_factory)
    registry = ProjectRegistry(config)

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    env_loader = EnvLoader(env_path)

    event_bus = EventBus()
    history_writer = HistoryWriter(test_session_factory)

    scheduler = MagicMock(spec=Scheduler)
    scheduler.cancel_task = AsyncMock(return_value=None)
    scheduler.is_project_paused = MagicMock(return_value=False)
    scheduler.pause_project = AsyncMock()
    scheduler.resume_project = AsyncMock()

    # Review gate: enabled by default (True)
    _review_gate_state: dict[str, bool] = {}

    def _is_gate_enabled(project_id: str) -> bool:
        return _review_gate_state.get(project_id, True)

    async def _enable_gate(project_id: str) -> None:
        _review_gate_state[project_id] = True

    async def _disable_gate(project_id: str) -> None:
        _review_gate_state[project_id] = False

    scheduler.is_review_gate_enabled = MagicMock(side_effect=_is_gate_enabled)
    scheduler.enable_review_gate = AsyncMock(side_effect=_enable_gate)
    scheduler.disable_review_gate = AsyncMock(side_effect=_disable_gate)

    app = FastAPI(title="HelixOS Test", version="0.1.0")
    app.include_router(sse_router)
    app.include_router(api_router)

    app.state.config = config
    app.state.task_manager = task_manager
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
async def client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient for the test app."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def task_manager(test_app) -> TaskManager:
    """TaskManager from the test app."""
    return test_app.state.task_manager


@pytest.fixture
async def seeded_task(task_manager: TaskManager) -> Task:
    """Create a BACKLOG task with description."""
    task = make_task(
        task_id="proj-a:T-P0-1", project_id="proj-a",
        description="Original description",
    )
    return await task_manager.create_task(task)


# ---------------------------------------------------------------------------
# PATCH /api/tasks/{id} -- update task fields
# ---------------------------------------------------------------------------


class TestUpdateTaskFields:
    """Tests for PATCH /api/tasks/{task_id} (title/description updates)."""

    async def test_update_title(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should update the task title."""
        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}",
            json={"title": "Updated title"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated title"
        assert data["description"] == "Original description"

    async def test_update_description(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should update the task description."""
        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}",
            json={"description": "New description"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test task"
        assert data["description"] == "New description"

    async def test_update_both_fields(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should update both title and description at once."""
        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}",
            json={"title": "New title", "description": "New desc"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New title"
        assert data["description"] == "New desc"

    async def test_no_changes(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Sending same values should return task unchanged."""
        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}",
            json={"title": "Test task", "description": "Original description"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test task"

    async def test_empty_body(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Omitting all fields should return task unchanged."""
        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test task"

    async def test_not_found(self, client: AsyncClient):
        """Should return 404 for unknown task."""
        resp = await client.patch(
            "/api/tasks/nonexistent",
            json={"title": "x"},
        )
        assert resp.status_code == 404

    async def test_persists_across_reads(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Updated fields should persist when fetched again."""
        await client.patch(
            f"/api/tasks/{seeded_task.id}",
            json={"title": "Persisted title"},
        )
        resp = await client.get(f"/api/tasks/{seeded_task.id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Persisted title"


# ---------------------------------------------------------------------------
# 428 review gate response for frontend modal
# ---------------------------------------------------------------------------


class TestReviewGateModal:
    """Tests for the 428 review gate response that triggers the modal."""

    async def test_gate_on_blocks_backlog_to_queued(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Gate ON: BACKLOG -> QUEUED should return 428 with gate_action."""
        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}/status",
            json={"status": "queued"},
        )
        assert resp.status_code == 428
        data = resp.json()
        assert data["gate_action"] == "review_required"
        assert data["task_id"] == seeded_task.id
        assert "detail" in data

    async def test_428_response_shape(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """428 response should have all fields the frontend needs."""
        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}/status",
            json={"status": "queued"},
        )
        data = resp.json()
        # All three fields required by frontend
        assert "detail" in data
        assert "gate_action" in data
        assert "task_id" in data

    async def test_gate_off_allows_backlog_to_queued(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Gate OFF: BACKLOG -> QUEUED should succeed."""
        # Disable the review gate
        await client.patch("/api/projects/proj-a/review-gate?enabled=false")

        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}/status",
            json={"status": "queued"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"


# ---------------------------------------------------------------------------
# Full modal flow: edit + submit for review
# ---------------------------------------------------------------------------


class TestReviewSubmitFlow:
    """Tests for the full review submit flow (edit task, then BACKLOG -> REVIEW)."""

    async def test_backlog_to_review_with_gate_on(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Gate ON: BACKLOG -> REVIEW should still work (not blocked by gate)."""
        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "review"

    async def test_edit_then_submit_flow(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Full flow: edit title/description, then transition to REVIEW."""
        # Step 1: Edit task fields (description must be >= 20 chars for plan validity)
        edit_resp = await client.patch(
            f"/api/tasks/{seeded_task.id}",
            json={"title": "Reviewed task", "description": "Ready for review process"},
        )
        assert edit_resp.status_code == 200
        assert edit_resp.json()["title"] == "Reviewed task"

        # Step 2: Transition to REVIEW
        status_resp = await client.patch(
            f"/api/tasks/{seeded_task.id}/status",
            json={"status": "review"},
        )
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["status"] == "review"
        assert data["title"] == "Reviewed task"
        assert data["description"] == "Ready for review process"

    async def test_428_then_edit_then_review(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Full modal flow: attempt queued (428), edit, then submit for review."""
        # Step 1: Attempt BACKLOG -> QUEUED -> 428
        block_resp = await client.patch(
            f"/api/tasks/{seeded_task.id}/status",
            json={"status": "queued"},
        )
        assert block_resp.status_code == 428
        assert block_resp.json()["gate_action"] == "review_required"

        # Step 2: Edit task with a valid plan (>= 20 chars)
        edit_resp = await client.patch(
            f"/api/tasks/{seeded_task.id}",
            json={"title": "Revised title", "description": "Revised description for review submission"},
        )
        assert edit_resp.status_code == 200

        # Step 3: Submit for REVIEW instead
        review_resp = await client.patch(
            f"/api/tasks/{seeded_task.id}/status",
            json={"status": "review"},
        )
        assert review_resp.status_code == 200
        data = review_resp.json()
        assert data["status"] == "review"
        assert data["title"] == "Revised title"

    async def test_queued_to_review_with_gate_on(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """QUEUED task can be sent to REVIEW (context menu 'Send to Review')."""
        # Create a QUEUED task (gate off for initial transition)
        task = make_task(
            task_id="proj-a:T-P0-2",
            project_id="proj-a",
            local_task_id="T-P0-2",
            title="Queued task",
        )
        await task_manager.create_task(task)

        # Move to QUEUED with gate off
        await client.patch("/api/projects/proj-a/review-gate?enabled=false")
        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-2/status",
            json={"status": "queued"},
        )
        assert resp.status_code == 200

        # Re-enable gate
        await client.patch("/api/projects/proj-a/review-gate?enabled=true")

        # QUEUED -> REVIEW should work
        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-2/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "review"

    async def test_gate_off_no_modal(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Gate OFF: BACKLOG -> QUEUED should NOT trigger 428."""
        await client.patch("/api/projects/proj-a/review-gate?enabled=false")

        resp = await client.patch(
            f"/api/tasks/{seeded_task.id}/status",
            json={"status": "queued"},
        )
        # Should succeed directly, no 428
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
