"""Tests for the POST /api/projects/{project_id}/start-all-planned endpoint.

Covers:
1. Gate ON -> tasks move to REVIEW
2. Gate OFF -> tasks move to QUEUED
3. No planned tasks -> started=0
4. Concurrent request -> skipped via optimistic lock
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import (
    GitConfig,
    OrchestratorConfig,
    OrchestratorSettings,
    ProjectConfig,
    ReviewPipelineConfig,
)
from src.db import Base
from src.events import EventBus
from src.models import ExecutorType, PlanStatus, Task, TaskStatus
from src.process_manager import ProcessStatus
from src.process_monitor import ProcessMonitor
from src.scheduler import Scheduler
from src.task_manager import TaskManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> OrchestratorConfig:
    """Create a minimal OrchestratorConfig with one test project."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir(exist_ok=True)
    tasks_md = repo_path / "TASKS.md"
    tasks_md.write_text(
        "# Task Backlog\n\n## Active Tasks\n\n"
        "#### T-P0-1: Test task\n- Description\n\n"
        "## Completed Tasks\n",
        encoding="utf-8",
    )
    return OrchestratorConfig(
        orchestrator=OrchestratorSettings(
            state_db_path=tmp_path / "test.db",
            unified_env_path=tmp_path / ".env",
            global_concurrency_limit=3,
        ),
        projects={
            "proj-a": ProjectConfig(
                name="Project A",
                repo_path=repo_path,
                executor_type=ExecutorType.CODE,
                max_concurrency=1,
            ),
        },
        git=GitConfig(),
        review_pipeline=ReviewPipelineConfig(),
    )


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
async def test_session_factory(
    test_engine,
) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
async def test_app(tmp_path: Path, test_session_factory):
    """Create a test FastAPI app with mocked lifespan services."""
    from fastapi import FastAPI

    from src.api import api_router
    from src.config import ProjectRegistry
    from src.env_loader import EnvLoader
    from src.events import sse_router
    from src.history_writer import HistoryWriter

    config = _make_config(tmp_path)
    task_manager = TaskManager(test_session_factory)
    registry = ProjectRegistry(config)

    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    env_loader = EnvLoader(env_path)

    event_bus = EventBus()
    history_writer = HistoryWriter(test_session_factory)

    # Mock scheduler with review gate state
    scheduler = MagicMock(spec=Scheduler)
    scheduler.cancel_task = AsyncMock(return_value=None)
    scheduler.is_project_paused = MagicMock(return_value=False)
    scheduler.pause_project = AsyncMock()
    scheduler.resume_project = AsyncMock()

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

    mock_monitor = MagicMock(spec=ProcessMonitor)
    mock_monitor.get_active_processes.return_value = []
    app.state.process_monitor = mock_monitor

    # Store gate state for test manipulation
    app.state._review_gate_state = _review_gate_state

    yield app


@pytest.fixture
async def client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient for the test app."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_task(
    task_manager: TaskManager,
    task_id: str,
    plan_status: str = PlanStatus.READY,
    description: str = "A valid plan with enough characters to pass validity checks easily.",
) -> Task:
    """Insert a BACKLOG task with the given plan_status via upsert."""
    task = Task(
        id=task_id,
        project_id="proj-a",
        local_task_id=task_id.split(":")[-1],
        title=f"Task {task_id}",
        status=TaskStatus.BACKLOG,
        executor_type=ExecutorType.CODE,
        plan_status=plan_status,
        description=description,
    )
    await task_manager.upsert_task(task)
    return task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_on_tasks_move_to_review(test_app, client: AsyncClient) -> None:
    """Gate ON: planned tasks should move to REVIEW."""
    tm: TaskManager = test_app.state.task_manager
    # Gate is ON by default
    await _create_task(tm, "proj-a:T-P0-1")
    await _create_task(tm, "proj-a:T-P0-2")

    resp = await client.post("/api/projects/proj-a/start-all-planned")
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] == 2
    assert data["skipped"] == 0

    # Verify tasks are now in REVIEW
    t1 = await tm.get_task("proj-a:T-P0-1")
    t2 = await tm.get_task("proj-a:T-P0-2")
    assert t1 is not None and t1.status == TaskStatus.REVIEW
    assert t2 is not None and t2.status == TaskStatus.REVIEW


@pytest.mark.asyncio
async def test_gate_off_tasks_move_to_queued(test_app, client: AsyncClient) -> None:
    """Gate OFF: planned tasks should move to QUEUED."""
    tm: TaskManager = test_app.state.task_manager
    # Disable gate
    test_app.state._review_gate_state["proj-a"] = False

    await _create_task(tm, "proj-a:T-P0-1")

    resp = await client.post("/api/projects/proj-a/start-all-planned")
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] == 1
    assert data["skipped"] == 0

    t1 = await tm.get_task("proj-a:T-P0-1")
    assert t1 is not None and t1.status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_no_planned_tasks(test_app, client: AsyncClient) -> None:
    """No planned tasks -> started=0, skipped=0."""
    tm: TaskManager = test_app.state.task_manager
    # Create a backlog task WITHOUT ready plan
    await _create_task(tm, "proj-a:T-P0-1", plan_status=PlanStatus.NONE)

    resp = await client.post("/api/projects/proj-a/start-all-planned")
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] == 0
    assert data["skipped"] == 0


@pytest.mark.asyncio
async def test_concurrent_request_skipped_via_optimistic_lock(
    test_app, client: AsyncClient,
) -> None:
    """Concurrent edit -> task skipped with optimistic lock error."""
    tm: TaskManager = test_app.state.task_manager
    test_app.state._review_gate_state["proj-a"] = False

    task = await _create_task(tm, "proj-a:T-P0-1")

    # Simulate concurrent edit by updating the task's updated_at
    # (another request modifies the task between our list and update)
    await tm.update_status("proj-a:T-P0-1", TaskStatus.QUEUED)
    # Move it back to BACKLOG so it's eligible again, but updated_at changed
    await tm.update_status("proj-a:T-P0-1", TaskStatus.BACKLOG)

    # The endpoint fetches the task list (with original updated_at from before),
    # but updated_at was changed. However since we re-fetched with list_tasks,
    # the updated_at will be current. Let's test a true race by patching.
    from unittest.mock import patch

    original_list = tm.list_tasks

    async def _patched_list(*args, **kwargs):
        """Return task with stale updated_at."""
        tasks = await original_list(*args, **kwargs)
        for t in tasks:
            if t.id == "proj-a:T-P0-1":
                # Set a stale timestamp that won't match DB
                t.updated_at = task.updated_at
        return tasks

    with patch.object(tm, "list_tasks", side_effect=_patched_list):
        resp = await client.post("/api/projects/proj-a/start-all-planned")

    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] == 0
    assert data["skipped"] == 1
    assert data["skipped_details"][0]["reason"] == "concurrent_edit"


@pytest.mark.asyncio
async def test_project_not_found(client: AsyncClient) -> None:
    """Non-existent project -> 404."""
    resp = await client.post("/api/projects/nonexistent/start-all-planned")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_mixed_plan_statuses(test_app, client: AsyncClient) -> None:
    """Only tasks with plan_status=ready are started; others ignored."""
    tm: TaskManager = test_app.state.task_manager
    test_app.state._review_gate_state["proj-a"] = False

    await _create_task(tm, "proj-a:T-P0-1", plan_status=PlanStatus.READY)
    await _create_task(tm, "proj-a:T-P0-2", plan_status=PlanStatus.NONE)
    await _create_task(tm, "proj-a:T-P0-3", plan_status=PlanStatus.GENERATING)
    await _create_task(tm, "proj-a:T-P0-4", plan_status=PlanStatus.FAILED)

    resp = await client.post("/api/projects/proj-a/start-all-planned")
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] == 1
    assert data["skipped"] == 0

    t1 = await tm.get_task("proj-a:T-P0-1")
    t2 = await tm.get_task("proj-a:T-P0-2")
    assert t1 is not None and t1.status == TaskStatus.QUEUED
    assert t2 is not None and t2.status == TaskStatus.BACKLOG


@pytest.mark.asyncio
async def test_only_backlog_tasks_eligible(test_app, client: AsyncClient) -> None:
    """Tasks not in BACKLOG are not eligible even if plan_status=ready."""
    tm: TaskManager = test_app.state.task_manager
    test_app.state._review_gate_state["proj-a"] = False

    # Create a ready task, move it to QUEUED already
    await _create_task(tm, "proj-a:T-P0-1", plan_status=PlanStatus.READY)
    await tm.update_status("proj-a:T-P0-1", TaskStatus.QUEUED)

    resp = await client.post("/api/projects/proj-a/start-all-planned")
    assert resp.status_code == 200
    data = resp.json()
    assert data["started"] == 0


@pytest.mark.asyncio
async def test_sse_events_emitted(test_app, client: AsyncClient) -> None:
    """Verify SSE status_change events are emitted for started tasks."""
    tm: TaskManager = test_app.state.task_manager
    event_bus: EventBus = test_app.state.event_bus
    test_app.state._review_gate_state["proj-a"] = False

    emitted: list[tuple] = []
    original_emit = event_bus.emit

    def _capture_emit(event_type, task_id, data, **kwargs):
        emitted.append((event_type, task_id, data))
        original_emit(event_type, task_id, data, **kwargs)

    event_bus.emit = _capture_emit

    await _create_task(tm, "proj-a:T-P0-1")

    resp = await client.post("/api/projects/proj-a/start-all-planned")
    assert resp.status_code == 200

    status_events = [e for e in emitted if e[0] == "status_change"]
    assert len(status_events) == 1
    assert status_events[0][1] == "proj-a:T-P0-1"
    assert status_events[0][2]["status"] == "queued"
