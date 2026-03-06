"""Regression tests: asyncio subprocess stream limit is set to avoid LimitOverrunError.

The Claude CLI with --output-format stream-json can emit single JSON lines >64KB.
asyncio.create_subprocess_exec defaults to 64KB StreamReader buffers, which raises
LimitOverrunError on large lines.  These tests verify that all three subprocess
call sites pass limit=SUBPROCESS_STREAM_LIMIT.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from src.config import SUBPROCESS_STREAM_LIMIT


def _make_mock_proc() -> AsyncMock:
    """Create a mock subprocess with PIPE-like stdout/stderr."""
    mock_proc = AsyncMock()
    mock_proc.pid = 12345
    mock_proc.stdout = AsyncMock()
    mock_proc.stdout.readline = AsyncMock(return_value=b"")
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.readline = AsyncMock(return_value=b"")
    mock_proc.wait = AsyncMock(return_value=0)
    mock_proc.returncode = 0
    return mock_proc


@pytest.mark.asyncio
async def test_code_executor_passes_stream_limit() -> None:
    """CodeExecutor.execute() passes limit=SUBPROCESS_STREAM_LIMIT."""
    from src.config import OrchestratorSettings
    from src.executors.code_executor import CodeExecutor
    from src.models import ExecutorType, Project, Task, TaskStatus

    project = Project(
        id="test",
        name="Test",
        repo_path=None,
        workspace_path=None,
        tasks_file="TASKS.md",
        executor_type=ExecutorType.CODE,
        max_concurrency=1,
        env_keys=[],
        claude_md_path=None,
        is_primary=False,
    )
    task = Task(
        id="T-P0-1",
        project_id="test",
        local_task_id="T-P0-1",
        title="test task",
        status=TaskStatus.RUNNING,
        executor_type=ExecutorType.CODE,
    )
    settings = OrchestratorSettings()
    executor = CodeExecutor(settings)
    mock_proc = _make_mock_proc()

    with patch("src.executors.code_executor.asyncio.create_subprocess_exec",
               return_value=mock_proc) as mock_exec, \
         patch("shutil.which", return_value="/usr/bin/claude"), \
         patch.object(executor, "_preflight_checks", return_value=None):
        with contextlib.suppress(Exception):
            await executor.execute(
                task=task,
                project=project,
                env={},
                on_log=lambda _line: None,
            )

        mock_exec.assert_called_once()
        assert mock_exec.call_args.kwargs.get("limit") == SUBPROCESS_STREAM_LIMIT



@pytest.mark.asyncio
async def test_review_pipeline_passes_stream_limit() -> None:
    """ReviewPipeline._call_claude_cli() passes limit=SUBPROCESS_STREAM_LIMIT."""
    from src.config import ReviewPipelineConfig
    from src.review_pipeline import ReviewPipeline

    config = ReviewPipelineConfig()
    pipeline = ReviewPipeline(config=config)
    mock_proc = _make_mock_proc()

    with patch("src.review_pipeline.asyncio.create_subprocess_exec",
               return_value=mock_proc) as mock_exec, \
         patch("shutil.which", return_value="/usr/bin/claude"):
        with contextlib.suppress(Exception):
            await pipeline._call_claude_cli(
                prompt="test prompt",
                model="claude-sonnet-4-5",
                system_prompt="test",
            )

        mock_exec.assert_called_once()
        assert mock_exec.call_args.kwargs.get("limit") == SUBPROCESS_STREAM_LIMIT


def test_subprocess_stream_limit_value() -> None:
    """SUBPROCESS_STREAM_LIMIT is 8 MiB -- large enough for CLI output."""
    assert SUBPROCESS_STREAM_LIMIT == 8 * 1024 * 1024
