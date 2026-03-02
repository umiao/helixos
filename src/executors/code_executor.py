"""CodeExecutor -- spawns ``claude`` CLI in a project's git repo.

Per PRD Section 7.2: runs ``claude -p "..." --allowedTools ... --output-format json``
via ``asyncio.create_subprocess_exec`` with timeout, streaming, and cancel support.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable

from src.config import OrchestratorSettings
from src.executors.base import BaseExecutor, ExecutorResult
from src.models import Project, Task

logger = logging.getLogger(__name__)


class CodeExecutor(BaseExecutor):
    """Executor that spawns ``claude`` CLI in a project's git repo.

    Streams stdout line-by-line via *on_log*, enforces a session timeout,
    and supports cancellation via ``cancel()``.
    """

    def __init__(self, config: OrchestratorSettings) -> None:
        """Initialize with orchestrator settings for timeout/grace config."""
        self._config = config
        self._proc: asyncio.subprocess.Process | None = None

    async def execute(
        self,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
    ) -> ExecutorResult:
        """Spawn ``claude`` CLI, stream stdout, enforce timeout.

        Args:
            task: The task to execute.
            project: The project this task belongs to.
            env: Environment variables to inject (merged with os.environ).
            on_log: Callback invoked for each line of stdout.

        Returns:
            ExecutorResult with success status, exit code, and log tail.
        """
        prompt = self._build_prompt(task)
        cmd = [
            "claude",
            "-p",
            prompt,
            "--allowedTools",
            "Bash,Read,Write,Edit,MultiTool",
            "--output-format",
            "json",
        ]

        timeout = self._config.session_timeout_minutes * 60
        grace = self._config.subprocess_terminate_grace_seconds
        start = time.monotonic()

        merged_env = {**os.environ, **env}

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project.repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )

        log_lines: list[str] = []
        timed_out = False

        try:
            async with asyncio.timeout(timeout):
                assert self._proc.stdout is not None  # noqa: S101
                async for raw_line in self._proc.stdout:
                    decoded = raw_line.decode("utf-8").strip()
                    if decoded:
                        log_lines.append(decoded)
                        on_log(decoded)
                await self._proc.wait()
        except TimeoutError:
            timed_out = True
            on_log(
                f"[TIMEOUT] Session exceeded "
                f"{self._config.session_timeout_minutes}min, terminating..."
            )
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=grace)
            except TimeoutError:
                on_log(
                    f"[TIMEOUT] Process did not exit after {grace}s, killing..."
                )
                self._proc.kill()
                await self._proc.wait()

        elapsed = time.monotonic() - start

        returncode = self._proc.returncode if self._proc.returncode is not None else -9

        return ExecutorResult(
            success=(not timed_out and returncode == 0),
            exit_code=returncode,
            log_lines=log_lines[-100:],
            error_summary="Session timeout - process killed" if timed_out else None,
            duration_seconds=elapsed,
        )

    async def cancel(self) -> None:
        """Cancel a running execution by terminating the subprocess."""
        if self._proc is not None and self._proc.returncode is None:
            logger.info("Cancelling running subprocess (pid=%s)", self._proc.pid)
            self._proc.terminate()

    def _build_prompt(self, task: Task) -> str:
        """Build the one-shot prompt for Claude Code per PRD Section 7.2."""
        return (
            f"You are working on task {task.local_task_id}: {task.title}\n\n"
            f"{task.description}\n\n"
            f"Follow the project's TASKS.md and claude.md conventions. "
            f"Complete this task, run tests, and update TASKS.md and PROGRESS.md."
        )
