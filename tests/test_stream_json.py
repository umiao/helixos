"""Tests for stream-json buffer, simplified text derivation, JSONL persistence, and API endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import OrchestratorSettings
from src.executors.code_executor import (
    CodeExecutor,
    _simplify_stream_event,
    _StreamJsonBuffer,
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
# _StreamJsonBuffer tests
# ------------------------------------------------------------------


class TestStreamJsonBuffer:
    """Tests for the _StreamJsonBuffer class."""

    def test_single_json_line(self) -> None:
        """A complete JSON line is parsed and returned."""
        buf = _StreamJsonBuffer()
        result = buf.feed('{"type": "assistant", "content": "hello"}\n')
        assert len(result) == 1
        assert result[0]["type"] == "assistant"
        assert result[0]["content"] == "hello"
        assert buf.non_json == []

    def test_multiple_json_lines(self) -> None:
        """Multiple JSON lines in one feed are all parsed."""
        buf = _StreamJsonBuffer()
        text = '{"type": "assistant"}\n{"type": "tool_use"}\n'
        result = buf.feed(text)
        assert len(result) == 2
        assert result[0]["type"] == "assistant"
        assert result[1]["type"] == "tool_use"

    def test_non_json_line(self) -> None:
        """Non-JSON lines are captured in non_json."""
        buf = _StreamJsonBuffer()
        result = buf.feed("plain text line\n")
        assert len(result) == 0
        assert buf.non_json == ["plain text line"]

    def test_mixed_json_and_non_json(self) -> None:
        """Mix of JSON and non-JSON lines."""
        buf = _StreamJsonBuffer()
        text = 'some text\n{"type": "result"}\nmore text\n'
        result = buf.feed(text)
        assert len(result) == 1
        assert result[0]["type"] == "result"
        assert buf.non_json == ["some text", "more text"]

    def test_split_line_across_feeds(self) -> None:
        """Partial line is buffered and completed on next feed."""
        buf = _StreamJsonBuffer()
        result1 = buf.feed('{"type": "assis')
        assert len(result1) == 0  # partial, no newline
        result2 = buf.feed('tant"}\n')
        assert len(result2) == 1
        assert result2[0]["type"] == "assistant"

    def test_empty_lines_ignored(self) -> None:
        """Empty lines are ignored."""
        buf = _StreamJsonBuffer()
        result = buf.feed("\n\n\n")
        assert len(result) == 0
        assert buf.non_json == []

    def test_non_dict_json_goes_to_non_json(self) -> None:
        """JSON arrays or primitives go to non_json."""
        buf = _StreamJsonBuffer()
        result = buf.feed('[1, 2, 3]\n')
        assert len(result) == 0
        assert buf.non_json == ["[1, 2, 3]"]


# ------------------------------------------------------------------
# _simplify_stream_event tests
# ------------------------------------------------------------------


class TestSimplifyStreamEvent:
    """Tests for _simplify_stream_event."""

    def test_assistant_text_string(self) -> None:
        """Assistant event with string content."""
        result = _simplify_stream_event({"type": "assistant", "content": "Hello world"})
        assert result == "Hello world"

    def test_assistant_text_blocks(self) -> None:
        """Assistant event with content blocks."""
        event = {
            "type": "assistant",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": "world"},
            ],
        }
        result = _simplify_stream_event(event)
        assert result == "Hello world"

    def test_tool_use(self) -> None:
        """Tool use event produces [TOOL] tag."""
        event = {
            "type": "tool_use",
            "name": "Read",
            "input": {"file": "test.py"},
        }
        result = _simplify_stream_event(event)
        assert result is not None
        assert result.startswith("[TOOL] Read(")
        assert "test.py" in result

    def test_tool_result(self) -> None:
        """Tool result event produces [RESULT] tag."""
        event = {"type": "tool_result", "content": "file contents here"}
        result = _simplify_stream_event(event)
        assert result is not None
        assert result.startswith("[RESULT] ")
        assert "file contents here" in result

    def test_tool_result_long_content_truncated(self) -> None:
        """Long tool result content is truncated."""
        event = {"type": "tool_result", "content": "x" * 300}
        result = _simplify_stream_event(event)
        assert result is not None
        assert len(result) < 220
        assert "..." in result

    def test_result_event(self) -> None:
        """Result event produces [DONE]."""
        result = _simplify_stream_event({"type": "result"})
        assert result == "[DONE]"

    def test_content_block_delta(self) -> None:
        """Content block delta with text."""
        event = {"type": "content_block_delta", "delta": {"text": "partial"}}
        result = _simplify_stream_event(event)
        assert result == "partial"

    def test_unknown_event_returns_none(self) -> None:
        """Unknown event types return None."""
        result = _simplify_stream_event({"type": "ping"})
        assert result is None

    def test_assistant_empty_content(self) -> None:
        """Assistant with empty content returns None."""
        result = _simplify_stream_event({"type": "assistant", "content": ""})
        assert result is None

    def test_stream_event_text_delta(self) -> None:
        """stream_event with nested delta text is extracted."""
        event = {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "streaming chunk"},
            },
        }
        result = _simplify_stream_event(event)
        assert result == "streaming chunk"

    def test_stream_event_empty_delta(self) -> None:
        """stream_event with no text in delta returns None."""
        event = {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta"}},
        }
        result = _simplify_stream_event(event)
        assert result is None

    def test_stream_event_no_event_key(self) -> None:
        """stream_event without 'event' key returns None."""
        result = _simplify_stream_event({"type": "stream_event"})
        assert result is None

    def test_system_init_event(self) -> None:
        """system init event produces [INIT] tag with model."""
        event = {
            "type": "system",
            "subtype": "init",
            "model": "claude-sonnet-4-6",
        }
        result = _simplify_stream_event(event)
        assert result == "[INIT] model=claude-sonnet-4-6"

    def test_system_non_init_returns_none(self) -> None:
        """system event without init subtype returns None."""
        result = _simplify_stream_event({"type": "system", "subtype": "other"})
        assert result is None

    def test_user_event_returns_none(self) -> None:
        """user event is suppressed."""
        result = _simplify_stream_event({"type": "user", "content": "some prompt"})
        assert result is None

    def test_rate_limit_event_returns_none(self) -> None:
        """rate_limit_event is suppressed."""
        event = {"type": "rate_limit_event", "retry_after": 5}
        result = _simplify_stream_event(event)
        assert result is None


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
        """ANSI-wrapped JSON line is correctly parsed by buffer."""
        buf = _StreamJsonBuffer()
        ansi_line = '\x1b[0m{"type": "result"}\x1b[0m'
        cleaned = _strip_ansi(ansi_line).rstrip("\n")
        result = buf.feed(cleaned + "\n")
        assert len(result) == 1
        assert result[0]["type"] == "result"


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
