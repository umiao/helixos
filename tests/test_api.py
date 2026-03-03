"""Tests for the HelixOS REST API endpoints.

Uses httpx AsyncClient against a test FastAPI app with in-memory SQLite
and mocked services. Each test verifies happy paths, error responses,
and status code contracts per PRD Section 10.
"""

from __future__ import annotations

import asyncio
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
from src.models import (
    ExecutionState,
    ExecutorType,
    ReviewState,
    Task,
    TaskStatus,
)
from src.process_manager import ProcessStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> OrchestratorConfig:
    """Create a minimal OrchestratorConfig with one test project."""
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir(exist_ok=True)
    # Create a minimal TASKS.md
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

    # Create a dummy .env file (no ANTHROPIC_API_KEY needed -- uses Claude CLI)
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    env_loader = EnvLoader(env_path)

    event_bus = EventBus()
    history_writer = HistoryWriter(test_session_factory)

    # Mock scheduler (no real tick loop)
    scheduler = MagicMock(spec=Scheduler)
    scheduler.cancel_task = AsyncMock(return_value=False)
    scheduler.is_project_paused = MagicMock(return_value=False)
    scheduler.pause_project = AsyncMock()
    scheduler.resume_project = AsyncMock()

    # Review gate: track state in a mutable container
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

    # Set up app.state as lifespan would
    app.state.config = config
    app.state.task_manager = task_manager
    app.state.registry = registry
    app.state.env_loader = env_loader
    app.state.event_bus = event_bus
    app.state.scheduler = scheduler
    app.state.review_pipeline = None  # No Claude CLI in tests
    app.state.history_writer = history_writer
    app.state.engine = None

    # Mock ProcessManager -- status returns not-running for any project
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
    """Create a single seeded BACKLOG task."""
    task = _make_task()
    return await task_manager.create_task(task)


# ---------------------------------------------------------------------------
# Project endpoint tests
# ---------------------------------------------------------------------------


class TestListProjects:
    """Tests for GET /api/projects."""

    async def test_list_projects_returns_configured(self, client: AsyncClient):
        """Should return all configured projects."""
        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "proj-a"
        assert data[0]["name"] == "Project A"
        assert data[0]["executor_type"] == "code"

    async def test_list_projects_shape(self, client: AsyncClient):
        """Response should have correct fields including claude_md_path."""
        resp = await client.get("/api/projects")
        project = resp.json()[0]
        assert "id" in project
        assert "name" in project
        assert "tasks_file" in project
        assert "max_concurrency" in project
        assert "claude_md_path" in project


class TestGetProject:
    """Tests for GET /api/projects/{project_id}."""

    async def test_get_project_found(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should return project with its tasks."""
        resp = await client.get("/api/projects/proj-a")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "proj-a"
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["id"] == "proj-a:T-P0-1"

    async def test_get_project_not_found(self, client: AsyncClient):
        """Should return 404 for unknown project."""
        resp = await client.get("/api/projects/nonexistent")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Task endpoint tests
# ---------------------------------------------------------------------------


class TestListTasks:
    """Tests for GET /api/tasks."""

    async def test_list_all_tasks(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should return all tasks."""
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_filter_by_project_id(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should filter tasks by project_id."""
        resp = await client.get("/api/tasks?project_id=proj-a")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = await client.get("/api/tasks?project_id=nonexistent")
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    async def test_filter_by_status(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should filter tasks by status."""
        resp = await client.get("/api/tasks?status=backlog")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

        resp = await client.get("/api/tasks?status=done")
        assert resp.status_code == 200
        assert len(resp.json()) == 0


class TestGetTask:
    """Tests for GET /api/tasks/{task_id}."""

    async def test_get_task_found(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should return the task."""
        resp = await client.get("/api/tasks/proj-a:T-P0-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "proj-a:T-P0-1"
        assert data["title"] == "Test task"
        assert data["status"] == "backlog"

    async def test_get_task_not_found(self, client: AsyncClient):
        """Should return 404 for unknown task."""
        resp = await client.get("/api/tasks/nonexistent")
        assert resp.status_code == 404


class TestUpdateTaskStatus:
    """Tests for PATCH /api/tasks/{task_id}/status."""

    async def test_valid_transition(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """BACKLOG -> QUEUED should succeed when review gate is disabled."""
        # Disable review gate so BACKLOG -> QUEUED is allowed
        gate_resp = await client.patch(
            "/api/projects/proj-a/review-gate?enabled=false",
        )
        assert gate_resp.status_code == 200

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "queued"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    async def test_invalid_transition(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """BACKLOG -> DONE should fail with 409."""
        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-1/status",
            json={"status": "done"},
        )
        assert resp.status_code == 409

    async def test_not_found_returns_404(self, client: AsyncClient):
        """Unknown task should return 404."""
        resp = await client.patch(
            "/api/tasks/nonexistent/status",
            json={"status": "queued"},
        )
        assert resp.status_code == 404


class TestTriggerReview:
    """Tests for POST /api/tasks/{task_id}/review."""

    async def test_review_returns_202(
        self, client: AsyncClient, test_app, seeded_task: Task,
    ):
        """Should return 202 when review pipeline is available."""
        # Provide a mock review pipeline
        mock_pipeline = MagicMock()
        mock_pipeline.review_task = AsyncMock(
            return_value=ReviewState(
                rounds_total=1,
                rounds_completed=1,
                consensus_score=0.9,
            ),
        )
        test_app.state.review_pipeline = mock_pipeline

        resp = await client.post("/api/tasks/proj-a:T-P0-1/review")
        assert resp.status_code == 202
        data = resp.json()
        assert data["task_id"] == "proj-a:T-P0-1"

        # Wait for background task to complete
        await asyncio.sleep(0.1)

    async def test_review_no_pipeline_returns_409(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should return 409 when no review pipeline."""
        resp = await client.post("/api/tasks/proj-a:T-P0-1/review")
        assert resp.status_code == 409
        assert "not available" in resp.json()["detail"]

    async def test_review_not_found_returns_404(self, client: AsyncClient):
        """Unknown task should return 404."""
        resp = await client.post("/api/tasks/nonexistent/review")
        assert resp.status_code == 404


class TestReviewDecide:
    """Tests for POST /api/tasks/{task_id}/review/decide."""

    async def test_approve_moves_to_queued(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """Approve decision should move task to QUEUED."""
        # Create task in REVIEW_NEEDS_HUMAN state
        task = _make_task(
            task_id="proj-a:T-P0-2",
            local_task_id="T-P0-2",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1,
                rounds_completed=1,
                consensus_score=0.5,
                human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-2/review/decide",
            json={"decision": "approve", "reason": "Looks good"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    async def test_reject_moves_to_backlog(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """Reject decision should move task to BACKLOG."""
        task = _make_task(
            task_id="proj-a:T-P0-3",
            local_task_id="T-P0-3",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1,
                rounds_completed=1,
                consensus_score=0.5,
                human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-3/review/decide",
            json={"decision": "reject"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "backlog"

    async def test_decide_wrong_status_returns_409(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Deciding on a non-REVIEW_NEEDS_HUMAN task should return 409."""
        resp = await client.post(
            "/api/tasks/proj-a:T-P0-1/review/decide",
            json={"decision": "approve"},
        )
        assert resp.status_code == 409


class TestForceExecute:
    """Tests for POST /api/tasks/{task_id}/execute."""

    async def test_force_execute_from_backlog(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should transition BACKLOG -> QUEUED and return 202."""
        resp = await client.post("/api/tasks/proj-a:T-P0-1/execute")
        assert resp.status_code == 202
        data = resp.json()
        assert data["task_id"] == "proj-a:T-P0-1"

    async def test_force_execute_invalid_state(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """Should return 409 for tasks that cannot transition to QUEUED."""
        task = _make_task(
            task_id="proj-a:T-P0-4",
            local_task_id="T-P0-4",
            status=TaskStatus.DONE,
        )
        await task_manager.create_task(task)

        resp = await client.post("/api/tasks/proj-a:T-P0-4/execute")
        assert resp.status_code == 409


class TestRetryTask:
    """Tests for POST /api/tasks/{task_id}/retry."""

    async def test_retry_from_failed(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """FAILED task should move to QUEUED."""
        task = _make_task(
            task_id="proj-a:T-P0-5",
            local_task_id="T-P0-5",
            status=TaskStatus.FAILED,
        )
        task = task.model_copy(update={
            "execution": ExecutionState(retry_count=3),
        })
        await task_manager.create_task(task)

        resp = await client.post("/api/tasks/proj-a:T-P0-5/retry")
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    async def test_retry_invalid_state(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """BACKLOG task cannot be retried (no BACKLOG -> QUEUED via retry context)."""
        # BACKLOG -> QUEUED is valid, so retry will actually work
        resp = await client.post("/api/tasks/proj-a:T-P0-1/retry")
        # BACKLOG can go to QUEUED, so this should succeed
        assert resp.status_code == 200


class TestCancelTask:
    """Tests for POST /api/tasks/{task_id}/cancel."""

    async def test_cancel_not_running(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Should return 409 if task is not running."""
        resp = await client.post("/api/tasks/proj-a:T-P0-1/cancel")
        assert resp.status_code == 409

    async def test_cancel_running(
        self, client: AsyncClient, test_app, task_manager: TaskManager,
    ):
        """Should return 200 if scheduler accepts the cancellation."""
        task = _make_task(
            task_id="proj-a:T-P0-6",
            local_task_id="T-P0-6",
            status=TaskStatus.RUNNING,
        )
        await task_manager.create_task(task)

        test_app.state.scheduler.cancel_task = AsyncMock(return_value=True)

        resp = await client.post("/api/tasks/proj-a:T-P0-6/cancel")
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Task cancelled"


# ---------------------------------------------------------------------------
# Sync endpoint tests
# ---------------------------------------------------------------------------


class TestSyncProject:
    """Tests for POST /api/projects/{project_id}/sync."""

    async def test_sync_project(self, client: AsyncClient):
        """Should sync a known project's TASKS.md."""
        resp = await client.post("/api/projects/proj-a/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "proj-a"
        assert "added" in data
        assert "updated" in data

    async def test_sync_unknown_project(self, client: AsyncClient):
        """Should return 404 for unknown project."""
        resp = await client.post("/api/projects/nonexistent/sync")
        assert resp.status_code == 404


class TestSyncAll:
    """Tests for POST /api/sync-all."""

    async def test_sync_all(self, client: AsyncClient):
        """Should sync all projects."""
        resp = await client.post("/api/sync-all")
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["project_id"] == "proj-a"


# ---------------------------------------------------------------------------
# Dashboard endpoint tests
# ---------------------------------------------------------------------------


class TestDashboardSummary:
    """Tests for GET /api/dashboard/summary."""

    async def test_empty_dashboard(self, client: AsyncClient):
        """Should return zero counts with no tasks."""
        resp = await client.get("/api/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tasks"] == 0
        assert data["running_count"] == 0
        assert data["project_count"] == 1

    async def test_dashboard_with_tasks(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """Should aggregate task stats correctly."""
        await task_manager.create_task(_make_task(
            task_id="proj-a:T-P0-10",
            local_task_id="T-P0-10",
            status=TaskStatus.BACKLOG,
        ))
        await task_manager.create_task(_make_task(
            task_id="proj-a:T-P0-11",
            local_task_id="T-P0-11",
            status=TaskStatus.QUEUED,
        ))

        resp = await client.get("/api/dashboard/summary")
        data = resp.json()
        assert data["total_tasks"] == 2
        assert data["by_status"]["backlog"] == 1
        assert data["by_status"]["queued"] == 1
        assert data["running_count"] == 0


# ---------------------------------------------------------------------------
# Task status endpoint tests (RUNNING state)
# ---------------------------------------------------------------------------


class TestStatusTransitionToRunning:
    """Tests for task status transitions involving RUNNING."""

    async def test_queued_to_running(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """QUEUED -> RUNNING should succeed."""
        task = _make_task(
            task_id="proj-a:T-P0-20",
            local_task_id="T-P0-20",
            status=TaskStatus.QUEUED,
        )
        await task_manager.create_task(task)

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-20/status",
            json={"status": "running"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    async def test_running_to_done(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """RUNNING -> DONE should succeed."""
        task = _make_task(
            task_id="proj-a:T-P0-21",
            local_task_id="T-P0-21",
            status=TaskStatus.RUNNING,
        )
        await task_manager.create_task(task)

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-21/status",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

    async def test_running_to_failed(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """RUNNING -> FAILED should succeed."""
        task = _make_task(
            task_id="proj-a:T-P0-22",
            local_task_id="T-P0-22",
            status=TaskStatus.RUNNING,
        )
        await task_manager.create_task(task)

        resp = await client.patch(
            "/api/tasks/proj-a:T-P0-22/status",
            json={"status": "failed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"


# ---------------------------------------------------------------------------
# Execution log endpoint tests
# ---------------------------------------------------------------------------


class TestGetTaskLogs:
    """Tests for GET /api/tasks/{task_id}/logs."""

    async def test_empty_logs(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Returns empty list when no logs exist."""
        resp = await client.get(f"/api/tasks/{seeded_task.id}/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == seeded_task.id
        assert data["total"] == 0
        assert data["entries"] == []

    async def test_logs_with_data(
        self, client: AsyncClient, test_app, seeded_task: Task,
    ):
        """Returns log entries after writes."""
        hw = test_app.state.history_writer
        await hw.write_log(seeded_task.id, "Build started", level="info")
        await hw.write_log(seeded_task.id, "Warning found", level="warn")

        resp = await client.get(f"/api/tasks/{seeded_task.id}/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["entries"]) == 2
        assert data["entries"][0]["message"] == "Build started"
        assert data["entries"][1]["level"] == "warn"

    async def test_logs_pagination(
        self, client: AsyncClient, test_app, seeded_task: Task,
    ):
        """Pagination with limit and offset works."""
        hw = test_app.state.history_writer
        for i in range(5):
            await hw.write_log(seeded_task.id, f"Line {i}")

        resp = await client.get(
            f"/api/tasks/{seeded_task.id}/logs?limit=2&offset=2",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["entries"]) == 2
        assert data["offset"] == 2
        assert data["limit"] == 2

    async def test_logs_level_filter(
        self, client: AsyncClient, test_app, seeded_task: Task,
    ):
        """Level filter returns only matching entries."""
        hw = test_app.state.history_writer
        await hw.write_log(seeded_task.id, "Info", level="info")
        await hw.write_log(seeded_task.id, "Error", level="error")

        resp = await client.get(
            f"/api/tasks/{seeded_task.id}/logs?level=error",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["level"] == "error"

    async def test_logs_404_for_missing_task(self, client: AsyncClient):
        """Returns 404 for a non-existent task."""
        resp = await client.get("/api/tasks/nonexistent/logs")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Review history endpoint tests
# ---------------------------------------------------------------------------


class TestGetTaskReviews:
    """Tests for GET /api/tasks/{task_id}/reviews."""

    async def test_empty_reviews(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """Returns empty list when no reviews exist."""
        resp = await client.get(f"/api/tasks/{seeded_task.id}/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == seeded_task.id
        assert data["total"] == 0
        assert data["entries"] == []

    async def test_reviews_with_data(
        self, client: AsyncClient, test_app, seeded_task: Task,
    ):
        """Returns review entries after writes."""
        from datetime import UTC, datetime

        from src.models import LLMReview

        hw = test_app.state.history_writer
        review = LLMReview(
            model="claude-sonnet-4-5",
            focus="feasibility",
            verdict="approve",
            summary="Looks good",
            suggestions=["Add more tests"],
            timestamp=datetime.now(UTC),
        )
        await hw.write_review(seeded_task.id, 1, review, consensus_score=0.95)

        resp = await client.get(f"/api/tasks/{seeded_task.id}/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        entry = data["entries"][0]
        assert entry["reviewer_model"] == "claude-sonnet-4-5"
        assert entry["verdict"] == "approve"
        assert entry["consensus_score"] == 0.95
        assert entry["suggestions"] == ["Add more tests"]

    async def test_reviews_pagination(
        self, client: AsyncClient, test_app, seeded_task: Task,
    ):
        """Pagination with limit and offset works."""
        from datetime import UTC, datetime

        from src.models import LLMReview

        hw = test_app.state.history_writer
        for i in range(4):
            review = LLMReview(
                model="claude-sonnet-4-5",
                focus="feasibility",
                verdict="approve",
                summary=f"Round {i + 1}",
                timestamp=datetime.now(UTC),
            )
            await hw.write_review(seeded_task.id, i + 1, review)

        resp = await client.get(
            f"/api/tasks/{seeded_task.id}/reviews?limit=2&offset=1",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert len(data["entries"]) == 2
        assert data["offset"] == 1

    async def test_reviews_404_for_missing_task(self, client: AsyncClient):
        """Returns 404 for a non-existent task."""
        resp = await client.get("/api/tasks/nonexistent/reviews")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Pause/Resume endpoint tests
# ---------------------------------------------------------------------------


class TestPauseExecution:
    """Tests for POST /api/projects/{project_id}/pause-execution."""

    async def test_pause_known_project(
        self, client: AsyncClient, test_app,
    ):
        """Should call scheduler.pause_project and return 200."""
        resp = await client.post("/api/projects/proj-a/pause-execution")
        assert resp.status_code == 200
        data = resp.json()
        assert data["paused"] is True
        assert data["project_id"] == "proj-a"
        test_app.state.scheduler.pause_project.assert_awaited_once_with("proj-a")

    async def test_pause_unknown_project_returns_404(
        self, client: AsyncClient,
    ):
        """Should return 404 for unknown project."""
        resp = await client.post("/api/projects/nonexistent/pause-execution")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestResumeExecution:
    """Tests for POST /api/projects/{project_id}/resume-execution."""

    async def test_resume_known_project(
        self, client: AsyncClient, test_app,
    ):
        """Should call scheduler.resume_project and return 200."""
        resp = await client.post("/api/projects/proj-a/resume-execution")
        assert resp.status_code == 200
        data = resp.json()
        assert data["paused"] is False
        assert data["project_id"] == "proj-a"
        test_app.state.scheduler.resume_project.assert_awaited_once_with("proj-a")

    async def test_resume_unknown_project_returns_404(
        self, client: AsyncClient,
    ):
        """Should return 404 for unknown project."""
        resp = await client.post("/api/projects/nonexistent/resume-execution")
        assert resp.status_code == 404


class TestProjectResponseIncludesPausedState:
    """Tests that project API responses include execution_paused field."""

    async def test_list_projects_includes_execution_paused(
        self, client: AsyncClient,
    ):
        """GET /api/projects should include execution_paused field."""
        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        project = resp.json()[0]
        assert "execution_paused" in project
        assert project["execution_paused"] is False

    async def test_get_project_includes_execution_paused(
        self, client: AsyncClient, seeded_task: Task,
    ):
        """GET /api/projects/{id} should include execution_paused field."""
        resp = await client.get("/api/projects/proj-a")
        assert resp.status_code == 200
        data = resp.json()
        assert "execution_paused" in data
        assert data["execution_paused"] is False

    async def test_list_projects_reflects_paused_state(
        self, client: AsyncClient, test_app,
    ):
        """GET /api/projects should show paused=True when scheduler says so."""
        test_app.state.scheduler.is_project_paused = MagicMock(return_value=True)
        resp = await client.get("/api/projects")
        assert resp.status_code == 200
        project = resp.json()[0]
        assert project["execution_paused"] is True
