"""CodeExecutor -- spawns ``claude`` CLI in a project's git repo.

Per PRD Section 7.2: runs ``claude -p "..." --allowedTools ... --output-format json``
via ``asyncio.create_subprocess_exec`` with timeout, streaming, and cancel support.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from collections.abc import Callable

from src.config import OrchestratorSettings
from src.executors.base import BaseExecutor, ErrorType, ExecutorResult
from src.models import Project, Task

logger = logging.getLogger(__name__)

# Maximum stderr capture size in bytes
MAX_STDERR_BYTES = 4096

# Regex to strip ANSI escape sequences
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _truncate_stderr(raw: bytes) -> str:
    """Decode, strip ANSI, and truncate stderr to MAX_STDERR_BYTES chars."""
    decoded = raw.decode("utf-8", errors="replace")
    cleaned = _strip_ansi(decoded)
    if len(cleaned) > MAX_STDERR_BYTES:
        return cleaned[:MAX_STDERR_BYTES - 14] + "...[truncated]"
    return cleaned


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

        Runs pre-flight checks before spawning the subprocess:
        - Verifies repo_path exists as a directory
        - Verifies ``claude`` CLI is available on PATH

        Args:
            task: The task to execute.
            project: The project this task belongs to.
            env: Environment variables to inject (merged with os.environ).
            on_log: Callback invoked for each line of stdout.

        Returns:
            ExecutorResult with success status, exit code, and log tail.
        """
        # -- Pre-flight checks --
        preflight = self._preflight_checks(project)
        if preflight is not None:
            on_log(f"[PRE-FLIGHT FAIL] {preflight.error_summary}")
            return preflight

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

        # -- Capture stderr --
        stderr_text: str | None = None
        if self._proc.stderr is not None:
            try:
                raw_stderr = await self._proc.stderr.read()
                if raw_stderr:
                    stderr_text = _truncate_stderr(raw_stderr)
            except Exception:
                logger.debug("Failed to read stderr for task %s", task.id)

        # -- Classify error type --
        error_type: ErrorType | None = None
        error_summary: str | None = None

        if timed_out:
            error_type = ErrorType.TIMEOUT
            error_summary = "Session timeout - process killed"
        elif returncode != 0:
            error_type = ErrorType.NON_ZERO_EXIT
            error_summary = f"Process exited with code {returncode}"
            if stderr_text:
                # Append first line of stderr for quick diagnostics
                first_line = stderr_text.split("\n", 1)[0].strip()
                if first_line:
                    error_summary = f"{error_summary}: {first_line[:200]}"

        return ExecutorResult(
            success=(not timed_out and returncode == 0),
            exit_code=returncode,
            log_lines=log_lines[-100:],
            error_summary=error_summary,
            error_type=error_type,
            stderr_output=stderr_text,
            duration_seconds=elapsed,
        )

    async def cancel(self) -> None:
        """Cancel a running execution by terminating the subprocess."""
        if self._proc is not None and self._proc.returncode is None:
            logger.info("Cancelling running subprocess (pid=%s)", self._proc.pid)
            self._proc.terminate()

    def _preflight_checks(self, project: Project) -> ExecutorResult | None:
        """Run pre-flight checks before spawning a subprocess.

        Returns an ExecutorResult with failure details if a check fails,
        or None if all checks pass.
        """
        # Check repo_path exists
        if project.repo_path is None or not os.path.isdir(project.repo_path):
            return ExecutorResult(
                success=False,
                exit_code=-1,
                error_summary=f"Repository path not found: {project.repo_path}",
                error_type=ErrorType.REPO_NOT_FOUND,
                duration_seconds=0.0,
            )

        # Check claude CLI is available
        if shutil.which("claude") is None:
            return ExecutorResult(
                success=False,
                exit_code=-1,
                error_summary="Claude CLI not found on PATH",
                error_type=ErrorType.CLI_NOT_FOUND,
                duration_seconds=0.0,
            )

        return None

    def _build_prompt(self, task: Task) -> str:
        """Build the one-shot prompt for Claude Code per PRD Section 7.2."""
        return (
            f"You are working on task {task.local_task_id}: {task.title}\n\n"
            f"{task.description}\n\n"
            f"Follow the project's TASKS.md and claude.md conventions. "
            f"Complete this task, run tests, and update TASKS.md and PROGRESS.md."
        )
