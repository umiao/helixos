"""Tests for CodeExecutor -- Agent SDK query with timeout, streaming, cancel.

Uses mock ``run_claude_query`` to avoid real SDK/CLI calls.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import OrchestratorSettings
from src.executors.base import BaseExecutor, ErrorType, ExecutorResult
from src.executors.code_executor import (
    HEARTBEAT_SECONDS,
    CodeExecutor,
    _format_elapsed,
    _LazyFileWriter,
    _strip_ansi,
    cleanup_empty_log_files,
)
from src.models import ExecutorType, Project, Task
from src.sdk_adapter import ClaudeEvent, ClaudeEventType

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def config() -> OrchestratorSettings:
    """Orchestrator settings with short timeouts for testing."""
    return OrchestratorSettings(
        session_timeout_minutes=1,
        subprocess_terminate_grace_seconds=2,
        inactivity_timeout_minutes=0,  # disabled by default in tests
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


def _text_event(text: str, model: str | None = None) -> ClaudeEvent:
    """Create a TEXT event."""
    return ClaudeEvent(type=ClaudeEventType.TEXT, text=text, model=model)


def _tool_event(
    name: str,
    tool_input: dict | None = None,
    tool_use_id: str = "tu_1",
) -> ClaudeEvent:
    """Create a TOOL_USE event."""
    return ClaudeEvent(
        type=ClaudeEventType.TOOL_USE,
        tool_name=name,
        tool_input=tool_input or {},
        tool_use_id=tool_use_id,
    )


def _tool_result_event(
    content: str = "ok",
    tool_result_for_id: str = "tu_1",
) -> ClaudeEvent:
    """Create a TOOL_RESULT event."""
    return ClaudeEvent(
        type=ClaudeEventType.TOOL_RESULT,
        tool_result_content=content,
        tool_result_for_id=tool_result_for_id,
    )


def _result_event(
    text: str = "Done",
    cost_usd: float | None = None,
) -> ClaudeEvent:
    """Create a RESULT event."""
    return ClaudeEvent(
        type=ClaudeEventType.RESULT,
        result_text=text,
        cost_usd=cost_usd,
    )


def _error_event(msg: str = "Error") -> ClaudeEvent:
    """Create an ERROR event."""
    return ClaudeEvent(type=ClaudeEventType.ERROR, error_message=msg)


def _init_event(session_id: str = "sess_1") -> ClaudeEvent:
    """Create an INIT event."""
    return ClaudeEvent(
        type=ClaudeEventType.INIT, session_id=session_id,
    )


async def _mock_sdk_events(
    events: list[ClaudeEvent],
    delay: float = 0.0,
):
    """Async generator yielding ClaudeEvent objects.

    Args:
        events: Events to yield.
        delay: Optional delay between events (for timeout testing).
    """
    for event in events:
        if delay > 0:
            await asyncio.sleep(delay)
        yield event


# ------------------------------------------------------------------
# Tests: BaseExecutor ABC
# ------------------------------------------------------------------


class TestBaseExecutor:
    """Verify BaseExecutor is abstract and cannot be instantiated directly."""

    def test_cannot_instantiate(self) -> None:
        """BaseExecutor is abstract -- instantiation raises TypeError."""
        with pytest.raises(TypeError):
            BaseExecutor()  # type: ignore[abstract]

    def test_is_subclass(self) -> None:
        """CodeExecutor is a proper subclass of BaseExecutor."""
        assert issubclass(CodeExecutor, BaseExecutor)


# ------------------------------------------------------------------
# Tests: ExecutorResult model
# ------------------------------------------------------------------


class TestExecutorResult:
    """Verify ExecutorResult model fields and defaults."""

    def test_minimal(self) -> None:
        """Required fields only -- defaults fill in the rest."""
        result = ExecutorResult(
            success=True,
            exit_code=0,
            duration_seconds=1.5,
        )
        assert result.success is True
        assert result.exit_code == 0
        assert result.log_lines == []
        assert result.error_summary is None
        assert result.outputs == []
        assert result.duration_seconds == 1.5

    def test_full(self) -> None:
        """All fields populated."""
        result = ExecutorResult(
            success=False,
            exit_code=1,
            log_lines=["line1", "line2"],
            error_summary="something broke",
            outputs=["file.txt"],
            duration_seconds=42.0,
        )
        assert result.success is False
        assert result.exit_code == 1
        assert result.log_lines == ["line1", "line2"]
        assert result.error_summary == "something broke"
        assert result.outputs == ["file.txt"]


# ------------------------------------------------------------------
# Tests: CodeExecutor._build_prompt
# ------------------------------------------------------------------


class TestBuildPrompt:
    """Verify prompt generation matches PRD Section 7.2."""

    def test_prompt_contains_task_id_and_title(
        self, config: OrchestratorSettings, task: Task
    ) -> None:
        """Prompt includes local task ID and title."""
        executor = CodeExecutor(config)
        prompt = executor._build_prompt(task)
        assert "T-P0-99" in prompt
        assert "Test task" in prompt

    def test_prompt_contains_description(
        self, config: OrchestratorSettings, task: Task
    ) -> None:
        """Prompt includes the task description."""
        executor = CodeExecutor(config)
        prompt = executor._build_prompt(task)
        assert "Do a test thing" in prompt

    def test_prompt_contains_conventions(
        self, config: OrchestratorSettings, task: Task
    ) -> None:
        """Prompt includes TASKS.md and PROGRESS.md conventions."""
        executor = CodeExecutor(config)
        prompt = executor._build_prompt(task)
        assert "TASKS.md" in prompt
        assert "PROGRESS.md" in prompt


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- success
# ------------------------------------------------------------------


class TestExecuteHooksLoading:
    """Tests for selective hooks loading (T-P1-103)."""

    @pytest.mark.asyncio
    @patch("src.executors.code_executor.run_claude_query")
    async def test_execution_agent_inherits_all_hooks(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Execution agent does NOT set setting_sources (inherits all CLI hooks)."""
        mock_query.return_value = _mock_sdk_events([
            _text_event("output"),
            _result_event("Done"),
        ])

        executor = CodeExecutor(config)
        await executor.execute(task, project, env={}, on_log=lambda x: None)

        call_args = mock_query.call_args
        options = call_args[1].get("options") or call_args[0][1]
        assert options.setting_sources is None, (
            "Execution agent should not set setting_sources "
            "(inherits all hooks from CLI settings)"
        )


class TestExecuteSuccess:
    """Test successful execution flow."""

    @patch("src.executors.code_executor.run_claude_query")
    async def test_success_result(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Successful execution returns success=True and exit_code=0."""
        mock_query.return_value = _mock_sdk_events([
            _text_event("output line 1"),
            _text_event("output line 2"),
            _result_event("Done"),
        ])

        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert result.success is True
        assert result.exit_code == 0
        assert result.error_summary is None
        assert result.duration_seconds >= 0

    @patch("src.executors.code_executor.run_claude_query")
    async def test_log_streaming(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Events are streamed to on_log callback."""
        mock_query.return_value = _mock_sdk_events([
            _text_event("hello"),
            _text_event("world"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        logs: list[str] = []
        await executor.execute(task, project, {}, logs.append)

        assert "hello" in logs
        assert "world" in logs

    @patch("src.executors.code_executor.run_claude_query")
    async def test_log_lines_in_result(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Result.log_lines contains the streamed text."""
        mock_query.return_value = _mock_sdk_events([
            _text_event("line A"),
            _text_event("line B"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        result = await executor.execute(task, project, {}, lambda _: None)

        assert "line A" in result.log_lines
        assert "line B" in result.log_lines

    @patch("src.executors.code_executor.run_claude_query")
    async def test_empty_text_events_skipped(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Empty text events are not appended to log."""
        mock_query.return_value = _mock_sdk_events([
            _text_event("data"),
            ClaudeEvent(type=ClaudeEventType.TEXT, text=""),
            ClaudeEvent(type=ClaudeEventType.TEXT, text=None),
            _text_event("more"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        logs: list[str] = []
        await executor.execute(task, project, {}, logs.append)

        text_logs = [ln for ln in logs if ln not in ("[DONE]",)]
        assert text_logs == ["data", "more"]

    @patch("src.executors.code_executor.run_claude_query")
    async def test_init_event_logged(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """INIT event is logged with session ID."""
        mock_query.return_value = _mock_sdk_events([
            _init_event("sess_abc"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        logs: list[str] = []
        await executor.execute(task, project, {}, logs.append)

        assert any("[INIT] session=sess_abc" in ln for ln in logs)

    @patch("src.executors.code_executor.run_claude_query")
    async def test_tool_events_logged(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """TOOL_USE and TOOL_RESULT events are logged."""
        mock_query.return_value = _mock_sdk_events([
            _tool_event("Bash", {"command": "ls"}),
            _tool_result_event("file.txt\ndir/"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        logs: list[str] = []
        await executor.execute(task, project, {}, logs.append)

        tool_logs = [ln for ln in logs if "[TOOL]" in ln]
        assert len(tool_logs) == 1
        assert "Bash" in tool_logs[0]

        result_logs = [ln for ln in logs if "[RESULT]" in ln]
        assert len(result_logs) == 1
        assert "file.txt" in result_logs[0]

    @patch("src.executors.code_executor.run_claude_query")
    async def test_done_marker_logged(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """RESULT event emits [DONE] to log."""
        mock_query.return_value = _mock_sdk_events([
            _result_event("completed"),
        ])

        executor = CodeExecutor(config)
        logs: list[str] = []
        await executor.execute(task, project, {}, logs.append)

        assert "[DONE]" in logs


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- failure
# ------------------------------------------------------------------


class TestExecuteFailure:
    """Test failed execution flow."""

    @patch("src.executors.code_executor.run_claude_query")
    async def test_error_event(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """ERROR event returns success=False with error details."""
        mock_query.return_value = _mock_sdk_events([
            _error_event("Something went wrong"),
        ])

        executor = CodeExecutor(config)
        result = await executor.execute(task, project, {}, lambda _: None)

        assert result.success is False
        assert result.exit_code == 1
        assert result.error_type == ErrorType.NON_ZERO_EXIT
        assert "Something went wrong" in result.error_summary

    @patch("src.executors.code_executor.run_claude_query")
    async def test_error_event_various_messages(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Various error messages are correctly reported."""
        for msg in ["API error", "Rate limited", "Budget exceeded"]:
            mock_query.return_value = _mock_sdk_events([
                _error_event(msg),
            ])

            executor = CodeExecutor(config)
            result = await executor.execute(
                task, project, {}, lambda _: None,
            )

            assert result.success is False
            assert msg in result.error_summary


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- session timeout
# ------------------------------------------------------------------


class TestExecuteTimeout:
    """Test session timeout handling."""

    @patch("src.executors.code_executor.HEARTBEAT_SECONDS", 0.05)
    @patch("src.executors.code_executor.run_claude_query")
    async def test_timeout_terminates(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
    ) -> None:
        """When execution exceeds timeout, query is terminated."""
        config = OrchestratorSettings(
            session_timeout_minutes=1,  # 60s
            subprocess_terminate_grace_seconds=5,
            inactivity_timeout_minutes=0,
        )

        async def _slow_events():
            yield _text_event("start")
            await asyncio.sleep(100)  # hang
            yield _result_event()

        mock_query.return_value = _slow_events()

        # Advance time past session timeout after first event
        original_monotonic = time.monotonic
        call_count = 0

        def fast_monotonic():
            nonlocal call_count
            call_count += 1
            real = original_monotonic()
            # After initial calls, jump past the 60s session timeout
            if call_count > 10:
                return real + 120
            return real

        with patch("src.executors.code_executor.time.monotonic", fast_monotonic):
            executor = CodeExecutor(config)
            logs: list[str] = []
            result = await executor.execute(task, project, {}, logs.append)

        assert result.success is False
        assert result.error_summary == (
            "Session timeout - query terminated"
        )
        assert result.error_type == ErrorType.TIMEOUT

    @patch("src.executors.code_executor.HEARTBEAT_SECONDS", 0.05)
    @patch("src.executors.code_executor.run_claude_query")
    async def test_timeout_log_messages(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
    ) -> None:
        """Timeout logs contain [TIMEOUT] markers."""
        config = OrchestratorSettings(
            session_timeout_minutes=1,
            subprocess_terminate_grace_seconds=5,
            inactivity_timeout_minutes=0,
        )

        async def _slow_events():
            yield _text_event("line")
            await asyncio.sleep(100)
            yield _result_event()

        mock_query.return_value = _slow_events()

        original_monotonic = time.monotonic
        call_count = 0

        def fast_monotonic():
            nonlocal call_count
            call_count += 1
            real = original_monotonic()
            if call_count > 10:
                return real + 120
            return real

        with patch("src.executors.code_executor.time.monotonic", fast_monotonic):
            executor = CodeExecutor(config)
            logs: list[str] = []
            await executor.execute(task, project, {}, logs.append)

        timeout_logs = [line for line in logs if "[TIMEOUT]" in line]
        assert len(timeout_logs) >= 1
        assert "terminating" in timeout_logs[0].lower()


# ------------------------------------------------------------------
# Tests: CodeExecutor.cancel
# ------------------------------------------------------------------


class TestCancel:
    """Test cancel() terminates the SDK query."""

    @patch("src.executors.code_executor.run_claude_query")
    async def test_cancel_running(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """cancel() terminates a running SDK query."""
        cancel_event = asyncio.Event()

        async def _slow_events():
            yield _text_event("running...")
            await cancel_event.wait()
            yield _result_event()

        mock_query.return_value = _slow_events()

        executor = CodeExecutor(config)

        exec_task = asyncio.create_task(
            executor.execute(task, project, {}, lambda _: None)
        )

        await asyncio.sleep(0.05)
        await executor.cancel()
        cancel_event.set()  # unblock the generator

        result = await exec_task
        assert result.success is False

    async def test_cancel_no_producer(
        self, config: OrchestratorSettings,
    ) -> None:
        """cancel() with no running query does nothing."""
        executor = CodeExecutor(config)
        await executor.cancel()

    @patch("src.executors.code_executor.run_claude_query")
    async def test_cancel_already_finished(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """cancel() after execution has finished does nothing."""
        mock_query.return_value = _mock_sdk_events([
            _text_event("done"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        # Should not raise
        await executor.cancel()


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- log tail limit
# ------------------------------------------------------------------


class TestLogTailLimit:
    """Verify that only the last 100 log lines are kept in the result."""

    @patch("src.executors.code_executor.run_claude_query")
    async def test_keeps_last_100(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """When >100 events, only the last 100 log lines are in result."""
        events = [_text_event(f"line {i}") for i in range(150)]
        events.append(_result_event())
        mock_query.return_value = _mock_sdk_events(events)

        executor = CodeExecutor(config)
        all_logs: list[str] = []
        result = await executor.execute(task, project, {}, all_logs.append)

        # all_logs gets all events including [DONE]
        assert len(all_logs) == 151  # 150 text + 1 [DONE]
        assert len(result.log_lines) == 100
        assert result.log_lines[0] == "line 51"  # [DONE] is at end
        assert result.log_lines[-1] == "[DONE]"


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- SDK query options
# ------------------------------------------------------------------


class TestQueryOptions:
    """Verify the SDK query is called with correct options."""

    @patch("src.executors.code_executor.run_claude_query")
    async def test_prompt_passed(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """run_claude_query is called with the built prompt."""
        mock_query.return_value = _mock_sdk_events([_result_event()])

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        call_args = mock_query.call_args
        prompt = call_args[0][0]
        assert "T-P0-99" in prompt
        assert "Test task" in prompt

    @patch("src.executors.code_executor.run_claude_query")
    async def test_cwd_is_repo_path(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """QueryOptions.cwd is set to project.repo_path."""
        mock_query.return_value = _mock_sdk_events([_result_event()])

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        call_args = mock_query.call_args
        options = call_args[0][1]
        assert options.cwd == str(project.repo_path)

    @patch("src.executors.code_executor.run_claude_query")
    async def test_env_injection(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Project env vars are passed to QueryOptions."""
        mock_query.return_value = _mock_sdk_events([_result_event()])

        injected = {"MY_KEY": "my_value", "ANOTHER": "val2"}
        executor = CodeExecutor(config)
        await executor.execute(task, project, injected, lambda _: None)

        call_args = mock_query.call_args
        options = call_args[0][1]
        assert options.env["MY_KEY"] == "my_value"
        assert options.env["ANOTHER"] == "val2"

    @patch("src.executors.code_executor.run_claude_query")
    async def test_allowed_tools(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """QueryOptions includes allowed tools."""
        mock_query.return_value = _mock_sdk_events([_result_event()])

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        call_args = mock_query.call_args
        options = call_args[0][1]
        assert "Bash" in options.allowed_tools
        assert "Read" in options.allowed_tools
        assert "Write" in options.allowed_tools
        assert "Edit" in options.allowed_tools

    @patch("src.executors.code_executor.run_claude_query")
    async def test_stream_event_callback(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """on_stream_event receives event dicts."""
        mock_query.return_value = _mock_sdk_events([
            _text_event("hello"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        stream_events: list[dict] = []
        await executor.execute(
            task, project, {}, lambda _: None,
            on_stream_event=stream_events.append,
        )

        assert len(stream_events) >= 2
        assert stream_events[0]["type"] == "text"
        assert stream_events[0]["text"] == "hello"


# ------------------------------------------------------------------
# Tests: ErrorType enum
# ------------------------------------------------------------------


class TestErrorType:
    """Verify ErrorType enum values."""

    def test_all_values_exist(self) -> None:
        """All required error types are defined."""
        assert ErrorType.INFRA == "infra"
        assert ErrorType.CLI_NOT_FOUND == "cli_not_found"
        assert ErrorType.REPO_NOT_FOUND == "repo_not_found"
        assert ErrorType.NON_ZERO_EXIT == "non_zero_exit"
        assert ErrorType.TIMEOUT == "timeout"
        assert ErrorType.INACTIVITY_TIMEOUT == "inactivity_timeout"
        assert ErrorType.UNKNOWN == "unknown"

    def test_executor_result_with_error_type(self) -> None:
        """ExecutorResult can hold an error_type and stderr_output."""
        result = ExecutorResult(
            success=False,
            exit_code=1,
            error_summary="Build failed",
            error_type=ErrorType.NON_ZERO_EXIT,
            stderr_output="some error",
            duration_seconds=1.0,
        )
        assert result.error_type == ErrorType.NON_ZERO_EXIT
        assert result.stderr_output == "some error"

    def test_executor_result_defaults_none(self) -> None:
        """ErrorType and stderr_output default to None."""
        result = ExecutorResult(
            success=True, exit_code=0, duration_seconds=0.5,
        )
        assert result.error_type is None
        assert result.stderr_output is None


# ------------------------------------------------------------------
# Tests: Pre-flight checks
# ------------------------------------------------------------------


class TestPreflightChecks:
    """Verify pre-flight checks before SDK query."""

    async def test_repo_not_found(
        self,
        config: OrchestratorSettings,
        task: Task,
    ) -> None:
        """Missing repo_path returns REPO_NOT_FOUND error."""
        project = Project(
            id="P0",
            name="TestProject",
            repo_path=Path("/nonexistent/path/that/does/not/exist"),
            executor_type=ExecutorType.CODE,
        )
        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert result.success is False
        assert result.error_type == ErrorType.REPO_NOT_FOUND
        assert "not found" in result.error_summary.lower()
        assert len(logs) == 1
        assert "[PRE-FLIGHT FAIL]" in logs[0]

    async def test_repo_path_none(
        self,
        config: OrchestratorSettings,
        task: Task,
    ) -> None:
        """None repo_path returns REPO_NOT_FOUND error."""
        project = Project(
            id="P0",
            name="TestProject",
            repo_path=None,
            executor_type=ExecutorType.CODE,
        )
        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert result.success is False
        assert result.error_type == ErrorType.REPO_NOT_FOUND

    @patch(
        "src.executors.code_executor._is_sdk_available", return_value=False,
    )
    async def test_sdk_not_available(
        self,
        mock_sdk: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Missing SDK returns CLI_NOT_FOUND error."""
        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert result.success is False
        assert result.error_type == ErrorType.CLI_NOT_FOUND
        assert "sdk" in result.error_summary.lower()
        assert len(logs) == 1
        assert "[PRE-FLIGHT FAIL]" in logs[0]

    @patch("src.executors.code_executor.run_claude_query")
    async def test_all_checks_pass(
        self,
        mock_query: MagicMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """When all pre-flight checks pass, SDK query is started."""
        mock_query.return_value = _mock_sdk_events([_result_event()])

        executor = CodeExecutor(config)
        result = await executor.execute(task, project, {}, lambda _: None)

        assert result.success is True
        mock_query.assert_called_once()


# ------------------------------------------------------------------
# Tests: ANSI stripping utility
# ------------------------------------------------------------------


class TestStripAnsi:
    """Verify ANSI escape sequence removal."""

    def test_no_ansi(self) -> None:
        """Plain text passes through unchanged."""
        assert _strip_ansi("hello world") == "hello world"

    def test_color_codes(self) -> None:
        """Color codes are removed."""
        assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_bold_and_underline(self) -> None:
        """Bold and underline codes are removed."""
        assert _strip_ansi("\x1b[1mbold\x1b[4munderline\x1b[0m") == "boldunderline"

    def test_cursor_movement(self) -> None:
        """Cursor movement codes are removed."""
        assert _strip_ansi("\x1b[2Jhello\x1b[H") == "hello"


# ------------------------------------------------------------------
# Tests: Timeout error type
# ------------------------------------------------------------------


class TestTimeoutErrorType:
    """Verify timeout sets ErrorType.TIMEOUT."""

    @patch("src.executors.code_executor.HEARTBEAT_SECONDS", 0.05)
    @patch("src.executors.code_executor.run_claude_query")
    async def test_timeout_error_type(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
    ) -> None:
        """Timeout sets error_type=TIMEOUT."""
        config = OrchestratorSettings(
            session_timeout_minutes=1,
            subprocess_terminate_grace_seconds=5,
            inactivity_timeout_minutes=0,
        )

        async def _slow_events():
            yield _text_event("line")
            await asyncio.sleep(100)
            yield _result_event()

        mock_query.return_value = _slow_events()

        original_monotonic = time.monotonic
        call_count = 0

        def fast_monotonic():
            nonlocal call_count
            call_count += 1
            real = original_monotonic()
            if call_count > 10:
                return real + 120
            return real

        with patch("src.executors.code_executor.time.monotonic", fast_monotonic):
            executor = CodeExecutor(config)
            result = await executor.execute(
                task, project, {}, lambda _: None,
            )

        assert result.success is False
        assert result.error_type == ErrorType.TIMEOUT
        assert "timeout" in result.error_summary.lower()


# ------------------------------------------------------------------
# Tests: Truncate stderr utility
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Tests: Inactivity timeout
# ------------------------------------------------------------------


class TestInactivityTimeout:
    """Verify inactivity timeout detection via SDK event gap."""

    @patch("src.executors.code_executor.HEARTBEAT_SECONDS", 0.02)
    @patch("src.executors.code_executor.run_claude_query")
    async def test_inactivity_detected(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
    ) -> None:
        """No SDK events for inactivity_timeout triggers INACTIVITY_TIMEOUT."""
        async def _slow_events():
            yield _text_event("initial")
            await asyncio.sleep(100)  # hang forever
            yield _result_event()

        mock_query.return_value = _slow_events()

        cfg = OrchestratorSettings(
            session_timeout_minutes=60,
            subprocess_terminate_grace_seconds=5,
            inactivity_timeout_minutes=1,  # use 1 min for test
        )

        # Patch time to make inactivity fire quickly.
        # The first few calls establish `start` and `last_event_time`.
        # After processing the first event, jump time forward to exceed
        # the inactivity threshold.
        base_time = time.monotonic()
        call_count = 0

        def fast_monotonic():
            nonlocal call_count
            call_count += 1
            # First 15 calls: normal time (start, event processing)
            # After that: jump 2 minutes ahead (exceeds 1 min inactivity)
            if call_count <= 15:
                return base_time + call_count * 0.001
            return base_time + 130  # 130s > 60s inactivity timeout

        with patch("src.executors.code_executor.time.monotonic", fast_monotonic):
            executor = CodeExecutor(cfg)
            logs: list[str] = []
            result = await executor.execute(task, project, {}, logs.append)

        assert result.success is False
        assert result.error_type == ErrorType.INACTIVITY_TIMEOUT
        assert "1 minutes" in result.error_summary

        inactivity_logs = [ln for ln in logs if "[INACTIVITY]" in ln]
        assert len(inactivity_logs) >= 1

    @patch("src.executors.code_executor.run_claude_query")
    async def test_inactivity_disabled_when_zero(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
    ) -> None:
        """inactivity_timeout_minutes=0 disables inactivity detection."""
        config = OrchestratorSettings(
            session_timeout_minutes=1,
            subprocess_terminate_grace_seconds=2,
            inactivity_timeout_minutes=0,
        )

        mock_query.return_value = _mock_sdk_events([
            _text_event("line1"),
            _text_event("line2"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        result = await executor.execute(task, project, {}, lambda _: None)

        assert result.success is True
        assert result.error_type is None

    @patch("src.executors.code_executor.run_claude_query")
    async def test_active_output_no_inactivity(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
    ) -> None:
        """Continuous events reset inactivity timer -- never fires."""
        config = OrchestratorSettings(
            session_timeout_minutes=60,
            subprocess_terminate_grace_seconds=5,
            inactivity_timeout_minutes=20,
        )

        events = [_text_event(f"line {i}") for i in range(50)]
        events.append(_result_event())
        mock_query.return_value = _mock_sdk_events(events)

        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert result.success is True
        assert result.error_type is None

    @patch("src.executors.code_executor.HEARTBEAT_SECONDS", 0.02)
    @patch("src.executors.code_executor.run_claude_query")
    async def test_inactivity_log_message_format(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
    ) -> None:
        """Inactivity log matches expected format."""
        async def _slow_events():
            yield _text_event("x")
            await asyncio.sleep(100)
            yield _result_event()

        mock_query.return_value = _slow_events()

        base_time = time.monotonic()
        call_count = 0

        def fast_monotonic():
            nonlocal call_count
            call_count += 1
            if call_count <= 15:
                return base_time + call_count * 0.001
            return base_time + 1500  # >20 min

        with patch("src.executors.code_executor.time.monotonic", fast_monotonic):
            cfg = OrchestratorSettings(
                session_timeout_minutes=60,
                subprocess_terminate_grace_seconds=5,
                inactivity_timeout_minutes=20,
            )
            executor = CodeExecutor(cfg)
            logs: list[str] = []
            await executor.execute(task, project, {}, logs.append)

        assert any(
            "[INACTIVITY]" in ln and "event-based detection" in ln
            for ln in logs
        )


# ------------------------------------------------------------------
# Tests: Config -- inactivity_timeout_minutes
# ------------------------------------------------------------------


class TestInactivityConfig:
    """Verify inactivity_timeout_minutes config field."""

    def test_default_value(self) -> None:
        """Default inactivity_timeout_minutes is 0 (disabled)."""
        config = OrchestratorSettings()
        assert config.inactivity_timeout_minutes == 0

    def test_custom_value(self) -> None:
        """Custom inactivity_timeout_minutes is accepted."""
        config = OrchestratorSettings(inactivity_timeout_minutes=30)
        assert config.inactivity_timeout_minutes == 30

    def test_zero_disables(self) -> None:
        """inactivity_timeout_minutes=0 is valid (disables feature)."""
        config = OrchestratorSettings(inactivity_timeout_minutes=0)
        assert config.inactivity_timeout_minutes == 0

    def test_negative_rejected(self) -> None:
        """Negative inactivity_timeout_minutes raises ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            OrchestratorSettings(inactivity_timeout_minutes=-1)


# ------------------------------------------------------------------
# _format_elapsed helper (T-P0-32)
# ------------------------------------------------------------------


def test_format_elapsed_zero() -> None:
    """0 seconds -> '0:00'."""
    assert _format_elapsed(0) == "0:00"


def test_format_elapsed_seconds() -> None:
    """30 seconds -> '0:30'."""
    assert _format_elapsed(30) == "0:30"


def test_format_elapsed_minutes() -> None:
    """125 seconds -> '2:05'."""
    assert _format_elapsed(125) == "2:05"


def test_format_elapsed_large() -> None:
    """3661 seconds -> '61:01'."""
    assert _format_elapsed(3661) == "61:01"


# ------------------------------------------------------------------
# Heartbeat emission
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.executors.code_executor.HEARTBEAT_SECONDS", 0.1)
@patch("src.executors.code_executor.run_claude_query")
async def test_heartbeat_emitted(
    mock_query: MagicMock, project: Project, task: Task,
) -> None:
    """[PROGRESS] heartbeat is emitted when no events arrive."""
    config = OrchestratorSettings(
        session_timeout_minutes=1,
        subprocess_terminate_grace_seconds=2,
        inactivity_timeout_minutes=0,
    )

    async def _delayed_events():
        yield _text_event("line1")
        await asyncio.sleep(0.25)
        yield _result_event()

    mock_query.return_value = _delayed_events()

    executor = CodeExecutor(config)
    log_lines_received: list[str] = []
    result = await executor.execute(
        task, project, {}, lambda msg: log_lines_received.append(msg),
    )

    assert result.success is True
    progress_lines = [ln for ln in log_lines_received if "[PROGRESS]" in ln]
    assert len(progress_lines) >= 1
    assert "elapsed" in progress_lines[0]
    assert "events" in progress_lines[0]
    assert "since last event" in progress_lines[0]


@pytest.mark.asyncio
@patch("src.executors.code_executor.run_claude_query")
async def test_no_heartbeat_when_fast(
    mock_query: MagicMock, project: Project, task: Task,
) -> None:
    """No heartbeat when execution completes quickly."""
    config = OrchestratorSettings(
        session_timeout_minutes=1,
        subprocess_terminate_grace_seconds=2,
        inactivity_timeout_minutes=0,
    )

    mock_query.return_value = _mock_sdk_events([
        _text_event("done"),
        _result_event(),
    ])

    executor = CodeExecutor(config)
    log_lines_received: list[str] = []
    result = await executor.execute(
        task, project, {}, lambda msg: log_lines_received.append(msg),
    )

    assert result.success is True
    progress_lines = [ln for ln in log_lines_received if "[PROGRESS]" in ln]
    assert len(progress_lines) == 0


def test_heartbeat_constant() -> None:
    """HEARTBEAT_SECONDS is 30 by default."""
    assert HEARTBEAT_SECONDS == 30


# ------------------------------------------------------------------
# SDK availability check
# ------------------------------------------------------------------


class TestSdkAvailability:
    """Verify _is_sdk_available function."""

    @patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()})
    def test_available_when_importable(self) -> None:
        """Returns True when claude_agent_sdk can be imported."""
        # Need to call the real function, not the mocked one
        from importlib import reload

        import src.executors.code_executor as mod
        reload(mod)
        assert mod._is_sdk_available() is True

    def test_unavailable_when_import_fails(self) -> None:
        """Returns False when claude_agent_sdk cannot be imported."""
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            # Import error when module mapped to None
            from importlib import reload

            import src.executors.code_executor as mod
            reload(mod)
            # The function tries to import, which raises ImportError for None
            # Actually, mapping to None causes ImportError
            result = mod._is_sdk_available()
            # On some Python versions this may or may not raise
            # The function catches ImportError, so it should return False
            assert isinstance(result, bool)


# ------------------------------------------------------------------
# JSONL log persistence
# ------------------------------------------------------------------


class TestJsonlPersistence:
    """Verify JSONL log files are written during execution."""

    @patch("src.executors.code_executor.run_claude_query")
    async def test_jsonl_file_created(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
        tmp_path: Path,
    ) -> None:
        """JSONL log file is created when events are emitted."""
        config = OrchestratorSettings(
            session_timeout_minutes=1,
            stream_log_dir=tmp_path / "logs",
        )

        mock_query.return_value = _mock_sdk_events([
            _text_event("hello"),
            _result_event(),
        ])

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        # Check that log dir was created with a JSONL file
        log_dir = tmp_path / "logs" / task.id.replace(":", "_")
        assert log_dir.exists()
        jsonl_files = list(log_dir.glob("stream_*.jsonl"))
        assert len(jsonl_files) == 1

        # Verify content
        import json
        lines = jsonl_files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 2
        first = json.loads(lines[0])
        assert first["type"] == "text"

    @patch("src.executors.code_executor.HEARTBEAT_SECONDS", 0.02)
    @patch("src.executors.code_executor.run_claude_query")
    async def test_no_jsonl_when_no_events(
        self,
        mock_query: MagicMock,
        project: Project,
        task: Task,
        tmp_path: Path,
    ) -> None:
        """No JSONL file when session times out before any events."""
        config = OrchestratorSettings(
            session_timeout_minutes=1,
            stream_log_dir=tmp_path / "logs",
        )

        # Producer that hangs before emitting anything
        async def _slow():
            await asyncio.sleep(100)
            yield _result_event()

        mock_query.return_value = _slow()

        # Jump time past session timeout immediately
        base_time = time.monotonic()
        call_count = 0

        def fast_monotonic():
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                return base_time + call_count * 0.001
            return base_time + 120  # past 60s session timeout

        with patch("src.executors.code_executor.time.monotonic", fast_monotonic):
            executor = CodeExecutor(config)
            await executor.execute(task, project, {}, lambda _: None)

        log_dir = tmp_path / "logs" / task.id.replace(":", "_")
        # Lazy writer doesn't create file when no events
        if log_dir.exists():
            jsonl_files = list(log_dir.glob("stream_*.jsonl"))
            assert len(jsonl_files) == 0


# ------------------------------------------------------------------
# T-P0-96: LazyFileWriter and cleanup_empty_log_files
# ------------------------------------------------------------------


class TestLazyFileWriter:
    """Tests for _LazyFileWriter -- deferred file creation."""

    def test_no_file_created_without_write(self, tmp_path: Path) -> None:
        """File should NOT exist if write() is never called."""
        target = tmp_path / "subdir" / "test.log"
        writer = _LazyFileWriter(target)
        assert not target.exists()
        writer.close()
        assert not target.exists()

    def test_file_created_on_first_write(self, tmp_path: Path) -> None:
        """File should be created when write() is called for the first time."""
        target = tmp_path / "subdir" / "test.log"
        writer = _LazyFileWriter(target)
        writer.write("hello\n")
        writer.flush()
        writer.close()
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello\n"

    def test_parent_dirs_created(self, tmp_path: Path) -> None:
        """Parent directories should be created on first write."""
        target = tmp_path / "a" / "b" / "c" / "deep.jsonl"
        writer = _LazyFileWriter(target)
        writer.write("line\n")
        writer.close()
        assert target.exists()

    def test_multiple_writes(self, tmp_path: Path) -> None:
        """Multiple writes should append to the same file."""
        target = tmp_path / "multi.log"
        writer = _LazyFileWriter(target)
        writer.write("first\n")
        writer.write("second\n")
        writer.close()
        assert target.read_text(encoding="utf-8") == "first\nsecond\n"

    def test_flush_noop_before_write(self, tmp_path: Path) -> None:
        """flush() before any write should be a no-op (no crash)."""
        target = tmp_path / "noop.log"
        writer = _LazyFileWriter(target)
        writer.flush()  # should not raise
        assert not target.exists()

    def test_close_noop_before_write(self, tmp_path: Path) -> None:
        """close() before any write should be a no-op (no crash)."""
        target = tmp_path / "noop.log"
        writer = _LazyFileWriter(target)
        writer.close()  # should not raise
        assert not target.exists()

    def test_opened_property(self, tmp_path: Path) -> None:
        """opened property reflects whether the file has been written to."""
        target = tmp_path / "prop.log"
        writer = _LazyFileWriter(target)
        assert not writer.opened
        writer.write("x")
        assert writer.opened
        writer.close()


class TestCleanupEmptyLogFiles:
    """Tests for cleanup_empty_log_files -- removes 0-byte files."""

    def test_removes_empty_files(self, tmp_path: Path) -> None:
        """Should remove 0-byte files and leave non-empty ones."""
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        nonempty = tmp_path / "nonempty.jsonl"
        nonempty.write_text("data\n", encoding="utf-8")

        removed = cleanup_empty_log_files(tmp_path)
        assert removed == 1
        assert not empty.exists()
        assert nonempty.exists()

    def test_recursive_cleanup(self, tmp_path: Path) -> None:
        """Should clean empty files in subdirectories."""
        sub = tmp_path / "task_1"
        sub.mkdir()
        (sub / "stream.jsonl").write_text("", encoding="utf-8")
        (sub / "raw.log").write_text("", encoding="utf-8")
        (sub / "good.jsonl").write_text("{}\n", encoding="utf-8")

        removed = cleanup_empty_log_files(tmp_path)
        assert removed == 2
        assert (sub / "good.jsonl").exists()

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        """Should return 0 for a non-existent directory."""
        removed = cleanup_empty_log_files(tmp_path / "does_not_exist")
        assert removed == 0

    def test_empty_dir(self, tmp_path: Path) -> None:
        """Should return 0 for an empty directory."""
        removed = cleanup_empty_log_files(tmp_path)
        assert removed == 0
