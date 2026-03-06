"""Real-CLI integration tests for the stream-json pipeline.

These tests invoke the actual ``claude`` CLI with ``--output-format stream-json``
and verify JSONL log persistence and API endpoint responses.

**Skipped by default** -- requires:
  - ``claude`` CLI on PATH
  - Valid API credentials (ANTHROPIC_API_KEY or Claude session)

Run explicitly with::

    pytest -m cli_integration tests/integration/test_stream_cli.py

"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import OrchestratorSettings, ReviewerConfig, ReviewPipelineConfig
from src.enrichment import generate_task_plan
from src.executors.code_executor import CodeExecutor
from src.models import ExecutorType, Project, Task
from src.review_pipeline import ReviewPipeline

# ---------------------------------------------------------------------------
# Skip if claude CLI is not available
# ---------------------------------------------------------------------------

_HAS_CLAUDE = shutil.which("claude") is not None

pytestmark = [
    pytest.mark.cli_integration,
    pytest.mark.skipif(not _HAS_CLAUDE, reason="claude CLI not on PATH"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stream_log_dir(tmp_path: Path) -> Path:
    """Temporary directory for stream log JSONL files."""
    d = tmp_path / "stream_logs"
    d.mkdir()
    return d


@pytest.fixture
def config(stream_log_dir: Path) -> OrchestratorSettings:
    """OrchestratorSettings pointing stream_log_dir at tmp."""
    return OrchestratorSettings(
        session_timeout_minutes=2,
        subprocess_terminate_grace_seconds=5,
        inactivity_timeout_minutes=0,
        stream_log_dir=stream_log_dir,
    )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """Minimal Project with a real directory as repo_path."""
    return Project(
        id="CLI",
        name="CLITest",
        repo_path=tmp_path,
        executor_type=ExecutorType.CODE,
    )


@pytest.fixture
def task() -> Task:
    """Trivial task for CLI execution."""
    return Task(
        id="CLI:T-P0-INT",
        project_id="CLI",
        local_task_id="T-P0-INT",
        title="Say hello",
        description='Reply with exactly "hello" and nothing else.',
        executor_type=ExecutorType.CODE,
    )


# ---------------------------------------------------------------------------
# Helper: validate JSONL file
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, return list of parsed dicts."""
    events: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                events.append(json.loads(stripped))
    return events


def _find_jsonl(log_dir: Path, pattern: str) -> list[Path]:
    """Find JSONL files matching a glob pattern under log_dir (recursive)."""
    return sorted(log_dir.rglob(pattern))


# ---------------------------------------------------------------------------
# Test: CodeExecutor (execution pipeline)
# ---------------------------------------------------------------------------


class TestExecutionStreamCLI:
    """Real CLI execution via CodeExecutor."""

    async def test_execution_produces_jsonl(
        self,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
        stream_log_dir: Path,
    ) -> None:
        """CodeExecutor.execute() produces a JSONL file with valid events."""
        executor = CodeExecutor(config)
        logs: list[str] = []
        stream_events: list[dict] = []

        result = await executor.execute(
            task,
            project,
            {},
            logs.append,
            on_stream_event=stream_events.append,
        )

        # Execution should succeed
        assert result.success is True, f"Execution failed: {result.error_summary}"

        # Stream events should have been delivered
        assert len(stream_events) > 0, "No stream events received"

        # At least one event should be a result
        event_types = {e.get("type") for e in stream_events}
        assert "result" in event_types, f"No result event; types={event_types}"

        # JSONL file should exist and contain valid JSON events
        jsonl_files = _find_jsonl(stream_log_dir, "stream_*.jsonl")
        assert len(jsonl_files) >= 1, "No JSONL files created"

        persisted = _read_jsonl(jsonl_files[-1])
        assert len(persisted) > 0, "JSONL file is empty"
        assert all(
            isinstance(e, dict) for e in persisted
        ), "Not all JSONL entries are dicts"

        # Raw log file should also exist
        raw_files = _find_jsonl(stream_log_dir, "stream_raw_*.log")
        assert len(raw_files) >= 1, "No raw log file created"


# ---------------------------------------------------------------------------
# Test: generate_task_plan (plan generation pipeline)
# ---------------------------------------------------------------------------


class TestPlanStreamCLI:
    """Real CLI plan generation via generate_task_plan."""

    async def test_plan_produces_jsonl(
        self,
        stream_log_dir: Path,
    ) -> None:
        """generate_task_plan() produces JSONL and returns a plan dict."""
        logs: list[str] = []
        stream_events: list[dict] = []

        plan = await generate_task_plan(
            title="Write a hello world script",
            description="Create a Python script that prints hello world.",
            timeout_minutes=2,
            on_log=logs.append,
            on_stream_event=stream_events.append,
            stream_log_dir=stream_log_dir,
            task_id="CLI:T-P0-PLAN",
        )

        # Plan should contain expected keys
        assert isinstance(plan, dict), f"Plan is not a dict: {type(plan)}"
        assert "plan" in plan or "steps" in plan, f"Plan keys: {list(plan.keys())}"

        # Stream events should have been delivered
        assert len(stream_events) > 0, "No stream events received"

        # JSONL file should exist
        jsonl_files = _find_jsonl(stream_log_dir, "plan_stream_*.jsonl")
        assert len(jsonl_files) >= 1, "No plan JSONL files created"

        persisted = _read_jsonl(jsonl_files[-1])
        assert len(persisted) > 0, "Plan JSONL file is empty"
        assert all(isinstance(e, dict) for e in persisted)


# ---------------------------------------------------------------------------
# Test: ReviewPipeline._call_claude_cli (review pipeline)
# ---------------------------------------------------------------------------


class TestReviewStreamCLI:
    """Real CLI review via ReviewPipeline._call_claude_cli."""

    async def test_review_produces_jsonl(
        self,
        stream_log_dir: Path,
    ) -> None:
        """ReviewPipeline._call_claude_cli produces JSONL and returns output."""
        review_config = ReviewPipelineConfig(
            reviewers=[
                ReviewerConfig(
                    name="test-reviewer",
                    model="claude-sonnet-4-5",
                    focus="general",
                    required=True,
                    max_budget_usd=0.10,
                ),
            ],
            review_timeout_minutes=2,
        )
        pipeline = ReviewPipeline(
            config=review_config,
            threshold=3.0,
            stream_log_dir=stream_log_dir,
        )

        logs: list[str] = []
        stream_events: list[dict] = []

        # Call the CLI directly (bypasses review_task orchestration)
        cli_output = await pipeline._call_claude_cli(
            prompt='Review this plan: "Print hello world". Respond with JSON.',
            system_prompt="You are a code reviewer. Return valid JSON.",
            model="claude-sonnet-4-5",
            max_budget_usd=0.10,
            on_log=logs.append,
            on_stream_event=stream_events.append,
            task_id="CLI:T-P0-REV",
        )

        # CLI output should be a dict
        assert isinstance(cli_output, dict), f"CLI output not dict: {type(cli_output)}"

        # Stream events should have been delivered
        assert len(stream_events) > 0, "No stream events received"

        # JSONL file should exist
        jsonl_files = _find_jsonl(stream_log_dir, "review_stream_*.jsonl")
        assert len(jsonl_files) >= 1, "No review JSONL files created"

        persisted = _read_jsonl(jsonl_files[-1])
        assert len(persisted) > 0, "Review JSONL file is empty"
        assert all(isinstance(e, dict) for e in persisted)


# ---------------------------------------------------------------------------
# Test: stream-log API endpoint with real JSONL
# ---------------------------------------------------------------------------


class TestStreamLogAPIWithRealData:
    """Verify the stream-log API endpoint returns events from real JSONL."""

    async def test_api_returns_events_from_execution(
        self,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
        stream_log_dir: Path,
    ) -> None:
        """After real execution, the API endpoint returns non-empty events."""
        # Step 1: Run a real execution to produce JSONL
        executor = CodeExecutor(config)
        result = await executor.execute(task, project, {}, lambda _: None)
        assert result.success is True, f"Execution failed: {result.error_summary}"

        # Step 2: Verify the API endpoint can read the JSONL
        from fastapi import FastAPI

        from src.api import api_router
        from src.config import OrchestratorConfig

        app = FastAPI()
        app.include_router(api_router)

        mock_task_manager = AsyncMock()
        mock_task_manager.get_task = AsyncMock(return_value=MagicMock())

        orch_config = OrchestratorConfig()
        orch_config.orchestrator.stream_log_dir = stream_log_dir

        app.state.config = orch_config
        app.state.task_manager = mock_task_manager

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/tasks/{task.id}/stream-log")

        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task.id
        assert len(data["events"]) > 0, "API returned zero events"
        assert data["file"] != "", "API returned empty filename"

        # Every event should be a dict with a type field
        for event in data["events"]:
            assert isinstance(event, dict)
