"""Tests for AI-assisted task enrichment and plan generation (src/enrichment.py).

Tests cover:
- _parse_enrichment with valid/invalid/malformed JSON
- enrich_task_title with mocked SDK (success + failure)
- is_claude_cli_available pre-flight check (SDK import)
- POST /api/tasks/enrich endpoint (success, SDK unavailable 503, error 503)
- _parse_plan with valid/invalid/malformed JSON
- generate_task_plan with mocked SDK (success + failure + codebase context)
- format_plan_as_text formatting
- POST /api/tasks/{id}/generate-plan endpoint (success, 404, 503)
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
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
    MAX_TASKS_PER_PLAN,
    EnrichmentResult,
    PlanGenerationError,
    PlanGenerationErrorType,
    PlanResult,
    ProposedTask,
    _parse_enrichment,
    _parse_plan,
    _validate_plan_structure,
    enrich_task_title,
    format_plan_as_text,
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
    scheduler.cancel_task = AsyncMock(return_value=False)
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
                "src.api.is_claude_cli_available", return_value=True,
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
            "src.api.is_claude_cli_available", return_value=False,
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
                "src.api.is_claude_cli_available", return_value=True,
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
                "src.api.is_claude_cli_available", return_value=True,
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


# ==================================================================
# Plan generation tests
# ==================================================================

# Reusable valid plan data for tests that need to pass structural validation
# but aren't testing the plan content itself.
_VALID_STEPS = [{"step": "Implement feature", "files": ["src/main.py"]}]
_VALID_AC = ["Feature works as expected"]



# ------------------------------------------------------------------
# Unit tests: _parse_plan
# ------------------------------------------------------------------


class TestParsePlan:
    """Tests for the _parse_plan function."""

    def test_valid_json(self) -> None:
        """Parse valid plan JSON."""
        text = json.dumps({
            "plan": "Add caching layer",
            "steps": [{"step": "Add Redis", "files": ["src/cache.py"]}],
            "acceptance_criteria": ["Cache hit rate > 80%"],
        })
        result = _parse_plan(text)
        assert result["plan"] == "Add caching layer"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["step"] == "Add Redis"
        assert result["steps"][0]["files"] == ["src/cache.py"]
        assert result["acceptance_criteria"] == ["Cache hit rate > 80%"]
        assert result["proposed_tasks"] == []

    def test_steps_without_files(self) -> None:
        """Steps without files key get empty files list."""
        text = json.dumps({
            "plan": "Refactor",
            "steps": [{"step": "Split module"}],
            "acceptance_criteria": ["Tests pass"],
        })
        result = _parse_plan(text)
        assert result["steps"][0]["files"] == []

    def test_invalid_json_returns_raw(self) -> None:
        """Non-JSON text falls back to raw text as plan."""
        result = _parse_plan("This is just text")
        assert result["plan"] == "This is just text"
        assert result["steps"] == []
        assert result["acceptance_criteria"] == []

    def test_empty_string(self) -> None:
        """Empty string returns empty plan."""
        result = _parse_plan("")
        assert result["plan"] == ""
        assert result["steps"] == []

    def test_missing_fields_falls_back(self) -> None:
        """Missing required fields trigger Pydantic rejection, falls back to raw text."""
        text = json.dumps({"plan": "Just a plan"})
        result = _parse_plan(text)
        # Pydantic rejects incomplete data, fallback returns raw text as plan
        assert result["plan"] == text
        assert result["steps"] == []
        assert result["acceptance_criteria"] == []

    def test_invalid_steps_rejected(self) -> None:
        """Steps with invalid items cause Pydantic rejection, falls back to raw text."""
        text = json.dumps({
            "plan": "p",
            "steps": [
                {"step": "valid"},
                {"notastep": "invalid"},
                "just a string",
            ],
            "acceptance_criteria": [],
        })
        result = _parse_plan(text)
        # Pydantic rejects the invalid step items, entire plan falls back
        assert result["plan"] == text
        assert result["steps"] == []
        assert result["proposed_tasks"] == []

    def test_with_proposed_tasks(self) -> None:
        """Parse plan JSON with proposed_tasks."""
        text = json.dumps({
            "plan": "Decompose auth feature",
            "steps": [{"step": "Plan subtasks"}],
            "acceptance_criteria": ["Sub-tasks created"],
            "proposed_tasks": [
                {
                    "title": "Add JWT middleware",
                    "description": "Create auth middleware for JWT validation",
                    "suggested_priority": "P0",
                    "suggested_complexity": "S",
                    "dependencies": [],
                    "acceptance_criteria": ["Middleware validates tokens"],
                },
                {
                    "title": "Add login endpoint",
                    "description": "POST /login with credentials",
                    "suggested_priority": "P1",
                    "suggested_complexity": "M",
                    "dependencies": ["Add JWT middleware"],
                    "acceptance_criteria": ["Login returns JWT"],
                },
            ],
        })
        result = _parse_plan(text)
        assert len(result["proposed_tasks"]) == 2
        assert result["proposed_tasks"][0]["title"] == "Add JWT middleware"
        assert result["proposed_tasks"][0]["suggested_priority"] == "P0"
        assert result["proposed_tasks"][1]["dependencies"] == ["Add JWT middleware"]

    def test_proposed_tasks_missing_defaults_to_empty(self) -> None:
        """Plan without proposed_tasks field returns empty list."""
        text = json.dumps({
            "plan": "Simple plan",
            "steps": [{"step": "Do it"}],
            "acceptance_criteria": ["Done"],
        })
        result = _parse_plan(text)
        assert result["proposed_tasks"] == []


# ------------------------------------------------------------------
# Unit tests: _validate_plan_structure
# ------------------------------------------------------------------


class TestValidatePlanStructure:
    """Tests for the _validate_plan_structure function."""

    def test_valid_plan(self) -> None:
        """Complete plan passes validation."""
        plan_data = {
            "plan": "Add caching layer",
            "steps": [{"step": "Add Redis", "files": ["src/cache.py"]}],
            "acceptance_criteria": ["Cache hit rate > 80%"],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is True
        assert reason == "ok"

    def test_empty_plan_text(self) -> None:
        """Empty plan text fails validation."""
        plan_data = {"plan": "", "steps": [{"step": "x"}], "acceptance_criteria": ["y"]}
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert reason == "empty_plan_text"

    def test_whitespace_plan_text(self) -> None:
        """Whitespace-only plan text fails validation."""
        plan_data = {"plan": "   ", "steps": [{"step": "x"}], "acceptance_criteria": ["y"]}
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert reason == "empty_plan_text"

    def test_empty_steps(self) -> None:
        """Empty steps list fails validation."""
        plan_data = {"plan": "A plan", "steps": [], "acceptance_criteria": ["y"]}
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert reason == "empty_steps"

    def test_empty_acceptance_criteria(self) -> None:
        """Empty acceptance_criteria fails validation."""
        plan_data = {"plan": "A plan", "steps": [{"step": "x"}], "acceptance_criteria": []}
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert reason == "empty_acceptance_criteria"

    def test_missing_keys(self) -> None:
        """Missing keys treated as empty."""
        is_valid, reason = _validate_plan_structure({})
        assert is_valid is False
        assert reason == "empty_plan_text"

    def test_too_many_proposed_tasks(self) -> None:
        """More than MAX_TASKS_PER_PLAN proposed tasks fails validation."""
        plan_data = {
            "plan": "Big plan",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": f"Task {i}", "description": f"Desc {i}"}
                for i in range(MAX_TASKS_PER_PLAN + 1)
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert "too_many_proposed_tasks" in reason

    def test_max_proposed_tasks_at_limit(self) -> None:
        """Exactly MAX_TASKS_PER_PLAN proposed tasks passes validation."""
        plan_data = {
            "plan": "Plan at limit",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": f"Task {i}", "description": f"Desc {i}"}
                for i in range(MAX_TASKS_PER_PLAN)
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is True
        assert reason == "ok"


# ------------------------------------------------------------------
# Unit tests: format_plan_as_text
# ------------------------------------------------------------------


class TestFormatPlanAsText:
    """Tests for the format_plan_as_text function."""

    def test_full_plan(self) -> None:
        """Format a complete plan with all sections."""
        plan_data = {
            "plan": "Add user auth with JWT tokens.",
            "steps": [
                {"step": "Create auth middleware", "files": ["src/auth.py"]},
                {"step": "Add login endpoint", "files": ["src/api.py"]},
            ],
            "acceptance_criteria": ["Login returns JWT", "Protected routes reject unauthenticated"],
        }
        text = format_plan_as_text(plan_data)
        assert "Add user auth with JWT tokens." in text
        assert "## Implementation Steps" in text
        assert "1. Create auth middleware" in text
        assert "   - src/auth.py" in text
        assert "2. Add login endpoint" in text
        assert "## Acceptance Criteria" in text
        assert "- Login returns JWT" in text

    def test_empty_plan(self) -> None:
        """Empty plan data produces empty string."""
        text = format_plan_as_text({"plan": "", "steps": [], "acceptance_criteria": []})
        assert text == ""

    def test_plan_only(self) -> None:
        """Plan with no steps or criteria."""
        text = format_plan_as_text({
            "plan": "Just a summary.",
            "steps": [],
            "acceptance_criteria": [],
        })
        assert text == "Just a summary."

    def test_plan_with_proposed_tasks(self) -> None:
        """Plan with proposed tasks includes Proposed Tasks section."""
        text = format_plan_as_text({
            "plan": "Auth decomposition.",
            "steps": [{"step": "Plan subtasks"}],
            "acceptance_criteria": ["Sub-tasks created"],
            "proposed_tasks": [
                {"title": "Add JWT middleware", "description": "Create auth middleware"},
                {"title": "Add login endpoint", "description": "POST /login"},
            ],
        })
        assert "## Proposed Tasks" in text
        assert "1. Add JWT middleware" in text
        assert "Create auth middleware" in text
        assert "2. Add login endpoint" in text


# ------------------------------------------------------------------
# Unit tests: generate_task_plan (async)
# ------------------------------------------------------------------


class TestGenerateTaskPlan:
    """Tests for the generate_task_plan async function (SDK-based)."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful plan generation returns structured data."""
        events = _make_plan_events(
            "Implement dark mode",
            [{"step": "Add theme context", "files": ["src/theme.ts"]}],
            ["Theme toggle works"],
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            result = await generate_task_plan("Add dark mode")
            assert result["plan"] == "Implement dark mode"
            assert len(result["steps"]) == 1
            assert result["acceptance_criteria"] == ["Theme toggle works"]

    @pytest.mark.asyncio
    async def test_with_repo_path(self, tmp_path: Path) -> None:
        """repo_path is passed as add_dirs in QueryOptions."""
        repo = tmp_path / "repo"
        repo.mkdir()
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task", repo_path=repo)
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert str(repo) in options.add_dirs

    @pytest.mark.asyncio
    async def test_without_repo_path(self) -> None:
        """No add_dirs when repo_path is None."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert options.add_dirs == []

    @pytest.mark.asyncio
    async def test_sdk_error_raises(self) -> None:
        """SDK error event raises PlanGenerationError."""
        events = [_make_error_event("SDK error")]

        with (
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
            pytest.raises(PlanGenerationError, match="Claude SDK error"),
        ):
            await generate_task_plan("Broken task")

    @pytest.mark.asyncio
    async def test_with_description(self) -> None:
        """Existing description is included in the prompt."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task", description="Existing desc")
            prompt_arg = mock_query.call_args[0][0]
            assert "Existing desc" in prompt_arg

    @pytest.mark.asyncio
    async def test_on_log_callback_called(self) -> None:
        """on_log callback is called for SDK events."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )
        logged: list[str] = []

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Task", on_log=logged.append,
            )
            assert result["plan"] == "A valid plan summary"
            # on_log should have been called (at least [DONE] for result event)
            assert len(logged) >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_on_no_output(self) -> None:
        """Heartbeat emitted when no SDK events for heartbeat_seconds."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )
        logged: list[str] = []

        # Create an async generator that delays before yielding
        async def _slow_events() -> AsyncIterator[ClaudeEvent]:
            await asyncio.sleep(0.3)  # Exceed heartbeat timeout
            for ev in events:
                yield ev

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_slow_events(),
        ):
            await generate_task_plan(
                "Task", on_log=logged.append, heartbeat_seconds=0.1,
            )
            heartbeats = [line for line in logged if "[PROGRESS] heartbeat" in line]
            assert len(heartbeats) >= 1

    @pytest.mark.asyncio
    async def test_on_raw_artifact_called(self) -> None:
        """on_raw_artifact callback is called with serialized events."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )
        artifacts: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifacts.append(content)

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            await generate_task_plan(
                "Task", on_raw_artifact=capture_artifact,
            )
            assert len(artifacts) == 1
            assert len(artifacts[0]) > 0

    @pytest.mark.asyncio
    async def test_on_raw_artifact_called_even_on_failure(self) -> None:
        """on_raw_artifact persists output even when SDK returns error."""
        events = [
            ClaudeEvent(type=ClaudeEventType.TEXT, text="partial output"),
            _make_error_event("SDK error"),
        ]
        artifacts: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifacts.append(content)

        with (
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
            pytest.raises(PlanGenerationError, match="Claude SDK error"),
        ):
            await generate_task_plan(
                "Broken task", on_raw_artifact=capture_artifact,
            )
        # Raw artifact should still be persisted despite error
        assert len(artifacts) == 1
        assert "partial output" in artifacts[0]

    @pytest.mark.asyncio
    async def test_structural_validation_rejects_empty_plan(self) -> None:
        """Plan with empty steps is rejected after parsing."""
        events = _make_plan_events("Plan text", [], ["criteria"])

        with (
            patch(
                "src.enrichment.run_claude_query",
                return_value=_mock_sdk_events(*events),
            ),
            pytest.raises(PlanGenerationError, match="invalid structure.*empty_steps"),
        ):
            await generate_task_plan("Task")

    @pytest.mark.asyncio
    async def test_query_options_configured(self) -> None:
        """QueryOptions are configured with model, system_prompt, json_schema."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert options.model == "claude-opus-4-6"
            assert options.permission_mode == "plan"
            assert options.system_prompt is not None
            assert options.json_schema is not None

    @pytest.mark.asyncio
    async def test_system_prompt_includes_project_context(self) -> None:
        """System prompt includes CLAUDE.md rules and TASKS.md schema."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            prompt = options.system_prompt
            # TASKS.md schema context
            assert "T-P{priority}-{number}" in prompt
            assert "Acceptance Criteria" in prompt
            # CLAUDE.md project rules
            assert "Scenario matrix" in prompt
            assert "Journey-first ACs" in prompt
            assert "proposed_tasks" in prompt

    @pytest.mark.asyncio
    async def test_json_schema_includes_proposed_tasks(self) -> None:
        """JSON schema includes proposed_tasks array definition."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            schema = json.loads(options.json_schema)
            assert "proposed_tasks" in schema["properties"]
            pt_schema = schema["properties"]["proposed_tasks"]
            assert pt_schema["type"] == "array"
            assert pt_schema["maxItems"] == 8
            item_props = pt_schema["items"]["properties"]
            assert "title" in item_props
            assert "description" in item_props
            assert "suggested_priority" in item_props
            assert "suggested_complexity" in item_props
            assert "dependencies" in item_props
            assert "acceptance_criteria" in item_props

    @pytest.mark.asyncio
    async def test_on_stream_event_callback(self) -> None:
        """on_stream_event callback is called for each SDK event."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )
        received: list[dict] = []

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Task", on_stream_event=received.append,
            )
            assert result["plan"] == "A valid plan summary"
            assert len(received) >= 1
            result_events = [e for e in received if e.get("type") == "result"]
            assert len(result_events) == 1

    @pytest.mark.asyncio
    async def test_on_stream_event_none_is_safe(self) -> None:
        """on_stream_event=None does not crash."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Task", on_stream_event=None,
            )
            assert result["plan"] == "A valid plan summary"

    @pytest.mark.asyncio
    async def test_multi_event_stream(self) -> None:
        """Multiple SDK events are dispatched to on_stream_event."""
        events = [
            ClaudeEvent(type=ClaudeEventType.INIT, session_id="test-session"),
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Planning..."),
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                structured_output={
                    "plan": "Multi-event plan",
                    "steps": [{"step": "Step 1", "files": []}],
                    "acceptance_criteria": ["AC 1"],
                },
            ),
        ]
        received: list[dict] = []

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Task", on_stream_event=received.append,
            )
            assert result["plan"] == "Multi-event plan"
            assert len(received) == 3
            assert received[0]["type"] == "init"
            assert received[1]["type"] == "text"
            assert received[2]["type"] == "result"

    @pytest.mark.asyncio
    async def test_jsonl_file_persistence(self, tmp_path: Path) -> None:
        """JSONL log files are created when stream_log_dir + task_id given."""
        events = _make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            await generate_task_plan(
                "Task",
                stream_log_dir=tmp_path,
                task_id="test-task-1",
            )

        log_dir = tmp_path / "test-task-1"
        assert log_dir.exists()

        jsonl_files = list(log_dir.glob("plan_stream_*.jsonl"))
        assert len(jsonl_files) == 1
        content = jsonl_files[0].read_text(encoding="utf-8").strip()
        assert len(content) > 0
        # Each line should be valid JSON
        for line in content.split("\n"):
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

        raw_files = list(log_dir.glob("plan_raw_*.log"))
        assert len(raw_files) == 1


# ------------------------------------------------------------------
# API endpoint tests: POST /api/tasks/{id}/generate-plan
# ------------------------------------------------------------------


class TestGeneratePlanEndpoint:
    """Tests for POST /api/tasks/{task_id}/generate-plan (async 202)."""

    @pytest.mark.asyncio
    async def test_task_not_found_404(self, client: AsyncClient) -> None:
        """Returns 404 for non-existent task."""
        with patch(
            "src.api.is_claude_cli_available", return_value=True,
        ):
            resp = await client.post(
                "/api/tasks/nonexistent-id/generate-plan",
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cli_unavailable_503(self, client: AsyncClient) -> None:
        """Returns 503 when Claude SDK is not available."""
        with patch(
            "src.api.is_claude_cli_available", return_value=False,
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
                "src.api.is_claude_cli_available", return_value=True,
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
                "src.api.is_claude_cli_available", return_value=True,
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
            "src.api.is_claude_cli_available", return_value=True,
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
                "src.api.is_claude_cli_available", return_value=True,
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
# Data integrity tests
# ------------------------------------------------------------------


class TestPlanDataRoundTripIntegrity:
    """All steps/files/ACs survive _parse_plan -> format_plan_as_text."""

    def test_plan_data_round_trip_integrity(self) -> None:
        """Structured plan data survives parse -> format round-trip."""
        inner = json.dumps({
            "plan": "Implement user auth with JWT tokens.",
            "steps": [
                {"step": "Add login endpoint", "files": ["src/auth.py", "src/api.py"]},
                {"step": "Add middleware", "files": ["src/middleware.py"]},
                {"step": "Write tests", "files": ["tests/test_auth.py"]},
            ],
            "acceptance_criteria": [
                "Login returns JWT",
                "Middleware validates token",
                "Tests pass",
            ],
        })
        plan_data = _parse_plan(inner)

        assert plan_data["plan"] == "Implement user auth with JWT tokens."
        assert len(plan_data["steps"]) == 3
        assert plan_data["steps"][0]["files"] == ["src/auth.py", "src/api.py"]
        assert len(plan_data["acceptance_criteria"]) == 3

        formatted = format_plan_as_text(plan_data)
        assert "Implement user auth with JWT tokens." in formatted
        assert "Add login endpoint" in formatted
        assert "src/auth.py" in formatted
        assert "Login returns JWT" in formatted
        assert "Tests pass" in formatted


class TestSdkEventSerialization:
    """SDK event dicts are properly serialized in raw artifacts."""

    @pytest.mark.asyncio
    async def test_multiple_events_serialized_to_artifact(self) -> None:
        """Multiple SDK events are serialized as newline-delimited JSON in artifact."""
        events = [
            ClaudeEvent(type=ClaudeEventType.INIT, session_id="s1"),
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Planning..."),
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                structured_output={
                    "plan": "Do the thing",
                    "steps": [{"step": "Step 1", "files": []}],
                    "acceptance_criteria": ["AC1"],
                },
            ),
        ]
        artifact_content: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifact_content.append(content)

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Test", description="desc",
                on_raw_artifact=capture_artifact,
            )

        assert len(artifact_content) == 1
        # Each event is a separate JSON line
        lines = artifact_content[0].split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

        assert result["plan"] == "Do the thing"
        assert len(result["steps"]) == 1


# ------------------------------------------------------------------
# Error taxonomy tests (T-P1-74)
# ------------------------------------------------------------------


class TestPlanGenerationErrorType:
    """Tests for PlanGenerationErrorType enum properties."""

    def test_all_types_have_user_message(self) -> None:
        """Every error type has a non-empty user_message."""
        for et in PlanGenerationErrorType:
            assert len(et.user_message) > 0

    def test_retryable_types(self) -> None:
        """Timeout, parse_failure, cli_error are retryable."""
        assert PlanGenerationErrorType.TIMEOUT.retryable is True
        assert PlanGenerationErrorType.PARSE_FAILURE.retryable is True
        assert PlanGenerationErrorType.CLI_ERROR.retryable is True

    def test_non_retryable_types(self) -> None:
        """CLI unavailable and budget exceeded are not retryable."""
        assert PlanGenerationErrorType.CLI_UNAVAILABLE.retryable is False
        assert PlanGenerationErrorType.BUDGET_EXCEEDED.retryable is False

    def test_string_values(self) -> None:
        """Enum values are lowercase snake_case strings."""
        assert PlanGenerationErrorType.CLI_UNAVAILABLE == "cli_unavailable"
        assert PlanGenerationErrorType.TIMEOUT == "timeout"
        assert PlanGenerationErrorType.PARSE_FAILURE == "parse_failure"
        assert PlanGenerationErrorType.BUDGET_EXCEEDED == "budget_exceeded"
        assert PlanGenerationErrorType.CLI_ERROR == "cli_error"


class TestPlanGenerationError:
    """Tests for PlanGenerationError exception class."""

    def test_error_carries_type(self) -> None:
        """Exception carries error_type for classification."""
        err = PlanGenerationError(PlanGenerationErrorType.TIMEOUT, "timed out")
        assert err.error_type == PlanGenerationErrorType.TIMEOUT
        assert err.detail == "timed out"

    def test_retryable_property(self) -> None:
        """retryable delegates to error_type."""
        err = PlanGenerationError(PlanGenerationErrorType.TIMEOUT, "timed out")
        assert err.retryable is True
        err2 = PlanGenerationError(PlanGenerationErrorType.BUDGET_EXCEEDED, "over")
        assert err2.retryable is False

    def test_user_message_property(self) -> None:
        """user_message delegates to error_type."""
        err = PlanGenerationError(PlanGenerationErrorType.CLI_UNAVAILABLE, "x")
        assert "not installed" in err.user_message.lower()

    def test_str_representation(self) -> None:
        """str() includes error type and detail."""
        err = PlanGenerationError(PlanGenerationErrorType.TIMEOUT, "timed out")
        s = str(err)
        assert "timeout" in s
        assert "timed out" in s


class TestClassifyCliError:
    """Tests for _classify_cli_error helper."""

    def test_budget_exceeded(self) -> None:
        """Stderr mentioning 'budget' -> BUDGET_EXCEEDED."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(1, "Error: API budget exceeded")
        assert result == PlanGenerationErrorType.BUDGET_EXCEEDED

    def test_usage_limit(self) -> None:
        """Stderr mentioning 'usage limit' -> BUDGET_EXCEEDED."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(1, "Usage limit reached")
        assert result == PlanGenerationErrorType.BUDGET_EXCEEDED

    def test_not_found(self) -> None:
        """Stderr mentioning 'not found' -> CLI_UNAVAILABLE."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(127, "claude: command not found")
        assert result == PlanGenerationErrorType.CLI_UNAVAILABLE

    def test_no_such_file(self) -> None:
        """Stderr with 'no such file' -> CLI_UNAVAILABLE."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(1, "No such file or directory")
        assert result == PlanGenerationErrorType.CLI_UNAVAILABLE

    def test_generic_error(self) -> None:
        """Unrecognized errors -> CLI_ERROR."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(1, "Something unexpected happened")
        assert result == PlanGenerationErrorType.CLI_ERROR


class TestStructuredErrorInApi:
    """Tests for structured error responses in API endpoints."""

    @pytest.mark.asyncio
    async def test_enrich_503_has_error_type(
        self, client: AsyncClient,
    ) -> None:
        """Enrich 503 response includes error_type and retryable fields."""
        with patch("src.api.is_claude_cli_available", return_value=False):
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
                "src.api.is_claude_cli_available",
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

        with patch("src.api.is_claude_cli_available", return_value=False):
            resp = await client.post(
                f"/api/tasks/{task.id}/generate-plan",
            )
            assert resp.status_code == 503
            data = resp.json()
            assert data["error_type"] == "cli_unavailable"
            assert data["retryable"] is False


# ------------------------------------------------------------------
# Unit tests: Pydantic validation models
# ------------------------------------------------------------------


class TestEnrichmentResultModel:
    """Tests for EnrichmentResult Pydantic validation."""

    def test_valid_enrichment(self) -> None:
        """Valid enrichment data passes validation."""
        result = EnrichmentResult.model_validate(
            {"description": "Add login", "priority": "P0"}
        )
        assert result.description == "Add login"
        assert result.priority == "P0"

    def test_invalid_priority_rejected(self) -> None:
        """Invalid priority enum value is rejected by Pydantic."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="priority"):
            EnrichmentResult.model_validate(
                {"description": "desc", "priority": "P5"}
            )

    def test_missing_required_field(self) -> None:
        """Missing required field is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="description"):
            EnrichmentResult.model_validate({"priority": "P0"})

    def test_all_valid_priorities(self) -> None:
        """All valid priority values are accepted."""
        for p in ("P0", "P1", "P2"):
            result = EnrichmentResult.model_validate(
                {"description": "d", "priority": p}
            )
            assert result.priority == p


class TestProposedTaskModel:
    """Tests for ProposedTask Pydantic validation."""

    def test_valid_proposed_task(self) -> None:
        """Valid proposed task passes validation."""
        task = ProposedTask.model_validate({
            "title": "Add auth",
            "description": "Implement JWT auth",
            "suggested_priority": "P0",
            "suggested_complexity": "S",
            "dependencies": ["Setup DB"],
            "acceptance_criteria": ["Auth works"],
        })
        assert task.title == "Add auth"
        assert task.suggested_priority == "P0"
        assert task.suggested_complexity == "S"
        assert task.dependencies == ["Setup DB"]

    def test_minimal_proposed_task(self) -> None:
        """Proposed task with only required fields uses defaults."""
        task = ProposedTask.model_validate({
            "title": "Fix bug",
            "description": "Fix the login bug",
        })
        assert task.suggested_priority == "P1"
        assert task.suggested_complexity == "M"
        assert task.dependencies == []
        assert task.acceptance_criteria == []

    def test_invalid_priority_rejected(self) -> None:
        """Invalid priority enum is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="suggested_priority"):
            ProposedTask.model_validate({
                "title": "x",
                "description": "y",
                "suggested_priority": "CRITICAL",
            })

    def test_invalid_complexity_rejected(self) -> None:
        """Invalid complexity enum is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="suggested_complexity"):
            ProposedTask.model_validate({
                "title": "x",
                "description": "y",
                "suggested_complexity": "XL",
            })


class TestPlanResultModel:
    """Tests for PlanResult and PlanStep Pydantic validation."""

    def test_valid_plan(self) -> None:
        """Valid plan data passes validation."""
        result = PlanResult.model_validate({
            "plan": "Add caching",
            "steps": [{"step": "Add Redis", "files": ["src/cache.py"]}],
            "acceptance_criteria": ["Tests pass"],
        })
        assert result.plan == "Add caching"
        assert len(result.steps) == 1
        assert result.steps[0].step == "Add Redis"
        assert result.steps[0].files == ["src/cache.py"]

    def test_step_without_files(self) -> None:
        """Step without files defaults to empty list."""
        result = PlanResult.model_validate({
            "plan": "p",
            "steps": [{"step": "Do thing"}],
            "acceptance_criteria": ["ac"],
        })
        assert result.steps[0].files == []

    def test_step_missing_step_key_rejected(self) -> None:
        """Step without required 'step' key is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="step"):
            PlanResult.model_validate({
                "plan": "p",
                "steps": [{"notastep": "invalid"}],
                "acceptance_criteria": [],
            })

    def test_missing_plan_field_rejected(self) -> None:
        """Missing plan field is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="plan"):
            PlanResult.model_validate({
                "steps": [],
                "acceptance_criteria": [],
            })

    def test_plan_with_proposed_tasks(self) -> None:
        """PlanResult with proposed_tasks validates correctly."""
        result = PlanResult.model_validate({
            "plan": "Auth plan",
            "steps": [{"step": "Do it"}],
            "acceptance_criteria": ["Done"],
            "proposed_tasks": [
                {"title": "Sub-task A", "description": "First piece"},
            ],
        })
        assert len(result.proposed_tasks) == 1
        assert result.proposed_tasks[0].title == "Sub-task A"

    def test_plan_without_proposed_tasks(self) -> None:
        """PlanResult without proposed_tasks defaults to empty list."""
        result = PlanResult.model_validate({
            "plan": "Simple",
            "steps": [{"step": "Do it"}],
            "acceptance_criteria": ["Done"],
        })
        assert result.proposed_tasks == []


class TestParseEnrichmentWithValidation:
    """Tests that _parse_enrichment uses Pydantic and logs raw content."""

    def test_invalid_priority_logs_raw_content(self, caplog: pytest.LogCaptureFixture) -> None:
        """Invalid priority triggers Pydantic rejection and logs raw content."""
        text = json.dumps({"description": "desc", "priority": "HIGH"})
        with caplog.at_level("WARNING"):
            result = _parse_enrichment(text)
        assert result["priority"] == "P1"  # falls back to default
        assert result["description"] == ""  # falls back to default
        assert "Raw" in caplog.text
        assert "HIGH" in caplog.text

    def test_malformed_json_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed JSON logs raw content."""
        with caplog.at_level("WARNING"):
            result = _parse_enrichment("not json {{{")
        assert result["description"] == ""
        assert "Raw" in caplog.text
        assert "not json" in caplog.text


class TestParsePlanWithValidation:
    """Tests that _parse_plan uses Pydantic and logs raw content."""

    def test_invalid_step_structure_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Steps with wrong structure trigger Pydantic rejection and log raw."""
        text = json.dumps({
            "plan": "p",
            "steps": [{"notastep": "bad"}],
            "acceptance_criteria": [],
        })
        with caplog.at_level("WARNING"):
            result = _parse_plan(text)
        assert result["steps"] == []  # falls back
        assert "Raw" in caplog.text

    def test_missing_acceptance_criteria_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing required field triggers Pydantic rejection."""
        text = json.dumps({"plan": "p", "steps": []})
        with caplog.at_level("WARNING"):
            result = _parse_plan(text)
        assert result["plan"] == text  # raw text fallback
        assert "Raw" in caplog.text
