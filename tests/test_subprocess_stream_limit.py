"""Regression tests: asyncio subprocess stream limit is set to avoid LimitOverrunError.

The Claude CLI with --output-format stream-json can emit single JSON lines >64KB.
asyncio.create_subprocess_exec defaults to 64KB StreamReader buffers, which raises
LimitOverrunError on large lines.  These tests verify that all three subprocess
call sites pass limit=SUBPROCESS_STREAM_LIMIT.
"""

from __future__ import annotations

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
async def test_code_executor_uses_sdk_not_subprocess() -> None:
    """CodeExecutor.execute() uses run_claude_query, not subprocess.

    T-P1-88 migrated CodeExecutor from asyncio.create_subprocess_exec to
    the Agent SDK.  This test verifies the migration is complete.
    """
    from src.executors.code_executor import CodeExecutor
    from src.sdk_adapter import ClaudeEvent, ClaudeEventType

    async def _mock_events(*_args, **_kwargs):  # noqa: ANN002, ANN003
        yield ClaudeEvent(type=ClaudeEventType.RESULT, result_text="done")

    with patch("src.executors.code_executor.run_claude_query",
               side_effect=_mock_events) as mock_query, \
         patch("src.executors.code_executor._is_sdk_available",
               return_value=True):
        import tempfile
        from pathlib import Path

        from src.config import OrchestratorSettings
        from src.models import ExecutorType, Project, Task, TaskStatus

        with tempfile.TemporaryDirectory() as tmpdir:
            project = Project(
                id="test", name="Test", repo_path=Path(tmpdir),
                executor_type=ExecutorType.CODE,
            )
            task = Task(
                id="T-P0-1", project_id="test", local_task_id="T-P0-1",
                title="test task", status=TaskStatus.RUNNING,
                executor_type=ExecutorType.CODE,
            )
            settings = OrchestratorSettings()
            executor = CodeExecutor(settings)
            await executor.execute(
                task=task, project=project, env={},
                on_log=lambda _line: None,
            )
            mock_query.assert_called_once()



@pytest.mark.asyncio
async def test_review_pipeline_uses_sdk_not_subprocess() -> None:
    """ReviewPipeline._call_claude_sdk() uses run_claude_query, not subprocess.

    T-P1-89 migrated ReviewPipeline from asyncio.create_subprocess_exec to
    the Agent SDK.  This test verifies the migration is complete.
    """
    from src.config import ReviewPipelineConfig
    from src.review_pipeline import ReviewPipeline
    from src.sdk_adapter import ClaudeEvent, ClaudeEventType

    async def _mock_events(*_args, **_kwargs):  # noqa: ANN002, ANN003
        yield ClaudeEvent(
            type=ClaudeEventType.RESULT,
            result_text="done",
            structured_output={"verdict": "approve", "summary": "ok", "suggestions": []},
            model="claude-sonnet-4-5",
        )

    config = ReviewPipelineConfig()
    pipeline = ReviewPipeline(config=config)

    with patch("src.review_pipeline.run_claude_query",
               side_effect=_mock_events) as mock_query:
        cli_output, events = await pipeline._call_claude_sdk(
            prompt="test prompt",
            model="claude-sonnet-4-5",
            system_prompt="test",
        )

        mock_query.assert_called_once()
        assert isinstance(cli_output, dict)
        assert isinstance(events, list)


def test_subprocess_stream_limit_value() -> None:
    """SUBPROCESS_STREAM_LIMIT is 8 MiB -- large enough for CLI output."""
    assert SUBPROCESS_STREAM_LIMIT == 8 * 1024 * 1024
