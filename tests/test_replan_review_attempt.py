"""Tests for T-P0-122: Fix replan review_attempt reset to 1 instead of incrementing.

Verifies that after a replan, the auto-enqueued review pipeline uses
``max_existing_attempt + 1`` instead of hardcoded ``1``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import (
    OrchestratorConfig,
    OrchestratorSettings,
    PlanValidationConfig,
    ProjectConfig,
)
from src.db import Base
from src.models import ReviewState, TaskStatus
from src.task_manager import TaskManager
from tests.factories import make_task

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
async def test_engine():
    """In-memory async engine for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(test_engine):
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
async def task_manager(session_factory):
    """Provide a TaskManager backed by the in-memory DB."""
    return TaskManager(session_factory)


@pytest.fixture
def mock_config() -> OrchestratorConfig:
    """Minimal OrchestratorConfig for tests."""
    return OrchestratorConfig(
        orchestrator=OrchestratorSettings(
            plan_validation=PlanValidationConfig(),
        ),
        projects={
            "proj-a": ProjectConfig(
                name="Test Project",
                repo_path=None,
                tasks_file="TASKS.md",
            ),
        },
    )


@pytest.fixture
async def app(task_manager, session_factory, mock_config):
    """Create test FastAPI app with required app.state attributes."""
    from fastapi import FastAPI

    from src.api import api_router
    from src.events import EventBus, sse_router

    application = FastAPI()
    application.include_router(api_router)
    application.include_router(sse_router)

    application.state.task_manager = task_manager
    application.state.session_factory = session_factory
    application.state.event_bus = EventBus()
    application.state.scheduler = MagicMock()
    application.state.scheduler.is_review_gate_enabled = MagicMock(return_value=False)
    application.state.history_writer = AsyncMock()
    application.state.review_pipeline = MagicMock()
    application.state.registry = MagicMock()
    application.state.config = mock_config

    mock_project = MagicMock()
    mock_project.repo_path = None
    mock_project.tasks_file = "TASKS.md"
    application.state.registry.get_project = MagicMock(return_value=mock_project)

    yield application

    await asyncio.sleep(0.1)


@pytest.fixture
async def client(app):
    """AsyncClient for making test requests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ==================================================================
# Tests
# ==================================================================


class TestReplanReviewAttemptIncrement:
    """Verify replan auto-enqueues review with correct attempt number."""

    async def test_replan_uses_next_review_attempt(
        self, client: AsyncClient, task_manager: TaskManager, app,
    ):
        """After replan, review_attempt should be max_existing + 1, not 1."""
        task = make_task(
            task_id="proj-a:T-P0-RA1",
            project_id="proj-a",
            local_task_id="T-P0-RA1",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
            replan_attempt=0,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        # Simulate that 2 review attempts already exist in history
        hw = app.state.history_writer
        hw.get_max_review_attempt = AsyncMock(return_value=2)

        captured_kwargs: dict = {}

        def capture_enqueue(**kwargs):
            """Capture _enqueue_review_pipeline call args."""
            captured_kwargs.update(kwargs)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=True), \
             patch("src.routes.reviews.generate_task_plan", new_callable=AsyncMock) as mock_gen, \
             patch("src.routes.reviews._enqueue_review_pipeline") as mock_enqueue:
            mock_gen.return_value = {
                "plan": "improved plan",
                "steps": [{"step": "do it", "files": []}],
                "acceptance_criteria": ["AC1"],
            }
            resp = await client.post(
                "/api/tasks/proj-a:T-P0-RA1/review/decide",
                json={"decision": "replan", "reason": "Needs more detail"},
            )
            assert resp.status_code == 200

            # Let the background task run
            await asyncio.sleep(0.3)

            # _enqueue_review_pipeline should have been called
            assert mock_enqueue.called, (
                "_enqueue_review_pipeline was not called after replan"
            )
            call_kwargs = mock_enqueue.call_args
            # review_attempt should be 3 (max_existing=2, so next=3)
            if call_kwargs.kwargs:
                actual_attempt = call_kwargs.kwargs.get("review_attempt")
            else:
                # positional: (task_manager, review_pipeline, event_bus,
                #              task, task_id, review_attempt, ...)
                actual_attempt = call_kwargs.args[5] if len(call_kwargs.args) > 5 else None

            assert actual_attempt == 3, (
                f"Expected review_attempt=3 (max 2 + 1), got {actual_attempt}"
            )

    async def test_replan_first_review_attempt_is_1(
        self, client: AsyncClient, task_manager: TaskManager, app,
    ):
        """If no prior reviews exist, post-replan review_attempt should be 1."""
        task = make_task(
            task_id="proj-a:T-P0-RA2",
            project_id="proj-a",
            local_task_id="T-P0-RA2",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
            replan_attempt=0,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        # No prior review attempts
        hw = app.state.history_writer
        hw.get_max_review_attempt = AsyncMock(return_value=0)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=True), \
             patch("src.routes.reviews.generate_task_plan", new_callable=AsyncMock) as mock_gen, \
             patch("src.routes.reviews._enqueue_review_pipeline") as mock_enqueue:
            mock_gen.return_value = {
                "plan": "plan",
                "steps": [{"step": "s1", "files": []}],
                "acceptance_criteria": ["AC1"],
            }
            resp = await client.post(
                "/api/tasks/proj-a:T-P0-RA2/review/decide",
                json={"decision": "replan"},
            )
            assert resp.status_code == 200

            await asyncio.sleep(0.3)

            assert mock_enqueue.called
            call_kwargs = mock_enqueue.call_args
            if call_kwargs.kwargs:
                actual_attempt = call_kwargs.kwargs.get("review_attempt")
            else:
                actual_attempt = call_kwargs.args[5] if len(call_kwargs.args) > 5 else None

            assert actual_attempt == 1, (
                f"Expected review_attempt=1 (no prior), got {actual_attempt}"
            )

    async def test_replan_review_attempt_after_multiple_replans(
        self, client: AsyncClient, task_manager: TaskManager, app,
    ):
        """After 2nd replan (attempt=1), review_attempt should still use DB max."""
        task = make_task(
            task_id="proj-a:T-P0-RA3",
            project_id="proj-a",
            local_task_id="T-P0-RA3",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
            replan_attempt=1,  # Already replanned once
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        # 5 prior review attempts from multiple review rounds
        hw = app.state.history_writer
        hw.get_max_review_attempt = AsyncMock(return_value=5)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=True), \
             patch("src.routes.reviews.generate_task_plan", new_callable=AsyncMock) as mock_gen, \
             patch("src.routes.reviews._enqueue_review_pipeline") as mock_enqueue:
            mock_gen.return_value = {
                "plan": "plan v3",
                "steps": [{"step": "s1", "files": []}],
                "acceptance_criteria": ["AC1"],
            }
            resp = await client.post(
                "/api/tasks/proj-a:T-P0-RA3/review/decide",
                json={"decision": "replan", "reason": "Still not good enough"},
            )
            assert resp.status_code == 200

            await asyncio.sleep(0.3)

            assert mock_enqueue.called
            call_kwargs = mock_enqueue.call_args
            if call_kwargs.kwargs:
                actual_attempt = call_kwargs.kwargs.get("review_attempt")
            else:
                actual_attempt = call_kwargs.args[5] if len(call_kwargs.args) > 5 else None

            assert actual_attempt == 6, (
                f"Expected review_attempt=6 (max 5 + 1), got {actual_attempt}"
            )
