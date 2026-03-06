"""CodeExecutor -- runs tasks via the Claude Agent SDK.

Per PRD Section 7.2: sends task prompt to ``run_claude_query()`` with
allowed tools, cwd, and env.  Streams typed ``ClaudeEvent`` objects for
real-time callbacks, JSONL persistence, and heartbeat emission.

Migrated from raw ``asyncio.create_subprocess_exec`` (CLI) to the Agent
SDK adapter (``src.sdk_adapter``) in T-P1-88.

Timeout / inactivity / cancellation:
- Session timeout: ``asyncio.timeout`` wrapping the event consumer loop.
- Inactivity timeout: if no SDK event arrives within
  ``inactivity_timeout_minutes``, the query is cancelled.
- Cancellation: ``cancel()`` cancels the producer task.
- Heartbeat: if no event for 30 s, emit ``[PROGRESS]`` to ``on_log``.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.config import OrchestratorSettings
from src.executors.base import BaseExecutor, ErrorType, ExecutorResult
from src.models import Project, Task
from src.sdk_adapter import ClaudeEventType, QueryOptions, run_claude_query

logger = logging.getLogger(__name__)

# Maximum stderr capture size in bytes
MAX_STDERR_BYTES = 4096

# Regex to strip ANSI escape sequences
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

_IS_WINDOWS = sys.platform == "win32"

# Interval in seconds between [PROGRESS] log emissions during execution.
PROGRESS_LOG_INTERVAL_SECONDS = 60

# Heartbeat interval: emit [PROGRESS] if no SDK event for this many seconds.
HEARTBEAT_SECONDS = 30


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


class _LazyFileWriter:
    """File writer that defers file creation until the first write.

    Prevents empty log files from accumulating when a subprocess produces
    no output (e.g., aborted or failed before any stdout).

    Args:
        path: Target file path.  Parent directories are created on first write.
    """

    def __init__(self, path: Path) -> None:
        """Initialize with target path; file is NOT opened yet."""
        self._path: Path = path
        self._file: io.TextIOWrapper | None = None

    def write(self, data: str) -> None:
        """Write data, opening the file on first call."""
        if self._file is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._path, "w", encoding="utf-8")  # noqa: SIM115
        self._file.write(data)

    def flush(self) -> None:
        """Flush underlying file (no-op if never opened)."""
        if self._file is not None:
            self._file.flush()

    def close(self) -> None:
        """Close underlying file (no-op if never opened)."""
        if self._file is not None:
            self._file.close()
            self._file = None

    @property
    def opened(self) -> bool:
        """Return True if the file has been opened (at least one write)."""
        return self._file is not None


def cleanup_empty_log_files(log_dir: Path) -> int:
    """Remove 0-byte files from *log_dir* recursively.

    Called at application startup to clean up stale empty log files left
    by aborted or failed runs.

    Args:
        log_dir: Root log directory (e.g. ``data/logs``).

    Returns:
        Number of files removed.
    """
    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return 0

    removed = 0
    for f in log_dir.rglob("*"):
        if f.is_file() and f.stat().st_size == 0:
            f.unlink()
            removed += 1
    return removed


class _StreamJsonBuffer:
    """Incremental buffer that accumulates raw text and yields parsed JSON objects.

    Handles the case where a JSON object might be split across multiple reads
    (though readline-based I/O typically delivers complete lines). Each call to
    ``feed()`` returns a list of successfully parsed JSON dicts from the input.
    Non-JSON lines are returned separately via the ``non_json`` attribute after
    each ``feed()`` call.
    """

    def __init__(self) -> None:
        """Initialize with empty buffer."""
        self._partial: str = ""
        self.non_json: list[str] = []

    def feed(self, text: str) -> list[dict]:
        """Feed a text chunk and return any complete JSON objects found.

        Args:
            text: Raw text (possibly containing multiple lines).

        Returns:
            List of parsed JSON dicts from complete lines.
        """
        self.non_json = []
        combined = self._partial + text
        self._partial = ""

        results: list[dict] = []
        lines = combined.split("\n")

        # If text doesn't end with newline, last element is partial
        if not combined.endswith("\n"):
            self._partial = lines.pop()

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
                if isinstance(obj, dict):
                    results.append(obj)
                else:
                    self.non_json.append(stripped)
            except (json.JSONDecodeError, ValueError):
                self.non_json.append(stripped)

        return results


def _simplify_stream_event(event: dict) -> str | None:
    """Derive a simplified text line from a parsed stream-json event.

    Maps stream-json event types to human-readable text for backward-compatible
    ``on_log()`` output:
    - assistant text -> emit the text content
    - content_block_delta -> emit delta text
    - stream_event -> emit nested delta text (``event.delta.text``)
    - tool_use -> ``[TOOL] name(...)``
    - tool_result -> ``[RESULT] content[:200]``
    - result -> ``[DONE]``
    - system (init) -> ``[INIT] model=X``
    - user / rate_limit_event -> None (suppressed)

    Args:
        event: A parsed JSON dict from stream-json output.

    Returns:
        Simplified text string, or None if the event has no useful text.
    """
    event_type = event.get("type", "")

    if event_type == "assistant":
        # Assistant text message
        content = event.get("content", "")
        if isinstance(content, list):
            # Content blocks - extract text blocks
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return " ".join(parts) if parts else None
        if isinstance(content, str) and content:
            return content
        # Also check for message.content pattern
        message = event.get("message", {})
        if isinstance(message, dict):
            msg_content = message.get("content", "")
            if isinstance(msg_content, str) and msg_content:
                return msg_content
        return None

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        if isinstance(delta, dict):
            text = delta.get("text", "")
            if text:
                return text
        return None

    if event_type == "stream_event":
        # --verbose stream_event wraps delta as event.delta.type == "text_delta"
        inner = event.get("event", {})
        if isinstance(inner, dict):
            delta = inner.get("delta", {})
            if isinstance(delta, dict):
                text = delta.get("text", "")
                if text:
                    return text
        return None

    if event_type == "tool_use":
        name = event.get("name", "unknown")
        tool_input = event.get("input", {})
        input_str = json.dumps(tool_input, ensure_ascii=False) if tool_input else ""
        if len(input_str) > 200:
            input_str = input_str[:200] + "..."
        return f"[TOOL] {name}({input_str})"

    if event_type == "tool_result":
        content = event.get("content", "")
        if isinstance(content, str):
            display = content[:200] + "..." if len(content) > 200 else content
        else:
            raw = json.dumps(content, ensure_ascii=False)
            display = raw[:200] + "..." if len(raw) > 200 else raw
        return f"[RESULT] {display}"

    if event_type == "result":
        return "[DONE]"

    if event_type == "system":
        # System init event contains model/tools info
        subtype = event.get("subtype", "")
        if subtype == "init":
            model = event.get("model", "unknown")
            return f"[INIT] model={model}"
        return None

    if event_type == "user":
        # User message echo -- no useful content to display
        return None

    if event_type == "rate_limit_event":
        # Rate limit info -- no actionable content for log
        return None

    return None


def _is_sdk_available() -> bool:
    """Check whether the Claude Agent SDK is importable."""
    try:
        import claude_agent_sdk  # type: ignore[import-untyped]  # noqa: F401

        return True
    except ImportError:
        return False


class CodeExecutor(BaseExecutor):
    """Executor that runs tasks via the Claude Agent SDK.

    Streams ``ClaudeEvent`` objects from ``run_claude_query()`` via a
    producer-task + queue pattern.  Handles JSONL persistence, event_bus
    emission, heartbeat, timeout, inactivity detection, and cancellation.
    """

    def __init__(self, config: OrchestratorSettings) -> None:
        """Initialize with orchestrator settings for timeout/grace config."""
        self._config = config
        self._producer_task: asyncio.Task | None = None
        self._cancelled = False

    async def execute(
        self,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
        on_stream_event: Callable[[dict], None] | None = None,
    ) -> ExecutorResult:
        """Run a Claude Agent SDK query, stream events, enforce timeout.

        Runs pre-flight checks before starting the query:
        - Verifies repo_path exists as a directory
        - Verifies Claude Agent SDK is importable

        Args:
            task: The task to execute.
            project: The project this task belongs to.
            env: Environment variables to inject into the SDK query.
            on_log: Callback invoked for each simplified log line.
            on_stream_event: Optional callback for typed event dicts.

        Returns:
            ExecutorResult with success status, exit code, and log tail.
        """
        # -- Pre-flight checks --
        preflight = self._preflight_checks(project)
        if preflight is not None:
            on_log(f"[PRE-FLIGHT FAIL] {preflight.error_summary}")
            return preflight

        prompt = self._build_prompt(task)

        options = QueryOptions(
            allowed_tools=["Bash", "Read", "Write", "Edit", "MultiTool"],
            cwd=str(project.repo_path),
            env=env,
        )

        session_timeout = self._config.session_timeout_minutes * 60
        inactivity_timeout = self._config.inactivity_timeout_minutes * 60
        start = time.monotonic()
        self._cancelled = False

        # -- JSONL log persistence (lazy: files created on first write) --
        log_dir = self._config.stream_log_dir / task.id.replace(":", "_")
        timestamp_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        jsonl_file = _LazyFileWriter(log_dir / f"stream_{timestamp_str}.jsonl")

        log_lines: list[str] = []
        timed_out = False
        inactivity_detected = False
        event_count = 0
        last_event_time = start
        has_error = False
        error_detail = ""

        event_queue: asyncio.Queue[object] = asyncio.Queue()
        _sentinel = object()

        async def _produce() -> None:
            """Producer task: iterate SDK events into the queue."""
            try:
                async for ev in run_claude_query(prompt, options):
                    await event_queue.put(ev)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.error(
                    "SDK producer error for task %s: %s", task.id, exc,
                )
            await event_queue.put(_sentinel)

        logger.info("Starting Claude SDK query for task %s", task.id)

        self._producer_task = asyncio.create_task(_produce())
        try:
            while True:
                now = time.monotonic()

                # -- Session timeout check --
                if session_timeout > 0 and (now - start) >= session_timeout:
                    timed_out = True
                    on_log(
                        f"[TIMEOUT] Session exceeded "
                        f"{self._config.session_timeout_minutes}min, "
                        f"terminating..."
                    )
                    break

                # -- Inactivity check --
                if inactivity_timeout > 0:
                    time_since_last = now - last_event_time
                    remaining_inactivity = (
                        inactivity_timeout - time_since_last
                    )
                    if remaining_inactivity <= 0:
                        inactivity_detected = True
                        mins = self._config.inactivity_timeout_minutes
                        on_log(
                            f"[INACTIVITY] No output for {mins} minutes "
                            f"-- event-based detection, terminating"
                        )
                        break

                # -- Determine queue.get timeout --
                get_timeout = float(HEARTBEAT_SECONDS)
                if session_timeout > 0:
                    remaining_session = session_timeout - (now - start)
                    get_timeout = min(get_timeout, remaining_session)
                if inactivity_timeout > 0:
                    remaining_inact = inactivity_timeout - (now - last_event_time)
                    get_timeout = min(get_timeout, remaining_inact)
                get_timeout = max(get_timeout, 0.01)  # floor

                try:
                    item = await asyncio.wait_for(
                        event_queue.get(), timeout=get_timeout,
                    )
                except TimeoutError:
                    # Re-check session/inactivity at top of loop
                    # Emit heartbeat if neither threshold was hit
                    check_now = time.monotonic()
                    session_expired = (
                        session_timeout > 0
                        and (check_now - start) >= session_timeout
                    )
                    inact_expired = (
                        inactivity_timeout > 0
                        and (check_now - last_event_time) >= inactivity_timeout
                    )
                    if not session_expired and not inact_expired:
                        elapsed_str = _format_elapsed(check_now - start)
                        since_last = int(check_now - last_event_time)
                        on_log(
                            f"[PROGRESS] {elapsed_str} elapsed | "
                            f"{event_count} events | "
                            f"{since_last}s since last event"
                        )
                    continue

                if item is _sentinel:
                    break

                if self._cancelled:
                    break

                event = item  # ClaudeEvent
                event_count += 1
                last_event_time = time.monotonic()
                event_dict = event.model_dump(exclude_none=True)

                # JSONL persistence
                jsonl_file.write(
                    json.dumps(event_dict, ensure_ascii=False) + "\n",
                )
                jsonl_file.flush()

                # Stream event callback
                if on_stream_event is not None:
                    on_stream_event(event_dict)

                # Process by event type for on_log
                if event.type == ClaudeEventType.RESULT:
                    log_lines.append("[DONE]")
                    on_log("[DONE]")
                elif event.type == ClaudeEventType.ERROR:
                    has_error = True
                    error_detail = (
                        event.error_message or "Unknown error"
                    )
                elif event.type == ClaudeEventType.TEXT:
                    if event.text:
                        log_lines.append(event.text)
                        on_log(event.text)
                elif event.type == ClaudeEventType.TOOL_USE:
                    input_str = json.dumps(
                        event.tool_input or {},
                    )[:200]
                    line = (
                        f"[TOOL] {event.tool_name}({input_str})"
                    )
                    log_lines.append(line)
                    on_log(line)
                elif event.type == ClaudeEventType.TOOL_RESULT:
                    content = (
                        event.tool_result_content or ""
                    )[:200]
                    line = f"[RESULT] {content}"
                    log_lines.append(line)
                    on_log(line)
                elif event.type == ClaudeEventType.INIT:
                    line = f"[INIT] session={event.session_id}"
                    log_lines.append(line)
                    on_log(line)

                if event_count == 1:
                    logger.info(
                        "First SDK event for task %s after %.1fs",
                        task.id,
                        time.monotonic() - start,
                    )
        finally:
            if self._producer_task is not None:
                self._producer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._producer_task
            jsonl_file.close()

        elapsed = time.monotonic() - start

        logger.info(
            "SDK query complete for task %s: %d events in %.1fs",
            task.id,
            event_count,
            elapsed,
        )

        # -- Classify error type --
        error_type: ErrorType | None = None
        error_summary: str | None = None
        returncode = 0

        if inactivity_detected:
            error_type = ErrorType.INACTIVITY_TIMEOUT
            mins = self._config.inactivity_timeout_minutes
            error_summary = (
                f"Inactivity timeout - no events for {mins} minutes"
            )
            returncode = -1
        elif timed_out:
            error_type = ErrorType.TIMEOUT
            error_summary = "Session timeout - query terminated"
            returncode = -1
        elif has_error:
            error_type = ErrorType.NON_ZERO_EXIT
            error_summary = f"Claude SDK error: {error_detail}"
            returncode = 1
        elif self._cancelled:
            error_type = ErrorType.UNKNOWN
            error_summary = "Execution cancelled"
            returncode = -1

        success = (
            not timed_out
            and not inactivity_detected
            and not has_error
            and not self._cancelled
        )

        return ExecutorResult(
            success=success,
            exit_code=returncode,
            log_lines=log_lines[-100:],
            error_summary=error_summary,
            error_type=error_type,
            duration_seconds=elapsed,
        )

    async def cancel(self) -> None:
        """Cancel a running execution by cancelling the SDK query."""
        self._cancelled = True
        if self._producer_task is not None and not self._producer_task.done():
            logger.info("Cancelling running SDK query")
            self._producer_task.cancel()

    def _preflight_checks(self, project: Project) -> ExecutorResult | None:
        """Run pre-flight checks before starting an SDK query.

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

        # Check Claude Agent SDK is available
        if not _is_sdk_available():
            return ExecutorResult(
                success=False,
                exit_code=-1,
                error_summary="Claude Agent SDK not available (install claude-agent-sdk)",
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
