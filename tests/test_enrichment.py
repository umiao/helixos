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
    EnrichmentResult,
    PlanGenerationError,
    PlanGenerationErrorType,
    PlanResult,
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
        """Non-zero exit code raises PlanGenerationError."""
        mock_proc = _mock_proc(b"", returncode=1)
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"CLI error message"),
        )

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ), pytest.raises(PlanGenerationError, match="Claude CLI failed"):
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
            assert "not installed" in resp.json()["detail"].lower()

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

# Reusable valid plan data for tests that need to pass structural validation
# but aren't testing the plan content itself.
_VALID_STEPS = [{"step": "Implement feature", "files": ["src/main.py"]}]
_VALID_AC = ["Feature works as expected"]


def _make_valid_plan_output() -> bytes:
    """Create mock plan output that passes structural validation."""
    return _make_plan_output("A valid plan summary", _VALID_STEPS, _VALID_AC)


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
        stdout = _make_valid_plan_output()
        mock_proc = _mock_readline_proc(stdout)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            await generate_task_plan("Task", repo_path=repo)
            call_args = mock_exec.call_args[0]
            assert "--add-dir" in call_args
            assert str(repo) in call_args
            # --permission-mode plan was removed: it conflicts with --json-schema
            assert "--permission-mode" not in call_args

    @pytest.mark.asyncio
    async def test_without_repo_path(self) -> None:
        """No --add-dir when repo_path is None."""
        stdout = _make_valid_plan_output()
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
        """Non-zero exit code raises PlanGenerationError."""
        mock_proc = _mock_readline_proc(b"", returncode=1)
        mock_proc.stderr.read = AsyncMock(return_value=b"error")

        with (
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            pytest.raises(PlanGenerationError, match="Claude CLI failed"),
        ):
            await generate_task_plan("Broken task")

    @pytest.mark.asyncio
    async def test_with_description(self) -> None:
        """Existing description is included in the prompt."""
        stdout = _make_valid_plan_output()
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
        stdout = _make_valid_plan_output()
        mock_proc = _mock_readline_proc(stdout)
        logged: list[str] = []

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await generate_task_plan(
                "Task", on_log=logged.append,
            )
            assert result["plan"] == "A valid plan summary"
            # on_log should have been called with the output line
            assert len(logged) >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_on_no_output(self) -> None:
        """Heartbeat emitted when no output for heartbeat_seconds."""
        # Mock proc that times out once then returns a line then EOF
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.wait = AsyncMock()

        stdout_data = _make_valid_plan_output()
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

    @pytest.mark.asyncio
    async def test_on_raw_artifact_called(self) -> None:
        """on_raw_artifact callback is called with full output before parsing."""
        stdout = _make_valid_plan_output()
        mock_proc = _mock_readline_proc(stdout)
        artifacts: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifacts.append(content)

        with patch(
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            await generate_task_plan(
                "Task", on_raw_artifact=capture_artifact,
            )
            assert len(artifacts) == 1
            assert len(artifacts[0]) > 0

    @pytest.mark.asyncio
    async def test_on_raw_artifact_called_even_on_failure(self) -> None:
        """on_raw_artifact persists output even when CLI exits non-zero."""
        mock_proc = _mock_readline_proc(b"partial output\n", returncode=1)
        mock_proc.stderr.read = AsyncMock(return_value=b"error")
        artifacts: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifacts.append(content)

        with (
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            pytest.raises(PlanGenerationError, match="Claude CLI failed"),
        ):
            await generate_task_plan(
                "Broken task", on_raw_artifact=capture_artifact,
            )
        # Raw artifact should still be persisted despite CLI failure
        assert len(artifacts) == 1
        assert "partial output" in artifacts[0]

    @pytest.mark.asyncio
    async def test_structural_validation_rejects_empty_plan(self) -> None:
        """Plan with empty steps is rejected after parsing."""
        stdout = _make_plan_output("Plan text", [], ["criteria"])
        mock_proc = _mock_readline_proc(stdout)

        with (
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            pytest.raises(PlanGenerationError, match="invalid structure.*empty_steps"),
        ):
            await generate_task_plan("Task")


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

        stdout = _make_valid_plan_output()
        mock_proc = _mock_readline_proc(stdout)

        # Collect emitted events
        emitted: list[tuple[str, str, dict]] = []
        original_emit = event_bus.emit

        def capture_emit(event_type: str, task_id: str, data: object, **kwargs: object) -> None:
            emitted.append((event_type, task_id, data))
            original_emit(event_type, task_id, data, **kwargs)

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
        """enrich_task_title raises PlanGenerationError on timeout."""
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            side_effect=TimeoutError(),
        )
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch(  # noqa: SIM117
            "src.enrichment.asyncio.create_subprocess_exec",
            return_value=proc,
        ):
            with pytest.raises(PlanGenerationError, match="timed out") as exc_info:
                await enrich_task_title("Title", timeout_minutes=1)

        assert exc_info.value.error_type == PlanGenerationErrorType.TIMEOUT

        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_task_plan_timeout(self) -> None:
        """generate_task_plan raises PlanGenerationError on overall timeout."""
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
        with (  # noqa: SIM117
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=proc,
            ),
            patch(
                "src.enrichment.asyncio.timeout",
                side_effect=TimeoutError(),
            ),
        ):
            with pytest.raises(PlanGenerationError, match="timed out") as exc_info:
                await generate_task_plan("Title", timeout_minutes=1)

        assert exc_info.value.error_type == PlanGenerationErrorType.TIMEOUT
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


class TestBlankLinePreservation:
    """Blank lines in CLI output must not corrupt JSON reassembly."""

    @pytest.mark.asyncio
    @patch("src.enrichment.asyncio.create_subprocess_exec")
    async def test_generate_plan_preserves_blank_lines_in_json(
        self, mock_exec: AsyncMock,
    ) -> None:
        """Blank lines in CLI output are preserved in raw artifact and don't break parsing.

        The CLI outputs JSON on a single line, but may emit blank lines
        before/after.  The old ``if decoded:`` filter dropped blank lines
        entirely; the fix preserves them for reassembly.  Parsing succeeds
        via the last-line fallback.
        """
        inner = json.dumps({
            "plan": "Do the thing",
            "steps": [{"step": "Step 1", "files": []}],
            "acceptance_criteria": ["AC1"],
        })
        cli_json = json.dumps({"type": "result", "result": inner})

        # Simulate CLI output: blank lines before/after the JSON blob
        raw_lines = [
            b"\n",  # blank line (progress output gap)
            b"\n",  # another blank line
            cli_json.encode("utf-8") + b"\n",  # the actual JSON
            b"\n",  # trailing blank
            b"",  # EOF
        ]

        proc = AsyncMock()
        proc.returncode = 0
        proc.wait = AsyncMock()
        proc.kill = MagicMock()
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(side_effect=raw_lines)
        mock_stderr = AsyncMock()
        mock_stderr.read = AsyncMock(return_value=b"")
        proc.stdout = mock_stdout
        proc.stderr = mock_stderr
        mock_exec.return_value = proc

        artifact_content: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifact_content.append(content)

        result = await generate_task_plan(
            "Test", description="desc",
            on_raw_artifact=capture_artifact,
        )

        # The blank lines should be preserved in the raw artifact
        assert len(artifact_content) == 1
        assert "\n\n" in artifact_content[0]  # blank lines preserved

        # The plan should still parse correctly (last-line fallback)
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
        with patch("src.enrichment.shutil.which", return_value=None):
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
        """Enrich CLI failure 503 includes structured error_type."""
        mock_proc = _mock_proc(b"", returncode=1)
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"some error"),
        )
        with (
            patch(
                "src.enrichment.shutil.which",
                return_value="/usr/bin/claude",
            ),
            patch(
                "src.enrichment.asyncio.create_subprocess_exec",
                return_value=mock_proc,
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

        with patch("src.enrichment.shutil.which", return_value=None):
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
