"""Tests for T-P0-26: Drag-to-REVIEW workflow -- transition-driven pipeline + review_status.

Covers:
  - review_status DB column lifecycle (idle -> running -> done/failed)
  - Transition-driven pipeline trigger (status change to REVIEW auto-enqueues)
  - Idempotent re-drag (REVIEW -> REVIEW with running pipeline)
  - Backward transition resets review_status to idle
  - Retry endpoint (POST /api/tasks/{id}/review)
  - Pipeline unavailable -> immediate failure
  - review_status in API responses
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import Base
from src.events import EventBus
from src.models import (
    ReviewState,
    TaskStatus,
)
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
async def session_factory(
    test_engine,
) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
async def task_manager(session_factory) -> TaskManager:
    """TaskManager instance."""
    return TaskManager(session_factory)


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

    # Review gate: default disabled for drag-to-review tests
    _review_gate_state: dict[str, bool] = {"proj-a": False}

    def _is_gate_enabled(project_id: str) -> bool:
        return _review_gate_state.get(project_id, False)

    scheduler.is_review_gate_enabled = MagicMock(side_effect=_is_gate_enabled)
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
    app.state.review_pipeline = None  # No Claude CLI by default
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
# Test: review_status in API responses
# ===========================================================================


class TestReviewStatusInResponse:
    """Verify review_status appears in task API responses."""

    async def test_get_task_includes_review_status(self, client, tm):
        """GET /api/tasks/{id} should include review_status field."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        resp = await client.get("/api/tasks/proj-a:T-P0-1")
        assert resp.status_code == 200
        data = resp.json()
        assert "review_status" in data
        assert data["review_status"] == "idle"

    async def test_list_tasks_includes_review_status(self, client, tm):
        """GET /api/tasks should include review_status on each task."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["review_status"] == "idle"


# ===========================================================================
# Test: review_status lifecycle via TaskManager
# ===========================================================================


class TestReviewStatusLifecycle:
    """Test review_status transitions managed by TaskManager."""

    async def test_backlog_to_review_sets_running(self, task_manager):
        """Transitioning BACKLOG -> REVIEW should set review_status to running."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await task_manager.create_task(task)

        updated = await task_manager.update_status(
            "proj-a:T-P0-1", TaskStatus.REVIEW,
        )
        assert updated.review_status == "running"

    async def test_review_to_backlog_resets_idle(self, task_manager):
        """Transitioning REVIEW -> BACKLOG should reset review_status to idle."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW, review_status="running")
        await task_manager.create_task(task)

        updated = await task_manager.update_status(
            "proj-a:T-P0-1", TaskStatus.BACKLOG,
        )
        assert updated.review_status == "idle"

    async def test_set_review_status_to_done(self, task_manager):
        """set_review_status should update the review_status column."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW, review_status="running")
        await task_manager.create_task(task)

        await task_manager.set_review_status("proj-a:T-P0-1", "done")
        result = await task_manager.get_task("proj-a:T-P0-1")
        assert result is not None
        assert result.review_status == "done"

    async def test_set_review_status_to_failed(self, task_manager):
        """set_review_status should update to failed."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW, review_status="running")
        await task_manager.create_task(task)

        await task_manager.set_review_status("proj-a:T-P0-1", "failed")
        result = await task_manager.get_task("proj-a:T-P0-1")
        assert result is not None
        assert result.review_status == "failed"

    async def test_set_review_status_not_found(self, task_manager):
        """set_review_status on nonexistent task should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await task_manager.set_review_status("nonexistent", "done")

    async def test_queued_to_review_sets_running(self, task_manager):
        """QUEUED -> REVIEW should also set review_status to running."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.QUEUED)
        await task_manager.create_task(task)

        updated = await task_manager.update_status(
            "proj-a:T-P0-1", TaskStatus.REVIEW,
        )
        assert updated.review_status == "running"


# ===========================================================================
# Test: Transition-driven pipeline trigger via API
# ===========================================================================


class TestTransitionDrivenPipeline:
    """Test that PATCH /api/tasks/{id}/status triggers pipeline on REVIEW entry."""

    async def test_drag_to_review_triggers_pipeline(
        self, client, test_app, tm,
    ):
        """Dragging BACKLOG -> REVIEW should enqueue review pipeline."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock(
            return_value=ReviewState(
                rounds_total=1,
                rounds_completed=1,
                consensus_score=0.9,
            ),
        )
        test_app.state.review_pipeline = mock_pipeline

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "review"
        assert data["review_status"] == "running"

        # Wait for background task to complete
        await asyncio.sleep(0.2)

        # Pipeline should have been called
        mock_pipeline.review_task.assert_called_once()

    async def test_drag_to_review_no_pipeline_fails_immediately(
        self, client, test_app, tm,
    ):
        """When no Claude CLI, dragging to REVIEW should set review_status=failed."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        # review_pipeline is None by default (no Claude CLI)
        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "review"

        # Wait for the background task to set review_status=failed
        await asyncio.sleep(0.2)

        # Verify review_status is now "failed"
        get_resp = await client.get("/api/tasks/proj-a:T-P0-1")
        assert get_resp.json()["review_status"] == "failed"

    async def test_pipeline_success_transitions_to_auto_approved(
        self, client, test_app, tm,
    ):
        """Pipeline success with score >= threshold -> REVIEW_AUTO_APPROVED."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock(
            return_value=ReviewState(
                rounds_total=1,
                rounds_completed=1,
                consensus_score=0.95,
                human_decision_needed=False,
            ),
        )
        test_app.state.review_pipeline = mock_pipeline

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200

        # Wait for pipeline to complete
        await asyncio.sleep(0.3)

        get_resp = await client.get("/api/tasks/proj-a:T-P0-1")
        data = get_resp.json()
        assert data["status"] == "review_auto_approved"
        assert data["review_status"] == "done"

    async def test_pipeline_success_transitions_to_needs_human(
        self, client, test_app, tm,
    ):
        """Pipeline with low score -> REVIEW_NEEDS_HUMAN."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock(
            return_value=ReviewState(
                rounds_total=1,
                rounds_completed=1,
                consensus_score=0.5,
                human_decision_needed=True,
                decision_points=["Low confidence"],
            ),
        )
        test_app.state.review_pipeline = mock_pipeline

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200

        await asyncio.sleep(0.3)

        get_resp = await client.get("/api/tasks/proj-a:T-P0-1")
        data = get_resp.json()
        assert data["status"] == "review_needs_human"
        assert data["review_status"] == "done"

    async def test_pipeline_failure_stays_in_review_with_failed(
        self, client, test_app, tm,
    ):
        """Pipeline exception -> task stays REVIEW, review_status=failed."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock(
            side_effect=RuntimeError("Claude CLI crashed"),
        )
        test_app.state.review_pipeline = mock_pipeline

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200

        await asyncio.sleep(0.3)

        get_resp = await client.get("/api/tasks/proj-a:T-P0-1")
        data = get_resp.json()
        assert data["status"] == "review"
        assert data["review_status"] == "failed"

    async def test_non_review_transition_no_pipeline(
        self, client, test_app, tm,
    ):
        """Transitioning to non-REVIEW status should not trigger pipeline."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW)
        await tm.create_task(task)

        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock()
        test_app.state.review_pipeline = mock_pipeline

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "backlog"},
        )
        assert resp.status_code == 200
        mock_pipeline.review_task.assert_not_called()


# ===========================================================================
# Test: Idempotent re-drag
# ===========================================================================


class TestIdempotentReDrag:
    """Test that re-dragging REVIEW -> REVIEW does not duplicate pipeline."""

    async def test_review_to_review_is_invalid(self, task_manager):
        """REVIEW -> REVIEW is not in VALID_TRANSITIONS (same status)."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW, review_status="running")
        await task_manager.create_task(task)

        # REVIEW -> REVIEW should fail because it's not in the transitions map
        with pytest.raises(ValueError, match="Cannot move"):
            await task_manager.update_status(
                "proj-a:T-P0-1", TaskStatus.REVIEW,
            )


# ===========================================================================
# Test: Retry endpoint
# ===========================================================================


class TestRetryReviewEndpoint:
    """Tests for POST /api/tasks/{task_id}/review (retry-only)."""

    async def test_retry_from_failed(self, client, test_app, tm):
        """Retry when review_status=failed should return 202."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW, review_status="failed")
        await tm.create_task(task)

        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock(
            return_value=ReviewState(
                rounds_total=1, rounds_completed=1, consensus_score=0.9,
            ),
        )
        test_app.state.review_pipeline = mock_pipeline

        resp = await client.post("/api/tasks/proj-a:T-P0-1/review")
        assert resp.status_code == 202
        assert resp.json()["task_id"] == "proj-a:T-P0-1"

        await asyncio.sleep(0.2)

    async def test_retry_from_idle(self, client, test_app, tm):
        """Retry when review_status=idle should work (manual trigger)."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.BACKLOG, review_status="idle")
        await tm.create_task(task)

        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock(
            return_value=ReviewState(
                rounds_total=1, rounds_completed=1, consensus_score=0.9,
            ),
        )
        test_app.state.review_pipeline = mock_pipeline

        resp = await client.post("/api/tasks/proj-a:T-P0-1/review")
        assert resp.status_code == 202

        await asyncio.sleep(0.2)

    async def test_retry_rejects_running(self, client, tm):
        """Retry when review_status=running should return 409."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW, review_status="running")
        await tm.create_task(task)

        resp = await client.post("/api/tasks/proj-a:T-P0-1/review")
        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"]

    async def test_retry_rejects_done(self, client, tm):
        """Retry when review_status=done should return 409."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW, review_status="done")
        await tm.create_task(task)

        resp = await client.post("/api/tasks/proj-a:T-P0-1/review")
        assert resp.status_code == 409
        assert "Cannot retry" in resp.json()["detail"]

    async def test_retry_not_found(self, client):
        """Retry on nonexistent task should return 404."""
        resp = await client.post("/api/tasks/nonexistent/review")
        assert resp.status_code == 404


# ===========================================================================
# Test: Backward transition resets review_status
# ===========================================================================


class TestBackwardTransitionReset:
    """Test review_status reset on backward transitions."""

    async def test_review_to_backlog_resets(self, client, tm):
        """REVIEW -> BACKLOG via API should reset review_status to idle."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a", status=TaskStatus.REVIEW, review_status="failed")
        await tm.create_task(task)

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "backlog"},
        )
        assert resp.status_code == 200
        assert resp.json()["review_status"] == "idle"

    async def test_review_auto_approved_to_backlog_resets(self, client, tm):
        """REVIEW_AUTO_APPROVED -> BACKLOG should reset review_status."""
        task = make_task(
            task_id="proj-a:T-P0-1", project_id="proj-a",
            status=TaskStatus.REVIEW_AUTO_APPROVED, review_status="done",
            )
        await tm.create_task(task)

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "backlog"},
        )
        assert resp.status_code == 200
        assert resp.json()["review_status"] == "idle"


# ===========================================================================
# Test: SSE events emitted
# ===========================================================================


class TestSSEEventsEmitted:
    """Test that SSE events are emitted during review lifecycle."""

    async def test_review_failed_emits_alert(
        self, client, test_app, tm, event_bus,
    ):
        """When pipeline is unavailable, SSE alert and review_failed should fire."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        # Collect events
        events: list[tuple[str, str]] = []
        original_emit = event_bus.emit

        def capture_emit(event_type: str, task_id: str, data: dict, **kwargs: object) -> None:
            events.append((event_type, task_id))
            original_emit(event_type, task_id, data, **kwargs)

        event_bus.emit = capture_emit

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200

        await asyncio.sleep(0.3)

        event_types = [e[0] for e in events]
        assert "alert" in event_types
        assert "review_failed" in event_types

    async def test_review_started_emitted_with_pipeline(
        self, client, test_app, tm, event_bus,
    ):
        """When pipeline available, review_started SSE should fire."""
        task = make_task(task_id="proj-a:T-P0-1", project_id="proj-a")
        await tm.create_task(task)

        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock(
            return_value=ReviewState(
                rounds_total=1, rounds_completed=1, consensus_score=0.9,
            ),
        )
        test_app.state.review_pipeline = mock_pipeline

        events: list[tuple[str, str]] = []
        original_emit = event_bus.emit

        def capture_emit(event_type: str, task_id: str, data: dict, **kwargs: object) -> None:
            events.append((event_type, task_id))
            original_emit(event_type, task_id, data, **kwargs)

        event_bus.emit = capture_emit

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200

        await asyncio.sleep(0.3)

        event_types = [e[0] for e in events]
        assert "review_started" in event_types
        assert "status_change" in event_types


# ===========================================================================
# Test: DB migration (review_status column)
# ===========================================================================


class TestReviewStatusMigration:
    """Test that review_status column is auto-migrated."""

    async def test_review_status_has_default_idle(self, task_manager):
        """New tasks should have review_status=idle by default."""
        task = make_task(
            task_id="proj-a:T-P0-99",
            project_id="proj-a",
            local_task_id="T-P0-99",
            title="Migration test",
        )
        await task_manager.create_task(task)

        result = await task_manager.get_task("proj-a:T-P0-99")
        assert result is not None
        assert result.review_status == "idle"
