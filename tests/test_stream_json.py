"""Tests for SDK event streaming, JSONL persistence, and API endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import OrchestratorSettings
from src.executors.code_executor import (
    CodeExecutor,
    _strip_ansi,
)
from src.models import ExecutorType, Project, Task
from src.sdk_adapter import ClaudeEvent, ClaudeEventType

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> OrchestratorSettings:
    """Orchestrator settings with stream_log_dir in tmp_path."""
    return OrchestratorSettings(
        session_timeout_minutes=1,
        subprocess_terminate_grace_seconds=2,
        inactivity_timeout_minutes=0,
        stream_log_dir=tmp_path / "logs",
    )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A minimal Project pointing at a temp directory."""
    return Project(
        id="P0",
        name="TestProject",
        repo_path=tmp_path,
        executor_type=ExecutorType.CODE,
    )


@pytest.fixture
def task() -> Task:
    """A minimal Task for testing."""
    return Task(
        id="P0:T-P0-99",
        project_id="P0",
        local_task_id="T-P0-99",
        title="Test task",
        description="Do a test thing",
        executor_type=ExecutorType.CODE,
    )


@pytest.fixture(autouse=True)
def _mock_sdk_available() -> None:
    """Ensure preflight SDK check passes in all test environments."""
    with patch(
        "src.executors.code_executor._is_sdk_available", return_value=True,
    ):
        yield


# ------------------------------------------------------------------
# SDK mock helpers
# ------------------------------------------------------------------


async def _mock_sdk_events(events: list[ClaudeEvent]):
    """Async generator yielding ClaudeEvent objects."""
    for event in events:
        yield event


# ------------------------------------------------------------------
# Integration tests: SDK event streaming + JSONL persistence
# ------------------------------------------------------------------


class TestStreamJsonExecution:
    """Tests for SDK event streaming during execution."""

    @patch("src.executors.code_executor.run_claude_query")
    async def test_on_stream_event_called_with_event_dicts(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """on_stream_event is called for each ClaudeEvent dict."""
        mock_query.return_value = _mock_sdk_events([
            ClaudeEvent(type=ClaudeEventType.TEXT, text="thinking..."),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_USE,
                tool_name="Read",
                tool_input={"file": "x.py"},
                tool_use_id="tu_1",
            ),
            ClaudeEvent(type=ClaudeEventType.RESULT, result_text="done"),
        ])

        executor = CodeExecutor(config)
        logs: list[str] = []
        stream_events: list[dict] = []

        result = await executor.execute(
            task, project, {}, logs.append,
            on_stream_event=stream_events.append,
        )

        assert result.success is True
        assert len(stream_events) == 3
        assert stream_events[0]["type"] == "text"
        assert stream_events[1]["type"] == "tool_use"
        assert stream_events[2]["type"] == "result"

    @patch("src.executors.code_executor.run_claude_query")
    async def test_on_log_called_with_simplified_text(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """on_log is called with simplified text derived from SDK events."""
        mock_query.return_value = _mock_sdk_events([
            ClaudeEvent(type=ClaudeEventType.TEXT, text="hello world"),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_USE,
                tool_name="Bash",
                tool_input={},
                tool_use_id="tu_1",
            ),
            ClaudeEvent(type=ClaudeEventType.RESULT, result_text="done"),
        ])

        executor = CodeExecutor(config)
        logs: list[str] = []

        await executor.execute(task, project, {}, logs.append)

        assert "hello world" in logs
        assert any(line.startswith("[TOOL] Bash(") for line in logs)
        assert "[DONE]" in logs

    @patch("src.executors.code_executor.run_claude_query")
    async def test_jsonl_file_created(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """JSONL file is created at data/logs/{task_id}/stream_*.jsonl."""
        mock_query.return_value = _mock_sdk_events([
            ClaudeEvent(type=ClaudeEventType.TEXT, text="hi"),
            ClaudeEvent(type=ClaudeEventType.RESULT, result_text="done"),
        ])

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        log_dir = config.stream_log_dir / "P0_T-P0-99"
        assert log_dir.is_dir()

        jsonl_files = list(log_dir.glob("stream_*.jsonl"))
        assert len(jsonl_files) == 1

        with open(jsonl_files[0], encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 2
        assert lines[0]["type"] == "text"
        assert lines[1]["type"] == "result"

    @patch("src.executors.code_executor.run_claude_query")
    async def test_on_stream_event_none_is_ok(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """on_stream_event=None (default) doesn't crash."""
        mock_query.return_value = _mock_sdk_events([
            ClaudeEvent(type=ClaudeEventType.RESULT, result_text="done"),
        ])

        executor = CodeExecutor(config)
        result = await executor.execute(task, project, {}, lambda _: None)
        assert result.success is True


# ------------------------------------------------------------------
# API endpoint tests
# ------------------------------------------------------------------


def _make_api_app(tmp_path: Path, task_exists: bool = True):
    """Build a minimal FastAPI app with stream-log endpoint for testing."""
    from fastapi import FastAPI

    from src.api import api_router
    from src.config import OrchestratorConfig

    app = FastAPI()
    app.include_router(api_router)

    mock_task = MagicMock() if task_exists else None
    mock_task_manager = AsyncMock()
    mock_task_manager.get_task = AsyncMock(return_value=mock_task)

    orch_config = OrchestratorConfig()
    orch_config.orchestrator.stream_log_dir = tmp_path / "logs"

    app.state.config = orch_config
    app.state.task_manager = mock_task_manager
    return app


class TestStreamLogEndpoint:
    """Tests for GET /api/tasks/{task_id}/stream-log."""

    async def test_stream_log_returns_events(self, tmp_path: Path) -> None:
        """Endpoint returns parsed events from most recent JSONL."""
        log_dir = tmp_path / "logs" / "P0_T-P0-99"
        log_dir.mkdir(parents=True)
        jsonl_path = log_dir / "stream_20260306T120000.jsonl"
        events = [
            {"type": "assistant", "content": "hi"},
            {"type": "result"},
        ]
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

        app = _make_api_app(tmp_path)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/tasks/P0:T-P0-99/stream-log")

        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "P0:T-P0-99"
        assert data["file"] == "stream_20260306T120000.jsonl"
        assert len(data["events"]) == 2
        assert data["events"][0]["type"] == "assistant"

    async def test_stream_log_no_log_dir(self, tmp_path: Path) -> None:
        """Endpoint returns empty events when no log dir exists."""
        app = _make_api_app(tmp_path)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/tasks/P0:T-P0-99/stream-log")

        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["file"] == ""

    async def test_stream_log_task_not_found(self, tmp_path: Path) -> None:
        """Endpoint returns 404 for unknown task."""
        app = _make_api_app(tmp_path, task_exists=False)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/tasks/P0:T-P0-99/stream-log")

        assert resp.status_code == 404

    async def test_stream_log_picks_most_recent(self, tmp_path: Path) -> None:
        """Endpoint picks the most recent JSONL file."""
        log_dir = tmp_path / "logs" / "P0_T-P0-99"
        log_dir.mkdir(parents=True)

        with open(log_dir / "stream_20260305T100000.jsonl", "w", encoding="utf-8") as f:
            f.write('{"type": "old"}\n')
        with open(log_dir / "stream_20260306T120000.jsonl", "w", encoding="utf-8") as f:
            f.write('{"type": "new"}\n')

        app = _make_api_app(tmp_path)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/tasks/P0:T-P0-99/stream-log")

        data = resp.json()
        assert data["file"] == "stream_20260306T120000.jsonl"
        assert data["events"][0]["type"] == "new"


# ------------------------------------------------------------------
# ANSI stripping tests (standalone utility -- not SDK-dependent)
# ------------------------------------------------------------------


class TestAnsiStripping:
    """Tests for ANSI escape code stripping."""

    def test_strip_ansi_basic(self) -> None:
        """Basic ANSI escape codes are removed."""
        assert _strip_ansi("\x1b[0m hello \x1b[31m") == " hello "

    def test_ansi_wrapped_json_parsed(self) -> None:
        """ANSI-wrapped JSON line is correctly stripped and parsed."""
        ansi_line = '\x1b[0m{"type": "result"}\x1b[0m'
        cleaned = _strip_ansi(ansi_line)
        parsed = json.loads(cleaned)
        assert parsed["type"] == "result"


# ------------------------------------------------------------------
# Bulk event tests
# ------------------------------------------------------------------


class TestBulkEvents:
    """Tests for many SDK events arriving rapidly."""

    @patch("src.executors.code_executor.run_claude_query")
    async def test_many_events_all_captured(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Many SDK events are all captured in JSONL."""
        events = [
            ClaudeEvent(
                type=ClaudeEventType.TOOL_USE,
                tool_name="Read",
                tool_input={"n": i},
                tool_use_id=f"tu_{i}",
            )
            for i in range(20)
        ]
        events.append(
            ClaudeEvent(type=ClaudeEventType.RESULT, result_text="done"),
        )
        mock_query.return_value = _mock_sdk_events(events)

        executor = CodeExecutor(config)
        stream_events: list[dict] = []

        result = await executor.execute(
            task, project, {}, lambda _: None,
            on_stream_event=stream_events.append,
        )

        assert result.success is True
        assert len(stream_events) == 21  # 20 tool_use + 1 result

        # Verify JSONL file has all events
        log_dir = config.stream_log_dir / "P0_T-P0-99"
        jsonl_files = list(log_dir.glob("stream_*.jsonl"))
        with open(jsonl_files[0], encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 21
