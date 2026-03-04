"""Tests for AI-assisted task enrichment and plan generation (src/enrichment.py).

Tests cover:
- _parse_enrichment with valid/invalid/malformed JSON
- enrich_task_title with mocked subprocess (success + failure)
- is_claude_cli_available pre-flight check
- POST /api/tasks/enrich endpoint (success, CLI unavailable 503, CLI error 503)
- _parse_plan with valid/invalid/malformed JSON
- generate_task_plan with mocked subprocess (success + failure + codebase context)
- format_plan_as_text formatting
- POST /api/tasks/{id}/generate-plan endpoint (success, 404, 503)
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
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
    _parse_enrichment,
    _parse_plan,
    enrich_task_title,
    format_plan_as_text,
    generate_task_plan,
    is_claude_cli_available,
)
from src.models import ExecutorType
from src.process_manager import ProcessStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_cli_output(inner_json: str) -> bytes:
    """Create mock Claude CLI stdout bytes wrapping an inner JSON string."""
    cli_output = {"type": "result", "result": inner_json}
    return json.dumps(cli_output).encode("utf-8")


def _make_enrichment_output(description: str, priority: str) -> bytes:
    """Create mock Claude CLI stdout for an enrichment response."""
    inner = json.dumps({"description": description, "priority": priority})
    return _make_cli_output(inner)


def _mock_proc(stdout: bytes, returncode: int = 0) -> AsyncMock:
    """Create a mock subprocess with given stdout and return code."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


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

    def test_description_is_stringified(self) -> None:
        """Non-string description is cast to str."""
        text = json.dumps({"description": 42, "priority": "P0"})
        result = _parse_enrichment(text)
        assert result["description"] == "42"

    def test_priority_is_stringified(self) -> None:
        """Non-string priority is cast to str and falls back to P1 if invalid."""
        text = json.dumps({"description": "desc", "priority": 0})
        result = _parse_enrichment(text)
        assert result["priority"] == "P1"


# ------------------------------------------------------------------
# Unit tests: is_claude_cli_available
# ------------------------------------------------------------------


class TestClaudeCliAvailable:
    """Tests for the is_claude_cli_available pre-flight check."""

    def test_available(self) -> None:
        """Returns True when claude is on PATH."""
        with patch("src.enrichment.shutil.which", return_value="/usr/bin/claude"):
            assert is_claude_cli_available() is True

    def test_unavailable(self) -> None:
        """Returns False when claude is not on PATH."""
        with patch("src.enrichment.shutil.which", return_value=None):
            assert is_claude_cli_available() is False


# ------------------------------------------------------------------
# Unit tests: enrich_task_title (async)
# ------------------------------------------------------------------


class TestEnrichTaskTitle:
    """Tests for the enrich_task_title async function."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful enrichment returns description and priority."""
        stdout = _make_enrichment_output(
            "Implement user authentication with JWT",
            "P0",
        )
        mock_proc = _mock_proc(stdout)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await enrich_task_title("Add auth")
            assert result["description"] == "Implement user authentication with JWT"
            assert result["priority"] == "P0"

    @pytest.mark.asyncio
    async def test_cli_failure_raises(self) -> None:
        """Non-zero exit code raises RuntimeError."""
        mock_proc = _mock_proc(b"", returncode=1)
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"CLI error message"),
        )

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), pytest.raises(RuntimeError, match="Claude CLI failed"):
            await enrich_task_title("Something")

    @pytest.mark.asyncio
    async def test_malformed_json_uses_defaults(self) -> None:
        """Malformed inner JSON uses safe defaults."""
        cli_output = json.dumps({"result": "not valid json"}).encode("utf-8")
        mock_proc = _mock_proc(cli_output)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
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
        stdout = _make_enrichment_output(
            "Add user authentication flow with OAuth2",
            "P0",
        )
        mock_proc = _mock_proc(stdout)

        with (
            patch(
                "src.enrichment.shutil.which", return_value="/usr/bin/claude",
            ),
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
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
        """Returns 503 when Claude CLI is not on PATH."""
        with patch(
            "src.enrichment.shutil.which", return_value=None,
        ):
            resp = await client.post(
                "/api/tasks/enrich",
                json={"title": "Something"},
            )
            assert resp.status_code == 503
            assert "not available" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_cli_error_503(self, client: AsyncClient) -> None:
        """Returns 503 when Claude CLI subprocess fails."""
        mock_proc = _mock_proc(b"", returncode=1)
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"error"),
        )

        with (
            patch(
                "src.enrichment.shutil.which", return_value="/usr/bin/claude",
            ),
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            resp = await client.post(
                "/api/tasks/enrich",
                json={"title": "Something"},
            )
            assert resp.status_code == 503
            assert "failed" in resp.json()["detail"].lower()

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
        """Invalid priority from CLI falls back to P1."""
        inner = json.dumps({"description": "desc", "priority": "CRITICAL"})
        stdout = _make_cli_output(inner)
        mock_proc = _mock_proc(stdout)

        with (
            patch(
                "src.enrichment.shutil.which", return_value="/usr/bin/claude",
            ),
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
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

def _make_plan_output(
    plan: str,
    steps: list[dict],
    acceptance_criteria: list[str],
) -> bytes:
    """Create mock Claude CLI stdout for a plan generation response."""
    inner = json.dumps({
        "plan": plan,
        "steps": steps,
        "acceptance_criteria": acceptance_criteria,
    })
    return _make_cli_output(inner)


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

    def test_missing_fields(self) -> None:
        """Missing fields default to empty."""
        text = json.dumps({"plan": "Just a plan"})
        result = _parse_plan(text)
        assert result["plan"] == "Just a plan"
        assert result["steps"] == []
        assert result["acceptance_criteria"] == []

    def test_invalid_steps_filtered(self) -> None:
        """Steps without 'step' key are filtered out."""
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
        assert len(result["steps"]) == 1
        assert result["steps"][0]["step"] == "valid"


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


# ------------------------------------------------------------------
# Unit tests: generate_task_plan (async)
# ------------------------------------------------------------------


class TestGenerateTaskPlan:
    """Tests for the generate_task_plan async function."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful plan generation returns structured data."""
        stdout = _make_plan_output(
            "Implement dark mode",
            [{"step": "Add theme context", "files": ["src/theme.ts"]}],
            ["Theme toggle works"],
        )
        mock_proc = _mock_proc(stdout)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await generate_task_plan("Add dark mode")
            assert result["plan"] == "Implement dark mode"
            assert len(result["steps"]) == 1
            assert result["acceptance_criteria"] == ["Theme toggle works"]

    @pytest.mark.asyncio
    async def test_with_repo_path(self, tmp_path: Path) -> None:
        """repo_path is passed as --add-dir when it exists."""
        repo = tmp_path / "repo"
        repo.mkdir()
        stdout = _make_plan_output("Plan", [], [])
        mock_proc = _mock_proc(stdout)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            await generate_task_plan("Task", repo_path=repo)
            call_args = mock_exec.call_args[0]
            assert "--add-dir" in call_args
            assert str(repo) in call_args
            assert "--permission-mode" in call_args
            assert "plan" in call_args

    @pytest.mark.asyncio
    async def test_without_repo_path(self) -> None:
        """No --add-dir when repo_path is None."""
        stdout = _make_plan_output("Plan", [], [])
        mock_proc = _mock_proc(stdout)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            await generate_task_plan("Task")
            call_args = mock_exec.call_args[0]
            assert "--add-dir" not in call_args

    @pytest.mark.asyncio
    async def test_cli_failure_raises(self) -> None:
        """Non-zero exit code raises RuntimeError."""
        mock_proc = _mock_proc(b"", returncode=1)
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with (
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            pytest.raises(RuntimeError, match="Claude CLI failed"),
        ):
            await generate_task_plan("Broken task")

    @pytest.mark.asyncio
    async def test_with_description(self) -> None:
        """Existing description is included in the prompt."""
        stdout = _make_plan_output("Plan", [], [])
        mock_proc = _mock_proc(stdout)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            await generate_task_plan("Task", description="Existing desc")
            call_args = mock_exec.call_args[0]
            # The prompt should contain the description
            prompt_arg = call_args[2]  # claude, -p, <prompt>
            assert "Existing desc" in prompt_arg


# ------------------------------------------------------------------
# API endpoint tests: POST /api/tasks/{id}/generate-plan
# ------------------------------------------------------------------


class TestGeneratePlanEndpoint:
    """Tests for POST /api/tasks/{task_id}/generate-plan."""

    @pytest.mark.asyncio
    async def test_task_not_found_404(self, client: AsyncClient) -> None:
        """Returns 404 for non-existent task."""
        with patch(
            "src.enrichment.shutil.which", return_value="/usr/bin/claude",
        ):
            resp = await client.post(
                "/api/tasks/nonexistent-id/generate-plan",
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cli_unavailable_503(self, client: AsyncClient) -> None:
        """Returns 503 when Claude CLI is not available."""
        with patch(
            "src.enrichment.shutil.which", return_value=None,
        ):
            resp = await client.post(
                "/api/tasks/any-id/generate-plan",
            )
            assert resp.status_code == 503
            assert "not available" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_success(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Successful plan generation returns structured plan and saves to task."""
        from src.sync.tasks_parser import sync_project_tasks

        # Sync tasks so we have a task in DB
        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        stdout = _make_plan_output(
            "Add authentication flow",
            [{"step": "Create auth module", "files": ["src/auth.py"]}],
            ["Login returns token"],
        )
        mock_proc = _mock_proc(stdout)

        with (
            patch(
                "src.enrichment.shutil.which", return_value="/usr/bin/claude",
            ),
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            resp = await client.post(
                f"/api/tasks/{task.id}/generate-plan",
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["plan"] == "Add authentication flow"
            assert len(data["steps"]) == 1
            assert data["steps"][0]["step"] == "Create auth module"
            assert data["acceptance_criteria"] == ["Login returns token"]
            assert "formatted" in data
            assert "## Implementation Steps" in data["formatted"]

        # Verify task.description was updated
        updated_task = await task_manager.get_task(task.id)
        assert updated_task is not None
        assert "Add authentication flow" in updated_task.description

    @pytest.mark.asyncio
    async def test_cli_error_503(
        self, test_app, client: AsyncClient,
    ) -> None:
        """Returns 503 when Claude CLI subprocess fails during generation."""
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        mock_proc = _mock_proc(b"", returncode=1)
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with (
            patch(
                "src.enrichment.shutil.which", return_value="/usr/bin/claude",
            ),
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            resp = await client.post(
                f"/api/tasks/{task.id}/generate-plan",
            )
            assert resp.status_code == 503
            assert "failed" in resp.json()["detail"].lower()
