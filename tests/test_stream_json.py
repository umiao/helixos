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
)
from src.models import ExecutorType, Project, Task

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
def _mock_claude_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure preflight CLI check passes in all test environments."""
    monkeypatch.setattr(
        "src.executors.code_executor.shutil.which", lambda cmd: "/usr/bin/claude"
    )


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


# ------------------------------------------------------------------
# Stdout mock helpers
# ------------------------------------------------------------------


class _MockStdout:
    """Mock stdout that returns lines via readline(), then b'' for EOF."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)
        self._index = 0

    async def readline(self) -> bytes:
        """Return next line, or b'' when exhausted."""
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return line
        return b""


def _make_mock_proc(
    stdout_lines: list[bytes],
    returncode: int = 0,
    stderr_data: bytes = b"",
) -> MagicMock:
    """Build a mock asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None

    proc.stdout = _MockStdout(stdout_lines)

    stderr_mock = MagicMock()
    stderr_mock.read = AsyncMock(return_value=stderr_data)
    proc.stderr = stderr_mock

    async def _wait() -> int:
        proc.returncode = returncode
        return returncode

    proc.wait = AsyncMock(side_effect=_wait)
    return proc


# ------------------------------------------------------------------
# Integration tests: stream-json parsing + JSONL persistence
# ------------------------------------------------------------------


class TestStreamJsonExecution:
    """Tests for stream-json parsing during execution."""

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_on_stream_event_called_with_parsed_dicts(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """on_stream_event is called for each parsed JSON object."""
        stream_lines = [
            b'{"type": "assistant", "content": "thinking..."}\n',
            b'{"type": "tool_use", "name": "Read", "input": {"file": "x.py"}}\n',
            b'{"type": "result"}\n',
        ]
        proc = _make_mock_proc(stream_lines, returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        stream_events: list[dict] = []

        result = await executor.execute(
            task, project, {}, logs.append,
            on_stream_event=stream_events.append,
        )

        assert result.success is True
        assert len(stream_events) == 3
        assert stream_events[0]["type"] == "assistant"
        assert stream_events[1]["type"] == "tool_use"
        assert stream_events[2]["type"] == "result"

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_on_log_called_with_simplified_text(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """on_log is called with simplified text derived from stream events."""
        stream_lines = [
            b'{"type": "assistant", "content": "hello world"}\n',
            b'{"type": "tool_use", "name": "Bash", "input": {}}\n',
            b'{"type": "result"}\n',
        ]
        proc = _make_mock_proc(stream_lines, returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []

        await executor.execute(task, project, {}, logs.append)

        assert "hello world" in logs
        assert any(line.startswith("[TOOL] Bash(") for line in logs)
        assert "[DONE]" in logs

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_non_json_lines_fall_back_to_on_log(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Non-JSON lines are passed to on_log as raw text (no crash)."""
        stream_lines = [
            b"plain text output\n",
            b'{"type": "result"}\n',
            b"another plain line\n",
        ]
        proc = _make_mock_proc(stream_lines, returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        stream_events: list[dict] = []

        result = await executor.execute(
            task, project, {}, logs.append,
            on_stream_event=stream_events.append,
        )

        assert result.success is True
        assert "plain text output" in logs
        assert "another plain line" in logs
        assert len(stream_events) == 1  # only the JSON line

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_malformed_json_does_not_crash(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Malformed JSON lines don't crash -- they fall back to on_log."""
        stream_lines = [
            b'{"type": "broken json\n',
            b'{"type": "result"}\n',
        ]
        proc = _make_mock_proc(stream_lines, returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        stream_events: list[dict] = []

        result = await executor.execute(
            task, project, {}, logs.append,
            on_stream_event=stream_events.append,
        )

        assert result.success is True
        assert len(stream_events) == 1
        assert '{"type": "broken json' in logs

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_jsonl_file_created(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """JSONL file is created at data/logs/{task_id}/stream_*.jsonl."""
        stream_lines = [
            b'{"type": "assistant", "content": "hi"}\n',
            b'{"type": "result"}\n',
        ]
        proc = _make_mock_proc(stream_lines, returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        log_dir = config.stream_log_dir / "P0_T-P0-99"
        assert log_dir.is_dir()

        jsonl_files = list(log_dir.glob("stream_*.jsonl"))
        assert len(jsonl_files) == 1

        with open(jsonl_files[0], encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 2
        assert lines[0]["type"] == "assistant"
        assert lines[1]["type"] == "result"

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_on_stream_event_none_is_ok(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """on_stream_event=None (default) doesn't crash."""
        stream_lines = [
            b'{"type": "result"}\n',
        ]
        proc = _make_mock_proc(stream_lines, returncode=0)
        mock_exec.return_value = proc

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
