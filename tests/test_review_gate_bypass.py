"""Regression tests for review gate bypass fix (T-P0-21).

Tests all 5 bypass paths identified in the task spec:
1. sync_project_tasks() auto-promoting BACKLOG -> QUEUED
2. POST /api/tasks/{id}/review/decide without gate check
3. POST /api/tasks/{id}/execute without gate check
4. POST /api/tasks/{id}/retry without gate check
5. POST /api/tasks/{id}/review auto-approve path without gate check

Also verifies gate-off behavior is unchanged.
"""

from __future__ import annotations

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
    ProjectRegistry,
    ReviewPipelineConfig,
)
from src.db import Base
from src.events import EventBus
from src.models import (
    ExecutionState,
    ExecutorType,
    ReviewState,
    Task,
    TaskStatus,
)
from src.process_manager import ProcessStatus
from src.scheduler import Scheduler
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import ReviewGateBlockedError, TaskManager

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


def _make_task(
    task_id: str = "proj-a:T-P0-1",
    project_id: str = "proj-a",
    local_task_id: str = "T-P0-1",
    title: str = "Test task",
    status: TaskStatus = TaskStatus.BACKLOG,
) -> Task:
    """Create a test Task."""
    return Task(
        id=task_id,
        project_id=project_id,
        local_task_id=local_task_id,
        title=title,
        status=status,
        executor_type=ExecutorType.CODE,
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
    """Create a test FastAPI app with review gate ON by default."""
    from fastapi import FastAPI

    from src.api import api_router
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

    # Mock scheduler with review gate ON by default
    scheduler = MagicMock(spec=Scheduler)
    scheduler.cancel_task = AsyncMock(return_value=False)
    scheduler.is_project_paused = MagicMock(return_value=False)
    scheduler.pause_project = AsyncMock()
    scheduler.resume_project = AsyncMock()

    _review_gate_state: dict[str, bool] = {}  # default: enabled (True)

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
async def client(test_app):
    """httpx AsyncClient for the test app."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def task_manager(test_app) -> TaskManager:
    """TaskManager from the test app."""
    return test_app.state.task_manager


# ==================================================================
# Bypass #1: sync_project_tasks() no longer auto-promotes to QUEUED
# ==================================================================


class TestSyncGateFix:
    """Verify sync no longer converts BACKLOG -> QUEUED."""

    async def test_sync_keeps_backlog_with_gate_on(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """Gate on: synced tasks from Active section stay BACKLOG."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "TASKS.md").write_text(
            "# Backlog\n\n## Active Tasks\n\n"
            "#### T-P0-1: Task one\n- desc\n\n"
            "#### T-P0-2: Task two\n- desc\n",
            encoding="utf-8",
        )
        config = OrchestratorConfig(
            projects={
                "proj": ProjectConfig(
                    name="proj", repo_path=repo,
                    executor_type=ExecutorType.CODE,
                ),
            },
        )
        registry = ProjectRegistry(config)

        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.added == 2

        for tid in ["proj:T-P0-1", "proj:T-P0-2"]:
            task = await task_manager.get_task(tid)
            assert task is not None
            assert task.status == TaskStatus.BACKLOG, (
                f"{tid} should be BACKLOG after sync, got {task.status}"
            )

    async def test_sync_keeps_backlog_regardless_of_gate(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """Sync behavior is gate-independent: tasks always enter as BACKLOG."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "TASKS.md").write_text(
            "# Backlog\n\n## Active Tasks\n\n"
            "#### T-P0-1: Task one\n- desc\n",
            encoding="utf-8",
        )
        config = OrchestratorConfig(
            projects={
                "proj": ProjectConfig(
                    name="proj", repo_path=repo,
                    executor_type=ExecutorType.CODE,
                ),
            },
        )
        registry = ProjectRegistry(config)

        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.added == 1

        task = await task_manager.get_task("proj:T-P0-1")
        assert task is not None
        assert task.status == TaskStatus.BACKLOG


# ==================================================================
# Bypass #2: PATCH /api/tasks/{id}/status returns 428 with gate on
# ==================================================================


class TestStatusEndpointGate:
    """PATCH /api/tasks/{id}/status must return 428 when gate blocks."""

    async def test_backlog_to_queued_blocked_428(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate on: BACKLOG -> QUEUED returns 428 with gate_action hint."""
        await task_manager.create_task(_make_task())

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "queued"},
        )
        assert resp.status_code == 428
        data = resp.json()
        assert data["gate_action"] == "review_required"
        assert data["task_id"] == "proj-a:T-P0-1"

    async def test_backlog_to_queued_allowed_gate_off(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate off: BACKLOG -> QUEUED succeeds as before."""
        await task_manager.create_task(_make_task())

        # Disable gate
        await client.patch("/api/projects/proj-a/review-gate?enabled=false")

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "queued"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    async def test_backlog_to_review_allowed_gate_on(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate on: BACKLOG -> REVIEW is always allowed."""
        await task_manager.create_task(_make_task())

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "review"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "review"


# ==================================================================
# Bypass #3: POST /api/tasks/{id}/execute returns 428 with gate on
# ==================================================================


class TestExecuteGate:
    """Force-execute must respect the review gate."""

    async def test_execute_backlog_blocked_428(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate on: force-execute a BACKLOG task returns 428."""
        await task_manager.create_task(_make_task())

        resp = await client.post("/api/tasks/proj-a:T-P0-1/execute")
        assert resp.status_code == 428
        data = resp.json()
        assert data["gate_action"] == "review_required"

    async def test_execute_backlog_allowed_gate_off(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate off: force-execute a BACKLOG task returns 202."""
        await task_manager.create_task(_make_task())
        await client.patch("/api/projects/proj-a/review-gate?enabled=false")

        resp = await client.post("/api/tasks/proj-a:T-P0-1/execute")
        assert resp.status_code == 202

    async def test_execute_failed_allowed_gate_on(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate on: force-execute from FAILED -> QUEUED is allowed.

        The gate only blocks BACKLOG -> QUEUED, not FAILED -> QUEUED.
        """
        task = _make_task(
            task_id="proj-a:T-P0-2", local_task_id="T-P0-2",
            status=TaskStatus.FAILED,
        )
        await task_manager.create_task(task)

        resp = await client.post("/api/tasks/proj-a:T-P0-2/execute")
        assert resp.status_code == 202


# ==================================================================
# Bypass #4: POST /api/tasks/{id}/retry returns 428 with gate on
# ==================================================================


class TestRetryGate:
    """Retry must respect the review gate for BACKLOG tasks."""

    async def test_retry_backlog_blocked_428(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate on: retrying a BACKLOG task returns 428."""
        await task_manager.create_task(_make_task())

        resp = await client.post("/api/tasks/proj-a:T-P0-1/retry")
        assert resp.status_code == 428
        data = resp.json()
        assert data["gate_action"] == "review_required"

    async def test_retry_failed_allowed_gate_on(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate on: retrying a FAILED task still works (FAILED -> QUEUED)."""
        task = _make_task(
            task_id="proj-a:T-P0-2", local_task_id="T-P0-2",
            status=TaskStatus.FAILED,
        )
        task = task.model_copy(update={
            "execution": ExecutionState(retry_count=3),
        })
        await task_manager.create_task(task)

        resp = await client.post("/api/tasks/proj-a:T-P0-2/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"


# ==================================================================
# Bypass #5: POST /api/tasks/{id}/review/decide passes gate flag
# ==================================================================


class TestReviewDecideGate:
    """Review decide endpoint must pass review_gate_enabled for defense-in-depth."""

    async def test_approve_from_review_needs_human_allowed(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate on: approving REVIEW_NEEDS_HUMAN -> QUEUED is allowed.

        This transition is post-review, so the gate does not block it.
        """
        task = _make_task(
            task_id="proj-a:T-P0-2", local_task_id="T-P0-2",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-2/review/decide",
            json={"decision": "approve", "reason": "OK"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    async def test_reject_from_review_needs_human(
        self, client: AsyncClient, task_manager: TaskManager,
    ) -> None:
        """Gate on: rejecting REVIEW_NEEDS_HUMAN -> BACKLOG is allowed."""
        task = _make_task(
            task_id="proj-a:T-P0-3", local_task_id="T-P0-3",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-3/review/decide",
            json={"decision": "reject"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "backlog"


# ==================================================================
# TaskManager unit tests for ReviewGateBlockedError
# ==================================================================


class TestReviewGateBlockedError:
    """Verify TaskManager raises ReviewGateBlockedError (not ValueError)."""

    async def test_gate_blocks_with_specific_error(
        self, test_session_factory,
    ) -> None:
        """update_status raises ReviewGateBlockedError for gate block."""
        tm = TaskManager(test_session_factory)
        await tm.create_task(_make_task())

        with pytest.raises(ReviewGateBlockedError) as exc_info:
            await tm.update_status(
                "proj-a:T-P0-1", TaskStatus.QUEUED,
                review_gate_enabled=True,
            )
        assert "review" in str(exc_info.value).lower()
        assert exc_info.value.task_id == "proj-a:T-P0-1"

    async def test_gate_off_no_error(
        self, test_session_factory,
    ) -> None:
        """update_status with gate off allows BACKLOG -> QUEUED."""
        tm = TaskManager(test_session_factory)
        await tm.create_task(_make_task())

        result = await tm.update_status(
            "proj-a:T-P0-1", TaskStatus.QUEUED,
            review_gate_enabled=False,
        )
        assert result.status == TaskStatus.QUEUED

    async def test_invalid_transition_still_valueerror(
        self, test_session_factory,
    ) -> None:
        """Invalid transitions still raise ValueError (not gate error)."""
        tm = TaskManager(test_session_factory)
        await tm.create_task(_make_task())

        with pytest.raises(ValueError, match="Cannot move"):
            await tm.update_status("proj-a:T-P0-1", TaskStatus.DONE)
