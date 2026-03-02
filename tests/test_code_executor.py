"""Tests for CodeExecutor -- subprocess spawning with timeout, streaming, cancel.

Uses mock subprocess to avoid spawning real ``claude`` CLI processes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import OrchestratorSettings
from src.executors.base import BaseExecutor, ExecutorResult
from src.executors.code_executor import CodeExecutor
from src.models import ExecutorType, Project, Task


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def config() -> OrchestratorSettings:
    """Orchestrator settings with short timeouts for testing."""
    return OrchestratorSettings(
        session_timeout_minutes=1,
        subprocess_terminate_grace_seconds=2,
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


class _AsyncLineIterator:
    """Async iterator over a list of byte lines -- used as mock stdout."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self._index = 0

    def __aiter__(self) -> _AsyncLineIterator:
        return self

    async def __anext__(self) -> bytes:
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._index]
        self._index += 1
        return line


class _HangingIterator:
    """Async iterator that yields initial lines then hangs until an event is set."""

    def __init__(self, initial_lines: list[bytes], hang_event: asyncio.Event) -> None:
        self._lines = initial_lines
        self._hang_event = hang_event
        self._index = 0
        self._hanging = False

    def __aiter__(self) -> _HangingIterator:
        return self

    async def __anext__(self) -> bytes:
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return line
        if not self._hanging:
            self._hanging = True
            await self._hang_event.wait()
            raise StopAsyncIteration
        raise StopAsyncIteration


def _make_mock_proc(
    stdout_lines: list[bytes],
    returncode: int = 0,
    wait_delay: float = 0.0,
) -> MagicMock:
    """Build a mock asyncio.subprocess.Process.

    Args:
        stdout_lines: Raw byte lines to yield from stdout.
        returncode: The process exit code.
        wait_delay: Seconds to delay in wait() (for timeout testing).
    """
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None

    # Use a proper async iterable for stdout
    proc.stdout = _AsyncLineIterator(stdout_lines)

    # wait() sets returncode and optionally delays
    async def _wait() -> int:
        if wait_delay > 0:
            await asyncio.sleep(wait_delay)
        proc.returncode = returncode
        return returncode

    proc.wait = _wait

    # terminate / kill are synchronous
    proc.terminate = MagicMock()
    proc.kill = MagicMock()

    return proc


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


class TestExecuteSuccess:
    """Test successful execution flow."""

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_success_result(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Successful execution returns success=True and exit_code=0."""
        proc = _make_mock_proc(
            stdout_lines=[b"output line 1\n", b"output line 2\n"],
            returncode=0,
        )
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert result.success is True
        assert result.exit_code == 0
        assert result.error_summary is None
        assert result.duration_seconds >= 0

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_log_streaming(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Lines are streamed to on_log callback."""
        proc = _make_mock_proc(
            stdout_lines=[b"hello\n", b"world\n"],
            returncode=0,
        )
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        await executor.execute(task, project, {}, logs.append)

        assert logs == ["hello", "world"]

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_log_lines_in_result(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Result.log_lines contains the streamed lines."""
        proc = _make_mock_proc(
            stdout_lines=[b"line A\n", b"line B\n"],
            returncode=0,
        )
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        result = await executor.execute(task, project, {}, lambda _: None)

        assert result.log_lines == ["line A", "line B"]

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_empty_lines_skipped(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Empty lines from stdout are not appended to log."""
        proc = _make_mock_proc(
            stdout_lines=[b"data\n", b"\n", b"  \n", b"more\n"],
            returncode=0,
        )
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert logs == ["data", "more"]
        assert result.log_lines == ["data", "more"]


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- failure
# ------------------------------------------------------------------


class TestExecuteFailure:
    """Test failed execution flow."""

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_nonzero_exit(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Non-zero exit code returns success=False."""
        proc = _make_mock_proc(
            stdout_lines=[b"error output\n"],
            returncode=1,
        )
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        result = await executor.execute(task, project, {}, lambda _: None)

        assert result.success is False
        assert result.exit_code == 1
        assert result.error_summary is None  # not a timeout

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_nonzero_exit_various_codes(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Various non-zero exit codes are correctly reported."""
        for code in [2, 127, 255]:
            proc = _make_mock_proc(stdout_lines=[], returncode=code)
            mock_exec.return_value = proc

            executor = CodeExecutor(config)
            result = await executor.execute(task, project, {}, lambda _: None)

            assert result.success is False
            assert result.exit_code == code


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- timeout
# ------------------------------------------------------------------


class TestExecuteTimeout:
    """Test timeout handling (terminate -> grace -> kill)."""

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_timeout_terminates(
        self,
        mock_exec: AsyncMock,
        project: Project,
        task: Task,
    ) -> None:
        """When execution exceeds timeout, process is terminated."""
        # Very short timeout for testing
        config = OrchestratorSettings(
            session_timeout_minutes=0,  # 0 minutes = immediate timeout
            subprocess_terminate_grace_seconds=5,
        )

        proc = MagicMock()
        proc.pid = 99
        proc.returncode = None

        # stdout that hangs after first line
        hang_event = asyncio.Event()
        proc.stdout = _HangingIterator([b"start\n"], hang_event)

        # terminate sets returncode immediately
        def _terminate() -> None:
            proc.returncode = -15
            hang_event.set()

        proc.terminate = MagicMock(side_effect=_terminate)
        proc.kill = MagicMock()

        async def _wait() -> int:
            return proc.returncode

        proc.wait = _wait

        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert result.success is False
        assert result.error_summary == "Session timeout - process killed"
        proc.terminate.assert_called_once()
        # Grace wait succeeded, so kill should not be called
        proc.kill.assert_not_called()

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_timeout_kill_after_grace(
        self,
        mock_exec: AsyncMock,
        project: Project,
        task: Task,
    ) -> None:
        """When process doesn't exit after grace period, it gets killed."""
        config = OrchestratorSettings(
            session_timeout_minutes=0,
            subprocess_terminate_grace_seconds=0,  # immediate grace timeout
        )

        proc = MagicMock()
        proc.pid = 100
        proc.returncode = None

        # stdout that hangs after first line
        hang_event = asyncio.Event()
        proc.stdout = _HangingIterator([b"data\n"], hang_event)

        wait_calls = 0

        async def _wait() -> int:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls <= 1:
                # First wait (grace) -- hang to trigger grace timeout
                await asyncio.sleep(100)
            proc.returncode = -9
            return -9

        proc.wait = _wait

        def _terminate_no_exit() -> None:
            hang_event.set()  # unblock stdout but don't set returncode

        proc.terminate = MagicMock(side_effect=_terminate_no_exit)

        def _kill() -> None:
            proc.returncode = -9

        proc.kill = MagicMock(side_effect=_kill)

        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert result.success is False
        assert result.exit_code == -9
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_timeout_log_messages(
        self,
        mock_exec: AsyncMock,
        project: Project,
        task: Task,
    ) -> None:
        """Timeout logs contain [TIMEOUT] markers."""
        config = OrchestratorSettings(
            session_timeout_minutes=0,
            subprocess_terminate_grace_seconds=5,
        )

        proc = MagicMock()
        proc.pid = 101
        proc.returncode = None

        hang_event = asyncio.Event()
        proc.stdout = _HangingIterator([b"line\n"], hang_event)

        def _terminate() -> None:
            proc.returncode = -15
            hang_event.set()

        proc.terminate = MagicMock(side_effect=_terminate)
        proc.kill = MagicMock()

        async def _wait() -> int:
            return proc.returncode

        proc.wait = _wait

        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        await executor.execute(task, project, {}, logs.append)

        timeout_logs = [l for l in logs if "[TIMEOUT]" in l]
        assert len(timeout_logs) >= 1
        assert "terminating" in timeout_logs[0].lower()


# ------------------------------------------------------------------
# Tests: CodeExecutor.cancel
# ------------------------------------------------------------------


class TestCancel:
    """Test cancel() terminates a running subprocess."""

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_cancel_running(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """cancel() terminates a running subprocess."""
        cancel_event = asyncio.Event()

        proc = MagicMock()
        proc.pid = 200
        proc.returncode = None

        proc.stdout = _HangingIterator([b"running...\n"], cancel_event)

        def _terminate() -> None:
            proc.returncode = -15
            cancel_event.set()

        proc.terminate = MagicMock(side_effect=_terminate)

        async def _wait() -> int:
            return proc.returncode

        proc.wait = _wait

        mock_exec.return_value = proc

        executor = CodeExecutor(config)

        # Start execution in background
        exec_task = asyncio.create_task(
            executor.execute(task, project, {}, lambda _: None)
        )

        # Wait for process to start
        await asyncio.sleep(0.05)

        # Cancel
        await executor.cancel()

        proc.terminate.assert_called_once()

        # Let the execution complete
        result = await exec_task
        assert result.success is False

    async def test_cancel_no_proc(self, config: OrchestratorSettings) -> None:
        """cancel() with no process does nothing."""
        executor = CodeExecutor(config)
        # Should not raise
        await executor.cancel()

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_cancel_already_finished(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """cancel() after execution has finished does nothing."""
        proc = _make_mock_proc(stdout_lines=[b"done\n"], returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        # Process is finished (returncode is set)
        await executor.cancel()
        proc.terminate.assert_not_called()


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- log tail limit
# ------------------------------------------------------------------


class TestLogTailLimit:
    """Verify that only the last 100 log lines are kept in the result."""

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_keeps_last_100(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """When >100 lines, only the last 100 are in result.log_lines."""
        lines = [f"line {i}\n".encode("utf-8") for i in range(150)]
        proc = _make_mock_proc(stdout_lines=lines, returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        all_logs: list[str] = []
        result = await executor.execute(task, project, {}, all_logs.append)

        # Callback receives all 150 lines
        assert len(all_logs) == 150
        # Result keeps only last 100
        assert len(result.log_lines) == 100
        assert result.log_lines[0] == "line 50"
        assert result.log_lines[-1] == "line 149"


# ------------------------------------------------------------------
# Tests: CodeExecutor.execute -- subprocess arguments
# ------------------------------------------------------------------


class TestSubprocessArgs:
    """Verify the subprocess is spawned with correct arguments."""

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_command_args(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """claude CLI is invoked with correct flags."""
        proc = _make_mock_proc(stdout_lines=[], returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        call_args = mock_exec.call_args
        cmd = call_args[0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--allowedTools" in cmd
        assert "--output-format" in cmd
        assert "json" in cmd

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_cwd_is_repo_path(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Subprocess cwd is set to project.repo_path."""
        proc = _make_mock_proc(stdout_lines=[], returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["cwd"] == str(project.repo_path)

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_env_injection(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Project env vars are merged into subprocess environment."""
        proc = _make_mock_proc(stdout_lines=[], returncode=0)
        mock_exec.return_value = proc

        injected = {"MY_KEY": "my_value", "ANOTHER": "val2"}
        executor = CodeExecutor(config)
        await executor.execute(task, project, injected, lambda _: None)

        call_kwargs = mock_exec.call_args[1]
        env = call_kwargs["env"]
        assert env["MY_KEY"] == "my_value"
        assert env["ANOTHER"] == "val2"
        # os.environ vars should also be present
        assert "PATH" in env

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_env_override(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Injected env vars override os.environ values."""
        proc = _make_mock_proc(stdout_lines=[], returncode=0)
        mock_exec.return_value = proc

        # Override PATH to verify injection takes precedence
        executor = CodeExecutor(config)
        await executor.execute(task, project, {"PATH": "/custom"}, lambda _: None)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["env"]["PATH"] == "/custom"

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_stdout_pipe(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """Subprocess is spawned with PIPE for stdout and stderr."""
        proc = _make_mock_proc(stdout_lines=[], returncode=0)
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        await executor.execute(task, project, {}, lambda _: None)

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["stdout"] == asyncio.subprocess.PIPE
        assert call_kwargs["stderr"] == asyncio.subprocess.PIPE


# ------------------------------------------------------------------
# Tests: UTF-8 decoding
# ------------------------------------------------------------------


class TestUtf8Decoding:
    """Verify all string decoding uses UTF-8."""

    @patch("src.executors.code_executor.asyncio.create_subprocess_exec")
    async def test_utf8_output(
        self,
        mock_exec: AsyncMock,
        config: OrchestratorSettings,
        project: Project,
        task: Task,
    ) -> None:
        """UTF-8 encoded output is correctly decoded."""
        # Chinese characters encoded as UTF-8
        proc = _make_mock_proc(
            stdout_lines=["Hello UTF-8 \u4e16\u754c\n".encode("utf-8")],
            returncode=0,
        )
        mock_exec.return_value = proc

        executor = CodeExecutor(config)
        logs: list[str] = []
        result = await executor.execute(task, project, {}, logs.append)

        assert logs == ["Hello UTF-8 \u4e16\u754c"]
        assert result.log_lines == ["Hello UTF-8 \u4e16\u754c"]
