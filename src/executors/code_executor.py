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
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.config import OrchestratorSettings
from src.executors.base import BaseExecutor, ErrorType, ExecutorResult
from src.models import Project, Task
from src.prompt_loader import load_prompt, render_prompt
from src.sdk_adapter import ClaudeEventType, QueryOptions, run_claude_query

logger = logging.getLogger(__name__)

# Regex to strip ANSI escape sequences
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Interval in seconds between [PROGRESS] log emissions during execution.
PROGRESS_LOG_INTERVAL_SECONDS = 60

# Heartbeat interval: emit [PROGRESS] if no SDK event for this many seconds.
HEARTBEAT_SECONDS = 30

# Execution system prompt loaded once from config/prompts/execution_system.md.
# Provides agent role context for execution SDK calls.
_EXECUTION_SYSTEM_PROMPT = load_prompt("execution_system")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as ``M:SS`` (e.g. ``2:05``)."""
    total = int(seconds)
    mins, secs = divmod(total, 60)
    return f"{mins}:{secs:02d}"


def _format_plan_json_for_prompt(plan_json: str | None) -> str:
    """Parse plan_json and format structured implementation steps + ACs.

    Returns an empty string if plan_json is None or malformed (graceful
    fallback -- the execution prompt will use description-only).
    """
    if not plan_json:
        return ""
    try:
        data = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
    except (json.JSONDecodeError, TypeError):
        logger.debug("Malformed plan_json, skipping structured injection")
        return ""

    if not isinstance(data, dict):
        return ""

    parts: list[str] = []

    # Implementation steps
    steps = data.get("steps")
    if steps and isinstance(steps, list):
        parts.append("## Implementation Steps")
        for i, step in enumerate(steps, 1):
            if isinstance(step, dict):
                desc = step.get("step", step.get("description", ""))
                files = step.get("files", [])
                parts.append(f"{i}. {desc}")
                if files and isinstance(files, list):
                    for f in files:
                        parts.append(f"   - File: {f}")
            elif isinstance(step, str):
                parts.append(f"{i}. {step}")

    # Acceptance criteria
    criteria = data.get("acceptance_criteria")
    if criteria and isinstance(criteria, list):
        parts.append("\n## Acceptance Criteria")
        for ac in criteria:
            if isinstance(ac, str):
                parts.append(f"- [ ] {ac}")

    return "\n".join(parts)


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
        review_feedback: str | None = None,
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
            review_feedback: Optional formatted block of previous review
                suggestions to inject into the prompt.

        Returns:
            ExecutorResult with success status, exit code, and log tail.
        """
        # -- Pre-flight checks --
        preflight = self._preflight_checks(project)
        if preflight is not None:
            on_log(f"[PRE-FLIGHT FAIL] {preflight.error_summary}")
            return preflight

        prompt = self._build_prompt(task, review_feedback=review_feedback)

        # setting_sources=None (default): execution agent inherits all CLI
        # hooks (block_dangerous, secret_guard, etc.) because it runs real
        # code in the user's repo and needs full safety guardrails.
        options = QueryOptions(
            model=self._config.execution_model,
            system_prompt=_EXECUTION_SYSTEM_PROMPT,
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

    def _build_prompt(
        self,
        task: Task,
        review_feedback: str | None = None,
    ) -> str:
        """Build the one-shot prompt for Claude Code per PRD Section 7.2.

        Args:
            task: The task to build the prompt for.
            review_feedback: Optional formatted block of previous review
                suggestions to include in the prompt.
        """
        prompt = render_prompt(
            "execution",
            local_task_id=task.local_task_id or "",
            title=task.title,
            description=task.description or "",
        )

        # Inject structured plan data from plan_json when available
        plan_section = _format_plan_json_for_prompt(task.plan_json)
        if plan_section:
            prompt += "\n\n" + plan_section

        if review_feedback:
            prompt += "\n\n" + review_feedback
        return prompt
