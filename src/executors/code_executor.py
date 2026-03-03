"""CodeExecutor -- spawns ``claude`` CLI in a project's git repo.

Per PRD Section 7.2: runs ``claude -p "..." --allowedTools ... --output-format json``
via ``asyncio.create_subprocess_exec`` with timeout, streaming, and cancel support.

Process group handling:
- Unix: ``start_new_session=True`` creates a new process group; on kill we
  send SIGTERM/SIGKILL to the entire group via ``os.killpg``.
- Windows: ``CREATE_NEW_PROCESS_GROUP`` creation flag; on kill we send
  ``CTRL_BREAK_EVENT`` to the group.

Inactivity detection:
- Each stdout line resets a per-line inactivity timer.  If no line arrives
  within ``inactivity_timeout_minutes`` (default 20, 0 = disabled), the
  process group is terminated with ``ErrorType.INACTIVITY_TIMEOUT``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import sys
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

_IS_WINDOWS = sys.platform == "win32"

# Interval in seconds between [PROGRESS] log emissions during execution.
PROGRESS_LOG_INTERVAL_SECONDS = 60


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``M:SS`` (e.g. ``2:05``)."""
    total = int(seconds)
    mins, secs = divmod(total, 60)
    return f"{mins}:{secs:02d}"


def _truncate_stderr(raw: bytes) -> str:
    """Decode, strip ANSI, and truncate stderr to MAX_STDERR_BYTES chars."""
    decoded = raw.decode("utf-8", errors="replace")
    cleaned = _strip_ansi(decoded)
    if len(cleaned) > MAX_STDERR_BYTES:
        return cleaned[:MAX_STDERR_BYTES - 14] + "...[truncated]"
    return cleaned


def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    """Send SIGTERM to the entire process group (or CTRL_BREAK on Windows).

    Suppresses ProcessLookupError in case the process already exited.
    """
    pid = proc.pid
    if pid is None:
        return

    if _IS_WINDOWS:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(pid), signal.SIGTERM)


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Send SIGKILL to the entire process group (force-kill).

    On Windows, falls back to ``proc.kill()`` since there is no SIGKILL.
    """
    pid = proc.pid
    if pid is None:
        return

    if _IS_WINDOWS:
        with contextlib.suppress(OSError):
            proc.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)


class CodeExecutor(BaseExecutor):
    """Executor that spawns ``claude`` CLI in a project's git repo.

    Streams stdout line-by-line via *on_log*, enforces a session timeout
    and per-line inactivity timeout, and supports cancellation via
    ``cancel()``.  Subprocess is created in its own process group so that
    child processes are also cleaned up on kill.
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

        session_timeout = self._config.session_timeout_minutes * 60
        inactivity_timeout = self._config.inactivity_timeout_minutes * 60
        grace = self._config.subprocess_terminate_grace_seconds
        start = time.monotonic()

        merged_env = {**os.environ, **env}

        # Platform-specific process group flags
        kwargs: dict[str, object] = {
            "cwd": str(project.repo_path),
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": merged_env,
        }
        if _IS_WINDOWS:
            import subprocess as _subprocess
            kwargs["creationflags"] = (
                _subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        else:
            kwargs["start_new_session"] = True

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            **kwargs,  # type: ignore[arg-type]
        )

        log_lines: list[str] = []
        timed_out = False
        inactivity_detected = False
        line_count = 0
        last_output_time = start

        # Background task that emits [PROGRESS] log entries periodically
        async def _progress_reporter() -> None:
            nonlocal line_count, last_output_time
            try:
                while True:
                    await asyncio.sleep(PROGRESS_LOG_INTERVAL_SECONDS)
                    now = time.monotonic()
                    elapsed_str = _format_elapsed(now - start)
                    since_last = int(now - last_output_time)
                    on_log(
                        f"[PROGRESS] {elapsed_str} elapsed | "
                        f"{line_count} lines | "
                        f"{since_last}s since last output"
                    )
            except asyncio.CancelledError:
                pass

        progress_task = asyncio.create_task(_progress_reporter())

        try:
            async with asyncio.timeout(session_timeout):
                assert self._proc.stdout is not None  # noqa: S101
                while True:
                    try:
                        if inactivity_timeout > 0:
                            raw_line = await asyncio.wait_for(
                                self._proc.stdout.readline(),
                                timeout=inactivity_timeout,
                            )
                        else:
                            raw_line = await self._proc.stdout.readline()
                    except TimeoutError:
                        # Per-line inactivity timeout
                        inactivity_detected = True
                        mins = self._config.inactivity_timeout_minutes
                        on_log(
                            f"[INACTIVITY] No output for {mins} minutes "
                            f"-- stdout-based detection, terminating process group"
                        )
                        break

                    if not raw_line:
                        # EOF -- process closed stdout
                        break

                    decoded = raw_line.decode("utf-8").strip()
                    if decoded:
                        log_lines.append(decoded)
                        on_log(decoded)
                        line_count += 1
                        last_output_time = time.monotonic()

                if not inactivity_detected:
                    await self._proc.wait()
        except TimeoutError:
            timed_out = True
            on_log(
                f"[TIMEOUT] Session exceeded "
                f"{self._config.session_timeout_minutes}min, terminating..."
            )
        finally:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task

        # -- Terminate process group if needed --
        if timed_out or inactivity_detected:
            _terminate_process_group(self._proc)
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=grace)
            except TimeoutError:
                tag = "[INACTIVITY]" if inactivity_detected else "[TIMEOUT]"
                on_log(
                    f"{tag} Process did not exit after {grace}s, killing..."
                )
                _kill_process_group(self._proc)
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

        if inactivity_detected:
            error_type = ErrorType.INACTIVITY_TIMEOUT
            mins = self._config.inactivity_timeout_minutes
            error_summary = (
                f"Inactivity timeout - no stdout for {mins} minutes, "
                f"process group killed"
            )
        elif timed_out:
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
            success=(not timed_out and not inactivity_detected and returncode == 0),
            exit_code=returncode,
            log_lines=log_lines[-100:],
            error_summary=error_summary,
            error_type=error_type,
            stderr_output=stderr_text,
            duration_seconds=elapsed,
        )

    async def cancel(self) -> None:
        """Cancel a running execution by terminating the process group."""
        if self._proc is not None and self._proc.returncode is None:
            logger.info("Cancelling running subprocess (pid=%s)", self._proc.pid)
            _terminate_process_group(self._proc)

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
