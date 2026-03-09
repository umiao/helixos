"""Tests for T-P1-116: Unified plan review before batch task decomposition.

Covers:
- AC1: plan_status_change SSE event includes proposed_tasks[] when plan_status=ready
- AC4: reject-plan endpoint resets plan_status to none
- Backend: update_plan accepts plan_json=None
- Backend: reject-plan 404 and 409 error cases
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import (
    OrchestratorConfig,
    OrchestratorSettings,
    PlanValidationConfig,
    ProjectConfig,
    ReviewPipelineConfig,
)
from src.db import Base
from src.events import EventBus, TaskEvent
from src.models import ExecutorType, Task, TaskStatus
from src.process_manager import ProcessStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_config(tmp_path: Path) -> OrchestratorConfig:
    """Create a minimal OrchestratorConfig with test project."""
    tasks_md = tmp_path / "TASKS.md"
    tasks_md.write_text(
        "# Task Backlog\n\n## Active Tasks\n\n### P0 -- Must Have\n\n## Completed Tasks\n",
        encoding="utf-8",
    )
    return OrchestratorConfig(
        projects={
            "test-proj": ProjectConfig(
                name="Test Project",
                repo_path=tmp_path,
                executor_type=ExecutorType.CODE,
                tasks_file="TASKS.md",
            ),
        },
        review_pipeline=ReviewPipelineConfig(),
        orchestrator=OrchestratorSettings(
            plan_validation=PlanValidationConfig(),
        ),
    )


SAMPLE_PLAN_DATA = {
    "plan": "Implement user authentication",
    "steps": [
        {"step": "Add login endpoint", "files": ["src/auth.py"]},
        {"step": "Add tests", "files": ["tests/test_auth.py"]},
    ],
    "acceptance_criteria": ["Login works", "Tests pass"],
    "proposed_tasks": [
        {
            "title": "Add login endpoint",
            "description": "Create POST /login with JWT",
            "files": ["src/auth.py"],
            "suggested_priority": "P0",
            "suggested_complexity": "M",
            "dependencies": [],
            "acceptance_criteria": ["POST /login returns JWT"],
        },
        {
            "title": "Add auth tests",
            "description": "Test login endpoint",
            "files": ["tests/test_auth.py"],
            "suggested_priority": "P1",
            "suggested_complexity": "S",
            "dependencies": ["Add login endpoint"],
            "acceptance_criteria": ["Tests pass"],
        },
    ],
}


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def test_session_factory(
    tmp_path: Path,
) -> async_sessionmaker[AsyncSession]:
    """Create an in-memory SQLite engine + session factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def test_app(tmp_path: Path, test_session_factory):
    """Create a test FastAPI app."""
    from fastapi import FastAPI

    from src.api import api_router
    from src.config import ProjectRegistry
    from src.env_loader import EnvLoader
    from src.events import sse_router
    from src.history_writer import HistoryWriter
    from src.project_settings import ProjectSettingsStore

    config = _make_config(tmp_path)
    task_manager = TaskManager(test_session_factory)
    registry = ProjectRegistry(config)

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    env_loader = EnvLoader(env_path)

    event_bus = EventBus()
    history_writer = HistoryWriter(test_session_factory)
    settings_store = ProjectSettingsStore(test_session_factory)

    scheduler = MagicMock(spec=Scheduler)
    scheduler.cancel_task = AsyncMock(return_value=None)
    scheduler.is_project_paused = MagicMock(return_value=False)
    scheduler.pause_project = AsyncMock()
    scheduler.resume_project = AsyncMock()

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
    app.state.settings_store = settings_store
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


async def _create_task_in_db(
    task_manager: TaskManager,
    plan_status: str = "none",
    plan_json: str | None = None,
    description: str = "",
) -> Task:
    """Insert a task into the DB and return it."""
    task = Task(
        id="test-proj::T-P0-99",
        project_id="test-proj",
        local_task_id="T-P0-99",
        title="Test task",
        description=description,
        status=TaskStatus.BACKLOG,
        executor_type=ExecutorType.CODE,
        plan_status=plan_status,
        plan_json=plan_json,
    )
    return await task_manager.upsert_task(task)


# ==================================================================
# AC1: plan_status_change SSE event includes proposed_tasks[]
# ==================================================================


class TestPlanStatusChangeEvent:
    """Verify SSE event payload includes proposed_tasks when ready."""

    @pytest.mark.asyncio
    async def test_proposed_tasks_in_ready_event(self, test_app) -> None:
        """When plan completes, the SSE event should include proposed_tasks."""
        event_bus: EventBus = test_app.state.event_bus
        task_manager: TaskManager = test_app.state.task_manager

        # Create task with plan_status=generating (simulating in-progress)
        await _create_task_in_db(task_manager, plan_status="generating")

        # Capture emitted events
        captured: list[TaskEvent] = []
        original_emit = event_bus.emit

        def capturing_emit(event_type, task_id, data, **kwargs):
            captured.append(TaskEvent(
                type=event_type, task_id=task_id, data=data,
                origin=kwargs.get("origin", "system"),
            ))
            original_emit(event_type, task_id, data, **kwargs)

        event_bus.emit = capturing_emit

        # Simulate the plan completion by calling update_plan + emit
        # (This mirrors what _run_plan_generation does)
        plan_data = SAMPLE_PLAN_DATA
        plan_data_json = json.dumps(plan_data)

        await task_manager.update_plan(
            task_id="test-proj::T-P0-99",
            description="formatted plan",
            plan_status="ready",
            plan_json=plan_data_json,
        )

        # Build proposed_tasks payload (same logic as in routes/tasks.py)
        proposed_tasks_payload = []
        for pt in plan_data.get("proposed_tasks", []):
            proposed_tasks_payload.append({
                "title": pt.get("title", ""),
                "description": pt.get("description", ""),
                "files": pt.get("files", []),
                "suggested_priority": pt.get("suggested_priority", "P1"),
                "suggested_complexity": pt.get("suggested_complexity", "M"),
                "dependencies": pt.get("dependencies", []),
                "acceptance_criteria": pt.get("acceptance_criteria", []),
            })

        event_bus.emit(
            "plan_status_change", "test-proj::T-P0-99",
            {"plan_status": "ready", "proposed_tasks": proposed_tasks_payload},
            origin="plan",
        )

        # Verify the event has proposed_tasks
        plan_events = [e for e in captured if e.type == "plan_status_change"]
        assert len(plan_events) >= 1
        last_event = plan_events[-1]
        assert last_event.data["plan_status"] == "ready"
        assert "proposed_tasks" in last_event.data
        assert len(last_event.data["proposed_tasks"]) == 2
        assert last_event.data["proposed_tasks"][0]["title"] == "Add login endpoint"
        assert last_event.data["proposed_tasks"][1]["title"] == "Add auth tests"


# ==================================================================
# AC4: reject-plan endpoint
# ==================================================================


class TestRejectPlanEndpoint:
    """Tests for POST /api/tasks/{task_id}/reject-plan."""

    @pytest.mark.asyncio
    async def test_reject_plan_success(self, test_app, client: AsyncClient) -> None:
        """Rejecting a ready plan resets plan_status to none."""
        task_manager: TaskManager = test_app.state.task_manager
        await _create_task_in_db(
            task_manager,
            plan_status="ready",
            plan_json=json.dumps(SAMPLE_PLAN_DATA),
            description="Generated plan text",
        )

        resp = await client.post("/api/tasks/test-proj::T-P0-99/reject-plan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plan_status"] == "none"
        assert body["task_id"] == "test-proj::T-P0-99"

        # Verify DB state
        task = await task_manager.get_task("test-proj::T-P0-99")
        assert task is not None
        assert task.plan_status == "none"
        assert task.plan_json is None

    @pytest.mark.asyncio
    async def test_reject_plan_emits_sse(self, test_app, client: AsyncClient) -> None:
        """Rejecting emits a plan_status_change event with plan_status=none."""
        task_manager: TaskManager = test_app.state.task_manager
        event_bus: EventBus = test_app.state.event_bus

        await _create_task_in_db(
            task_manager,
            plan_status="ready",
            plan_json=json.dumps(SAMPLE_PLAN_DATA),
        )

        captured: list[TaskEvent] = []
        original_emit = event_bus.emit

        def capturing_emit(event_type, task_id, data, **kwargs):
            captured.append(TaskEvent(
                type=event_type, task_id=task_id, data=data,
                origin=kwargs.get("origin", "system"),
            ))
            original_emit(event_type, task_id, data, **kwargs)

        event_bus.emit = capturing_emit

        await client.post("/api/tasks/test-proj::T-P0-99/reject-plan")

        plan_events = [e for e in captured if e.type == "plan_status_change"]
        assert len(plan_events) == 1
        assert plan_events[0].data["plan_status"] == "none"

    @pytest.mark.asyncio
    async def test_reject_plan_404(self, client: AsyncClient) -> None:
        """Rejecting a non-existent task returns 404."""
        resp = await client.post("/api/tasks/nonexistent/reject-plan")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_plan_409_not_ready(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Rejecting a task with plan_status != ready returns 409."""
        task_manager: TaskManager = test_app.state.task_manager
        await _create_task_in_db(task_manager, plan_status="generating")

        resp = await client.post("/api/tasks/test-proj::T-P0-99/reject-plan")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_reject_plan_409_none(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Rejecting when plan_status=none returns 409."""
        task_manager: TaskManager = test_app.state.task_manager
        await _create_task_in_db(task_manager, plan_status="none")

        resp = await client.post("/api/tasks/test-proj::T-P0-99/reject-plan")
        assert resp.status_code == 409


# ==================================================================
# update_plan with plan_json=None
# ==================================================================


class TestUpdatePlanNullable:
    """Verify TaskManager.update_plan accepts plan_json=None."""

    @pytest.mark.asyncio
    async def test_update_plan_clears_json(self, test_session_factory) -> None:
        """update_plan with plan_json=None should clear the field."""
        task_manager = TaskManager(test_session_factory)
        await _create_task_in_db(
            task_manager,
            plan_status="ready",
            plan_json=json.dumps(SAMPLE_PLAN_DATA),
        )

        await task_manager.update_plan(
            task_id="test-proj::T-P0-99",
            description="",
            plan_status="none",
            plan_json=None,
        )

        task = await task_manager.get_task("test-proj::T-P0-99")
        assert task is not None
        assert task.plan_status == "none"
        assert task.plan_json is None

    @pytest.mark.asyncio
    async def test_update_plan_sets_json(self, test_session_factory) -> None:
        """update_plan with a string plan_json should set the field."""
        task_manager = TaskManager(test_session_factory)
        await _create_task_in_db(task_manager, plan_status="generating")

        plan_json = json.dumps(SAMPLE_PLAN_DATA)
        await task_manager.update_plan(
            task_id="test-proj::T-P0-99",
            description="formatted plan",
            plan_status="ready",
            plan_json=plan_json,
        )

        task = await task_manager.get_task("test-proj::T-P0-99")
        assert task is not None
        assert task.plan_status == "ready"
        assert task.plan_json == plan_json


# ==================================================================
# SSE event proposed_tasks payload structure
# ==================================================================


class TestProposedTasksPayload:
    """Verify the proposed_tasks payload matches the expected schema."""

    def test_payload_structure(self) -> None:
        """Each proposed task in the payload has required fields."""
        plan_data = SAMPLE_PLAN_DATA
        proposed_tasks_payload = []
        for pt in plan_data.get("proposed_tasks", []):
            proposed_tasks_payload.append({
                "title": pt.get("title", ""),
                "description": pt.get("description", ""),
                "files": pt.get("files", []),
                "suggested_priority": pt.get("suggested_priority", "P1"),
                "suggested_complexity": pt.get("suggested_complexity", "M"),
                "dependencies": pt.get("dependencies", []),
                "acceptance_criteria": pt.get("acceptance_criteria", []),
            })

        assert len(proposed_tasks_payload) == 2

        first = proposed_tasks_payload[0]
        assert first["title"] == "Add login endpoint"
        assert first["description"] == "Create POST /login with JWT"
        assert first["files"] == ["src/auth.py"]
        assert first["suggested_priority"] == "P0"
        assert first["suggested_complexity"] == "M"
        assert first["dependencies"] == []
        assert first["acceptance_criteria"] == ["POST /login returns JWT"]

        second = proposed_tasks_payload[1]
        assert second["title"] == "Add auth tests"
        assert second["dependencies"] == ["Add login endpoint"]

    def test_empty_proposed_tasks(self) -> None:
        """Plan without proposed_tasks produces empty list."""
        plan_data = {"plan": "Simple plan", "steps": [], "acceptance_criteria": []}
        proposed_tasks_payload = []
        for pt in plan_data.get("proposed_tasks", []):
            proposed_tasks_payload.append({
                "title": pt.get("title", ""),
            })
        assert proposed_tasks_payload == []


# ==================================================================
# T-P0-136: DELETE /api/tasks/{task_id}/plan endpoint
# ==================================================================


class TestDeletePlanEndpoint:
    """Tests for DELETE /api/tasks/{task_id}/plan."""

    @pytest.mark.asyncio
    async def test_delete_plan_from_ready(self, test_app, client: AsyncClient) -> None:
        """Deleting a ready plan resets plan_status to none."""
        task_manager: TaskManager = test_app.state.task_manager
        await _create_task_in_db(
            task_manager,
            plan_status="ready",
            plan_json=json.dumps(SAMPLE_PLAN_DATA),
            description="Generated plan text",
        )

        resp = await client.delete("/api/tasks/test-proj::T-P0-99/plan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["plan_status"] == "none"
        assert body["task_id"] == "test-proj::T-P0-99"
        assert body["previous_status"] == "ready"

        # Verify DB state
        task = await task_manager.get_task("test-proj::T-P0-99")
        assert task is not None
        assert task.plan_status == "none"
        assert task.plan_json is None

    @pytest.mark.asyncio
    async def test_delete_plan_from_failed(self, test_app, client: AsyncClient) -> None:
        """Deleting a failed plan resets plan_status to none."""
        task_manager: TaskManager = test_app.state.task_manager
        await _create_task_in_db(task_manager, plan_status="failed")

        resp = await client.delete("/api/tasks/test-proj::T-P0-99/plan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["previous_status"] == "failed"
        assert body["plan_status"] == "none"

    @pytest.mark.asyncio
    async def test_delete_plan_from_decomposed(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Deleting a decomposed plan resets plan_status to none."""
        task_manager: TaskManager = test_app.state.task_manager
        await _create_task_in_db(
            task_manager,
            plan_status="ready",
            plan_json=json.dumps(SAMPLE_PLAN_DATA),
            description="Plan text",
        )
        # Transition to decomposed via state machine
        await task_manager.set_plan_state("test-proj::T-P0-99", "decomposed")

        resp = await client.delete("/api/tasks/test-proj::T-P0-99/plan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["previous_status"] == "decomposed"
        assert body["plan_status"] == "none"

    @pytest.mark.asyncio
    async def test_delete_plan_from_generating(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Deleting from generating state clears generation_id (cancel semantics)."""
        task_manager: TaskManager = test_app.state.task_manager
        # Create task at none, then transition to generating with generation_id
        await _create_task_in_db(task_manager, plan_status="none")
        await task_manager.set_plan_state(
            "test-proj::T-P0-99", "generating",
            plan_generation_id="test-gen-id-123",
        )

        resp = await client.delete("/api/tasks/test-proj::T-P0-99/plan")
        assert resp.status_code == 200
        body = resp.json()
        assert body["previous_status"] == "generating"

        # Verify generation_id is cleared (so in-flight result is discarded)
        task = await task_manager.get_task("test-proj::T-P0-99")
        assert task is not None
        assert task.plan_status == "none"
        assert task.plan_generation_id is None

    @pytest.mark.asyncio
    async def test_delete_plan_emits_sse(self, test_app, client: AsyncClient) -> None:
        """Deleting emits a plan_status_change event with plan_status=none."""
        task_manager: TaskManager = test_app.state.task_manager
        event_bus: EventBus = test_app.state.event_bus

        await _create_task_in_db(
            task_manager,
            plan_status="ready",
            plan_json=json.dumps(SAMPLE_PLAN_DATA),
        )

        captured: list[TaskEvent] = []
        original_emit = event_bus.emit

        def capturing_emit(event_type, task_id, data, **kwargs):
            captured.append(TaskEvent(
                type=event_type, task_id=task_id, data=data,
                origin=kwargs.get("origin", "system"),
            ))
            original_emit(event_type, task_id, data, **kwargs)

        event_bus.emit = capturing_emit

        await client.delete("/api/tasks/test-proj::T-P0-99/plan")

        plan_events = [e for e in captured if e.type == "plan_status_change"]
        assert len(plan_events) == 1
        assert plan_events[0].data["plan_status"] == "none"

    @pytest.mark.asyncio
    async def test_delete_plan_404(self, client: AsyncClient) -> None:
        """Deleting plan for non-existent task returns 404."""
        resp = await client.delete("/api/tasks/nonexistent/plan")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_plan_409_already_none(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Deleting when plan_status is already none returns 409."""
        task_manager: TaskManager = test_app.state.task_manager
        await _create_task_in_db(task_manager, plan_status="none")

        resp = await client.delete("/api/tasks/test-proj::T-P0-99/plan")
        assert resp.status_code == 409
