"""Tests for AI-assisted task enrichment (src/enrichment.py).

Tests cover:
- _parse_enrichment with valid/invalid/malformed JSON
- enrich_task_title with mocked SDK (success + failure)
- is_claude_cli_available pre-flight check (SDK import)
- POST /api/tasks/enrich endpoint (success, SDK unavailable 503, error 503)
- POST /api/tasks/{id}/generate-plan endpoint (success, 404, 503)
- Enrichment timeout configuration and behavior
- Zombie plan status cleanup
- Structured error responses in API endpoints
- Conditional enrichment (skip when description exists)
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
from src.enrichment import (
    PlanGenerationError,
    PlanGenerationErrorType,
    _parse_enrichment,
    enrich_task_title,
    generate_task_plan,
    is_claude_cli_available,
)
from src.models import ExecutorType
from src.process_manager import ProcessStatus
from src.scheduler import Scheduler
from src.sdk_adapter import ClaudeEvent, ClaudeEventType
from src.task_manager import TaskManager

# ------------------------------------------------------------------
# Helpers -- SDK event builders
# ------------------------------------------------------------------

# Reusable valid plan data for endpoint tests that need to pass structural
# validation but aren't testing the plan content itself.
_VALID_STEPS = [{"step": "Implement feature", "files": ["src/main.py"]}]
_VALID_AC = ["Feature works as expected"]


async def _mock_sdk_events(
    *events: ClaudeEvent,
) -> AsyncIterator[ClaudeEvent]:
    """Create a mock async iterator yielding ClaudeEvent objects."""
    for event in events:
        yield event


def _make_enrichment_events(
    description: str, priority: str,
) -> list[ClaudeEvent]:
    """Create ClaudeEvent list simulating enrichment result."""
    return [
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output={"description": description, "priority": priority},
        ),
    ]


def _make_plan_events(
    plan: str,
    steps: list[dict],
    acceptance_criteria: list[str],
) -> list[ClaudeEvent]:
    """Create ClaudeEvent list simulating plan generation result."""
    return [
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output={
                "plan": plan,
                "steps": steps,
                "acceptance_criteria": acceptance_criteria,
            },
        ),
    ]


def _make_error_event(message: str) -> ClaudeEvent:
    """Create a ClaudeEvent for an SDK error."""
    return ClaudeEvent(
        type=ClaudeEventType.ERROR,
        error_message=message,
    )


def _make_config(tmp_path: Path) -> OrchestratorConfig:
    """Create a minimal OrchestratorConfig for API tests."""
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


# ------------------------------------------------------------------
# Unit tests: _parse_enrichment
# ------------------------------------------------------------------


class TestParseEnrichment:
    """Tests for the _parse_enrichment function."""

    def test_valid_json(self) -> None:
        """Parse valid enrichment JSON."""
        text = json.dumps({"description": "Add user login", "priority": "P0"})
        result = _parse_enrichment(text)
        assert result["description"] == "Add user login"
        assert result["priority"] == "P0"

    def test_all_priorities(self) -> None:
        """All valid priorities are accepted."""
        for p in ("P0", "P1", "P2"):
            text = json.dumps({"description": "desc", "priority": p})
            result = _parse_enrichment(text)
            assert result["priority"] == p

    def test_invalid_priority_defaults_to_p1(self) -> None:
        """Invalid priority falls back to P1."""
        text = json.dumps({"description": "desc", "priority": "HIGH"})
        result = _parse_enrichment(text)
        assert result["priority"] == "P1"

    def test_missing_fields_use_defaults(self) -> None:
        """Missing fields fall back to defaults."""
        text = json.dumps({})
        result = _parse_enrichment(text)
        assert result["description"] == ""
        assert result["priority"] == "P1"

    def test_invalid_json_returns_defaults(self) -> None:
        """Non-JSON text returns safe defaults."""
        result = _parse_enrichment("not valid json")
        assert result["description"] == ""
        assert result["priority"] == "P1"

    def test_empty_string(self) -> None:
        """Empty string returns defaults."""
        result = _parse_enrichment("")
        assert result["description"] == ""
        assert result["priority"] == "P1"

    def test_non_string_description_rejected(self) -> None:
        """Non-string description is rejected by Pydantic, falls back to defaults."""
        text = json.dumps({"description": 42, "priority": "P0"})
        result = _parse_enrichment(text)
        assert result["description"] == ""
        assert result["priority"] == "P1"

    def test_non_string_priority_rejected(self) -> None:
        """Non-string priority is rejected by Pydantic, falls back to defaults."""
        text = json.dumps({"description": "desc", "priority": 0})
        result = _parse_enrichment(text)
        assert result["priority"] == "P1"
        assert result["description"] == ""


# ------------------------------------------------------------------
# Unit tests: is_claude_cli_available
# ------------------------------------------------------------------


class TestClaudeCliAvailable:
    """Tests for the is_claude_cli_available pre-flight check (SDK import)."""

    def test_available(self) -> None:
        """Returns True when claude_agent_sdk is importable."""
        with patch.dict(sys.modules, {"claude_agent_sdk": MagicMock()}):
            assert is_claude_cli_available() is True

    def test_unavailable(self) -> None:
        """Returns False when claude_agent_sdk is not importable."""
        with patch.dict(sys.modules, {"claude_agent_sdk": None}):
            assert is_claude_cli_available() is False


# ------------------------------------------------------------------
# Unit tests: enrich_task_title (async)
# ------------------------------------------------------------------


class TestEnrichTaskTitle:
    """Tests for the enrich_task_title async function (SDK-based)."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful enrichment returns description and priority."""
        events = _make_enrichment_events(
            "Implement user authentication with JWT", "P0",
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            result = await enrich_task_title("Add auth")
            assert result["description"] == "Implement user authentication with JWT"
            assert result["priority"] == "P0"

    @pytest.mark.asyncio
    async def test_sdk_error_raises(self) -> None:
        """SDK error event raises PlanGenerationError."""
        events = [_make_error_event("SDK error message")]

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ), pytest.raises(PlanGenerationError, match="Claude SDK error"):
            await enrich_task_title("Something")

    @pytest.mark.asyncio
    async def test_malformed_structured_output_uses_defaults(self) -> None:
        """Malformed structured_output uses safe defaults."""
        events = [
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                result_text="not valid json",
                structured_output=None,
            ),
        ]

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            result = await enrich_task_title("Some title")
            assert result["description"] == ""
            assert result["priority"] == "P1"

    @pytest.mark.asyncio
    async def test_enrichment_disables_cli_hooks(self) -> None:
        """Enrichment agent uses setting_sources=[] to disable CLI hooks."""
        events = _make_enrichment_events("desc", "P1")

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ) as mock_query:
            await enrich_task_title("Some task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert options.setting_sources == [], (
                f"Expected setting_sources=[] but got {options.setting_sources}"
            )


# ------------------------------------------------------------------
# API endpoint tests: POST /api/tasks/enrich
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
async def test_session_factory(
    test_engine,
) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
async def test_app(tmp_path: Path, test_session_factory):
    """Create a test FastAPI app for enrichment tests."""
    from fastapi import FastAPI

    from src.api import api_router
    from src.config import ProjectRegistry
    from src.env_loader import EnvLoader
    from src.events import EventBus, sse_router
    from src.history_writer import HistoryWriter

    config = _make_config(tmp_path)
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


class TestEnrichEndpoint:
    """Tests for POST /api/tasks/enrich."""

    @pytest.mark.asyncio
    async def test_success(self, client: AsyncClient) -> None:
        """Successful enrichment returns 200 with description + priority."""
        events = _make_enrichment_events(
            "Add user authentication flow with OAuth2", "P0",
        )

        with (
            patch(
                "src.routes.tasks.is_claude_cli_available", return_value=True,
            ),
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
        ):
            resp = await client.post(
                "/api/tasks/enrich",
                json={"title": "Add auth"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["description"] == "Add user authentication flow with OAuth2"
            assert data["priority"] == "P0"

    @pytest.mark.asyncio
    async def test_cli_unavailable_503(self, client: AsyncClient) -> None:
        """Returns 503 when Claude SDK is not available."""
        with patch(
            "src.routes.tasks.is_claude_cli_available", return_value=False,
        ):
            resp = await client.post(
                "/api/tasks/enrich",
                json={"title": "Something"},
            )
            assert resp.status_code == 503
            assert "not installed" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_cli_error_503(self, client: AsyncClient) -> None:
        """Returns 503 when Claude SDK returns error."""
        events = [_make_error_event("SDK error")]

        with (
            patch(
                "src.routes.tasks.is_claude_cli_available", return_value=True,
            ),
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
        ):
            resp = await client.post(
                "/api/tasks/enrich",
                json={"title": "Something"},
            )
            assert resp.status_code == 503
            assert "error" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_empty_title_422(self, client: AsyncClient) -> None:
        """Returns 422 for empty title (Pydantic min_length=1 validation)."""
        resp = await client.post(
            "/api/tasks/enrich",
            json={"title": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_title_422(self, client: AsyncClient) -> None:
        """Returns 422 for missing title field."""
        resp = await client.post(
            "/api/tasks/enrich",
            json={},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_enrichment_p1_default(self, client: AsyncClient) -> None:
        """Invalid priority from SDK falls back to P1."""
        events = [
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                structured_output={"description": "desc", "priority": "CRITICAL"},
            ),
        ]

        with (
            patch(
                "src.routes.tasks.is_claude_cli_available", return_value=True,
            ),
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
        ):
            resp = await client.post(
                "/api/tasks/enrich",
                json={"title": "Fix bug"},
            )
            assert resp.status_code == 200
            assert resp.json()["priority"] == "P1"


# ------------------------------------------------------------------
# API endpoint tests: POST /api/tasks/{id}/generate-plan
# ------------------------------------------------------------------


class TestGeneratePlanEndpoint:
    """Tests for POST /api/tasks/{task_id}/generate-plan (async 202)."""

    @pytest.mark.asyncio
    async def test_task_not_found_404(self, client: AsyncClient) -> None:
        """Returns 404 for non-existent task."""
        with patch(
            "src.routes.tasks.is_claude_cli_available", return_value=True,
        ):
            resp = await client.post(
                "/api/tasks/nonexistent-id/generate-plan",
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cli_unavailable_503(self, client: AsyncClient) -> None:
        """Returns 503 when Claude SDK is not available."""
        with patch(
            "src.routes.tasks.is_claude_cli_available", return_value=False,
        ):
            resp = await client.post(
                "/api/tasks/any-id/generate-plan",
            )
            assert resp.status_code == 503
            assert "not installed" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_success_returns_202(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Successful plan generation returns 202 immediately, background task completes."""
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        events = _make_plan_events(
            "Add authentication flow",
            [{"step": "Create auth module", "files": ["src/auth.py"]}],
            ["Login returns token"],
        )

        with (
            patch(
                "src.routes.tasks.is_claude_cli_available", return_value=True,
            ),
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
        ):
            resp = await client.post(
                f"/api/tasks/{task.id}/generate-plan",
            )
            assert resp.status_code == 202
            data = resp.json()
            assert data["task_id"] == task.id
            assert data["plan_status"] == "generating"

            # Wait for background task to complete (patch must stay active)
            await asyncio.sleep(0.3)

        # Verify task.description was updated by background task
        updated_task = await task_manager.get_task(task.id)
        assert updated_task is not None
        assert "Add authentication flow" in updated_task.description
        assert updated_task.plan_status == "ready"

    @pytest.mark.asyncio
    async def test_cli_error_sets_failed(
        self, test_app, client: AsyncClient,
    ) -> None:
        """SDK error in background sets plan_status to 'failed'."""
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        events = [_make_error_event("SDK error")]

        with (
            patch(
                "src.routes.tasks.is_claude_cli_available", return_value=True,
            ),
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
        ):
            resp = await client.post(
                f"/api/tasks/{task.id}/generate-plan",
            )
            assert resp.status_code == 202

            # Wait for background task to fail (patch must stay active)
            await asyncio.sleep(0.3)

        updated = await task_manager.get_task(task.id)
        assert updated is not None
        assert updated.plan_status == "failed"

    @pytest.mark.asyncio
    async def test_idempotency_guard_409(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Returns 409 when plan_status is already 'generating'."""
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        # Manually set plan_status to generating
        task.plan_status = "generating"
        await task_manager.update_task(task)

        with patch(
            "src.routes.tasks.is_claude_cli_available", return_value=True,
        ):
            resp = await client.post(
                f"/api/tasks/{task.id}/generate-plan",
            )
            assert resp.status_code == 409
            assert "already in progress" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_plan_status_in_task_response(
        self, test_app, client: AsyncClient,
    ) -> None:
        """plan_status field is included in task API responses."""
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        resp = await client.get(f"/api/tasks/{task.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "plan_status" in data
        assert data["plan_status"] == "none"

    @pytest.mark.asyncio
    async def test_sse_events_emitted(
        self, test_app, client: AsyncClient,
    ) -> None:
        """SSE plan_status_change events are emitted during generation."""
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        event_bus = test_app.state.event_bus
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        # Collect emitted events
        emitted: list[tuple[str, str, dict]] = []
        original_emit = event_bus.emit

        def capture_emit(event_type: str, task_id: str, data: object, **kwargs: object) -> None:
            emitted.append((event_type, task_id, data))
            original_emit(event_type, task_id, data, **kwargs)

        event_bus.emit = capture_emit

        with (
            patch(
                "src.routes.tasks.is_claude_cli_available", return_value=True,
            ),
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
        ):
            resp = await client.post(
                f"/api/tasks/{task.id}/generate-plan",
            )
            assert resp.status_code == 202

            # Wait for background task to complete (patch must stay active)
            await asyncio.sleep(0.3)

        # Check for plan_status_change events
        status_events = [
            e for e in emitted if e[0] == "plan_status_change"
        ]
        assert len(status_events) >= 2  # generating + ready
        assert status_events[0][2]["plan_status"] == "generating"
        assert status_events[-1][2]["plan_status"] == "ready"

        # Check for log events with source="plan"
        log_events = [
            e for e in emitted
            if e[0] == "log" and isinstance(e[2], dict) and e[2].get("source") == "plan"
        ]
        assert len(log_events) >= 1


# ------------------------------------------------------------------
# Config tests: enrichment_timeout_minutes
# ------------------------------------------------------------------


class TestEnrichmentTimeoutConfig:
    """Tests for ReviewPipelineConfig.enrichment_timeout_minutes."""

    def test_default_is_60(self) -> None:
        """enrichment_timeout_minutes defaults to 60."""
        config = ReviewPipelineConfig()
        assert config.enrichment_timeout_minutes == 60

    def test_custom_value(self) -> None:
        """enrichment_timeout_minutes can be customized."""
        config = ReviewPipelineConfig(enrichment_timeout_minutes=30)
        assert config.enrichment_timeout_minutes == 30

    def test_zero_disables(self) -> None:
        """enrichment_timeout_minutes=0 is valid (disables timeout)."""
        config = ReviewPipelineConfig(enrichment_timeout_minutes=0)
        assert config.enrichment_timeout_minutes == 0


# ------------------------------------------------------------------
# Unit tests: enrichment timeout behavior
# ------------------------------------------------------------------


class TestEnrichmentTimeout:
    """Tests for timeout behavior in enrichment SDK calls."""

    @pytest.mark.asyncio
    async def test_enrich_task_title_timeout(self) -> None:
        """enrich_task_title raises PlanGenerationError on timeout."""
        # Simulate an SDK query that never completes
        async def _hanging_query() -> AsyncIterator[ClaudeEvent]:
            await asyncio.sleep(10)  # Hang forever (will be timed out)
            yield ClaudeEvent(type=ClaudeEventType.RESULT)  # Never reached

        with (
            patch(
                "src.enrichment.run_claude_query",
                return_value=_hanging_query(),
            ),
            patch(
                "src.enrichment.asyncio.timeout",
                side_effect=TimeoutError(),
            ),
            pytest.raises(PlanGenerationError, match="timed out") as exc_info,
        ):
            await enrich_task_title("Title", timeout_minutes=1)

        assert exc_info.value.error_type == PlanGenerationErrorType.TIMEOUT

    @pytest.mark.asyncio
    async def test_generate_task_plan_timeout(self) -> None:
        """generate_task_plan raises PlanGenerationError on overall timeout."""
        # Patch asyncio.timeout to raise TimeoutError immediately
        with (
            patch(
                "src.enrichment.asyncio.timeout",
                side_effect=TimeoutError(),
            ),
            pytest.raises(PlanGenerationError, match="timed out") as exc_info,
        ):
            await generate_task_plan("Title", timeout_minutes=1)

        assert exc_info.value.error_type == PlanGenerationErrorType.TIMEOUT

    @pytest.mark.asyncio
    async def test_zero_timeout_disables(self) -> None:
        """timeout_minutes=0 passes timeout=None (no timeout) for enrich."""
        events = _make_enrichment_events("desc", "P0")

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ), patch(
            "src.enrichment.asyncio.timeout",
            wraps=asyncio.timeout,
        ) as mock_timeout:
            await enrich_task_title("Title", timeout_minutes=0)
            # timeout=None means no timeout
            mock_timeout.assert_called_once_with(None)


# ------------------------------------------------------------------
# Startup zombie cleanup tests
# ------------------------------------------------------------------


class TestZombiePlanStatusCleanup:
    """Tests for _reset_zombie_plan_status startup cleanup."""

    @pytest.mark.asyncio
    async def test_resets_generating_to_failed(
        self, test_app,
    ) -> None:
        """Tasks stuck with plan_status='generating' are reset to 'failed'."""
        from src.api import _reset_zombie_plan_status
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        # Set to generating (simulating crash)
        task.plan_status = "generating"
        await task_manager.update_task(task)

        count = await _reset_zombie_plan_status(task_manager)
        assert count == 1

        updated = await task_manager.get_task(task.id)
        assert updated is not None
        assert updated.plan_status == "failed"

    @pytest.mark.asyncio
    async def test_no_zombies(
        self, test_app,
    ) -> None:
        """No tasks reset when none have plan_status='generating'."""
        from src.api import _reset_zombie_plan_status

        task_manager: TaskManager = test_app.state.task_manager
        count = await _reset_zombie_plan_status(task_manager)
        assert count == 0


# ------------------------------------------------------------------
# Structured error responses in API endpoints
# ------------------------------------------------------------------


class TestStructuredErrorInApi:
    """Tests for structured error responses in API endpoints."""

    @pytest.mark.asyncio
    async def test_enrich_503_has_error_type(
        self, client: AsyncClient,
    ) -> None:
        """Enrich 503 response includes error_type and retryable fields."""
        with patch("src.routes.tasks.is_claude_cli_available", return_value=False):
            resp = await client.post(
                "/api/tasks/enrich", json={"title": "Something"},
            )
            assert resp.status_code == 503
            data = resp.json()
            assert data["error_type"] == "cli_unavailable"
            assert data["retryable"] is False

    @pytest.mark.asyncio
    async def test_enrich_cli_error_has_error_type(
        self, client: AsyncClient,
    ) -> None:
        """Enrich SDK error 503 includes structured error_type."""
        events = [_make_error_event("some error")]
        with (
            patch(
                "src.routes.tasks.is_claude_cli_available",
                return_value=True,
            ),
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
        ):
            resp = await client.post(
                "/api/tasks/enrich", json={"title": "Something"},
            )
            assert resp.status_code == 503
            data = resp.json()
            assert data["error_type"] == "cli_error"
            assert data["retryable"] is True

    @pytest.mark.asyncio
    async def test_generate_plan_503_has_error_type(
        self, client: AsyncClient, test_app,
    ) -> None:
        """Generate-plan 503 response includes structured error_type."""
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        with patch("src.routes.tasks.is_claude_cli_available", return_value=False):
            resp = await client.post(
                f"/api/tasks/{task.id}/generate-plan",
            )
            assert resp.status_code == 503
            data = resp.json()
            assert data["error_type"] == "cli_unavailable"
            assert data["retryable"] is False


# ------------------------------------------------------------------
# Conditional enrichment (T-P1-120)
# ------------------------------------------------------------------


class TestConditionalEnrichment:
    """enrich_task_title() skips LLM call when description is non-empty."""

    @pytest.mark.asyncio
    async def test_skip_when_description_nonempty(self) -> None:
        """Non-empty existing_description returns it directly without SDK call."""
        result = await enrich_task_title(
            "Some title",
            existing_description="Already has content",
        )
        assert result["description"] == "Already has content"
        assert result["priority"] == "P1"

    @pytest.mark.asyncio
    async def test_skip_when_description_whitespace_only(self) -> None:
        """Whitespace-only description is treated as empty (not skipped)."""
        # This should NOT skip -- whitespace-only means "empty".
        # We can't easily test the full SDK call, but we can verify it
        # does NOT return the whitespace string.
        with patch("src.enrichment.run_claude_query") as mock_query:
            mock_event = MagicMock()
            mock_event.type = "result"
            mock_event.structured_output = {
                "description": "Generated desc",
                "priority": "P0",
            }
            mock_event.result_text = None
            mock_event.error_message = None

            async def _fake_query(*a: Any, **kw: Any) -> AsyncIterator[Any]:
                yield mock_event

            mock_query.return_value = _fake_query()
            result = await enrich_task_title(
                "Title", existing_description="   ",
            )
            assert result["description"] == "Generated desc"
            mock_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_string_does_not_skip(self) -> None:
        """Empty string existing_description triggers normal enrichment."""
        with patch("src.enrichment.run_claude_query") as mock_query:
            mock_event = MagicMock()
            mock_event.type = "result"
            mock_event.structured_output = {
                "description": "AI generated",
                "priority": "P1",
            }
            mock_event.result_text = None
            mock_event.error_message = None

            async def _fake_query(*a: Any, **kw: Any) -> AsyncIterator[Any]:
                yield mock_event

            mock_query.return_value = _fake_query()
            result = await enrich_task_title("Title", existing_description="")
            assert result["description"] == "AI generated"
            mock_query.assert_called_once()
