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

import asyncio
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
    """Create a mock subprocess with given stdout and return code.

    Used for communicate()-based functions (enrich_task_title).
    """
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


def _mock_readline_proc(stdout: bytes, returncode: int = 0) -> AsyncMock:
    """Create a mock subprocess with readline-based stdout.

    Used for generate_task_plan() which reads line-by-line.
    """
    proc = AsyncMock()
    proc.returncode = returncode
    proc.wait = AsyncMock()
    proc.kill = MagicMock()

    # Build a mock stdout that yields lines then EOF
    lines = stdout.split(b"\n") if stdout else []
    line_queue: list[bytes] = [line + b"\n" for line in lines if line]
    line_queue.append(b"")  # EOF sentinel

    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=line_queue)

    # stderr for error cases
    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"")

    proc.stdout = mock_stdout
    proc.stderr = mock_stderr

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
    """Tests for the generate_task_plan async function (readline-based)."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful plan generation returns structured data."""
        stdout = _make_plan_output(
            "Implement dark mode",
            [{"step": "Add theme context", "files": ["src/theme.ts"]}],
            ["Theme toggle works"],
        )
        mock_proc = _mock_readline_proc(stdout)

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
        mock_proc = _mock_readline_proc(stdout)

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
        mock_proc = _mock_readline_proc(stdout)

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
        mock_proc = _mock_readline_proc(b"", returncode=1)
        mock_proc.stderr.read = AsyncMock(return_value=b"error")

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
        mock_proc = _mock_readline_proc(stdout)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            await generate_task_plan("Task", description="Existing desc")
            call_args = mock_exec.call_args[0]
            # The prompt should contain the description
            prompt_arg = call_args[2]  # claude, -p, <prompt>
            assert "Existing desc" in prompt_arg

    @pytest.mark.asyncio
    async def test_on_log_callback_called(self) -> None:
        """on_log callback is called for each stdout line."""
        stdout = _make_plan_output("Plan", [], [])
        mock_proc = _mock_readline_proc(stdout)
        logged: list[str] = []

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await generate_task_plan(
                "Task", on_log=logged.append,
            )
            assert result["plan"] == "Plan"
            # on_log should have been called with the output line
            assert len(logged) >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_on_no_output(self) -> None:
        """Heartbeat emitted when no output for heartbeat_seconds."""
        # Mock proc that times out once then returns a line then EOF
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        stdout_data = _make_plan_output("Plan", [], [])
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(
            side_effect=[
                TimeoutError(),  # first read times out
                stdout_data.split(b"\n")[0] + b"\n",  # then real line
                b"",  # EOF
            ],
        )
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")

        logged: list[str] = []

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "src.enrichment.asyncio.wait_for",
            side_effect=mock_stdout.readline.side_effect,
        ):
            await generate_task_plan(
                "Task", on_log=logged.append, heartbeat_seconds=1,
            )
            # Should have a heartbeat line
            heartbeats = [line for line in logged if "[PROGRESS] heartbeat" in line]
            assert len(heartbeats) >= 1


# ------------------------------------------------------------------
# API endpoint tests: POST /api/tasks/{id}/generate-plan
# ------------------------------------------------------------------


class TestGeneratePlanEndpoint:
    """Tests for POST /api/tasks/{task_id}/generate-plan (async 202)."""

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

        stdout = _make_plan_output(
            "Add authentication flow",
            [{"step": "Create auth module", "files": ["src/auth.py"]}],
            ["Login returns token"],
        )
        mock_proc = _mock_readline_proc(stdout)

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
        """CLI failure in background sets plan_status to 'failed'."""
        from src.sync.tasks_parser import sync_project_tasks

        task_manager: TaskManager = test_app.state.task_manager
        registry = test_app.state.registry
        project = registry.list_projects()[0]
        await sync_project_tasks(project.id, task_manager, registry)

        tasks = await task_manager.list_tasks(project_id=project.id)
        task = tasks[0]

        mock_proc = _mock_readline_proc(b"", returncode=1)
        mock_proc.stderr.read = AsyncMock(return_value=b"error")

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
            "src.enrichment.shutil.which", return_value="/usr/bin/claude",
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

        stdout = _make_plan_output("Plan", [], [])
        mock_proc = _mock_readline_proc(stdout)

        # Collect emitted events
        emitted: list[tuple[str, str, dict]] = []
        original_emit = event_bus.emit

        def capture_emit(event_type: str, task_id: str, data: object) -> None:
            emitted.append((event_type, task_id, data))
            original_emit(event_type, task_id, data)

        event_bus.emit = capture_emit

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
    """Tests for timeout behavior in enrichment subprocess calls."""

    @pytest.mark.asyncio
    async def test_enrich_task_title_timeout(self) -> None:
        """enrich_task_title raises RuntimeError on timeout."""
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            side_effect=TimeoutError(),
        )
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with (
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            await enrich_task_title("Title", timeout_minutes=1)

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_task_plan_timeout(self) -> None:
        """generate_task_plan raises RuntimeError on overall timeout."""
        proc = AsyncMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        # Mock stdout.readline that never returns (hangs forever)
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(side_effect=asyncio.CancelledError())
        proc.stdout = mock_stdout
        proc.stderr = AsyncMock()
        proc.stderr.read = AsyncMock(return_value=b"")

        # Patch asyncio.timeout to raise TimeoutError immediately
        with (
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch(
                "src.enrichment.asyncio.timeout",
                side_effect=TimeoutError(),
            ),
            pytest.raises(RuntimeError, match="timed out"),
        ):
            await generate_task_plan("Title", timeout_minutes=1)

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_zero_timeout_disables(self) -> None:
        """timeout_minutes=0 passes timeout=None (no timeout) for enrich."""
        stdout = _make_enrichment_output("desc", "P0")
        mock_proc = _mock_proc(stdout)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), patch(
            "src.enrichment.asyncio.wait_for",
            wraps=asyncio.wait_for,
        ) as mock_wait_for:
            await enrich_task_title("Title", timeout_minutes=0)
            # timeout=None means no timeout
            _, kwargs = mock_wait_for.call_args
            assert kwargs.get("timeout") is None


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
