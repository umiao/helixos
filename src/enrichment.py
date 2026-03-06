"""AI-assisted task enrichment and plan generation via Claude CLI.

Provides two capabilities:
1. **Enrichment**: Takes a task title and returns AI-generated description
   and priority suggestion.
2. **Plan generation**: Takes task title + description + optional codebase
   path and returns a structured implementation plan. Uses ``claude -p``
   with ``--add-dir`` for codebase context and ``--json-schema`` for
   structured output.

Note on ``--plan`` flag: Claude CLI does not have a ``--plan`` flag.
Instead, plan generation uses standard CLI features (``-p``,
``--system-prompt``, ``--json-schema``, ``--add-dir``) which are stable
and documented.  ``--permission-mode plan`` is NOT used because it
conflicts with ``--json-schema`` (ExitPlanMode denied in structured mode).
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from src.executors.code_executor import _simplify_stream_event, _StreamJsonBuffer

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Pydantic validation models for CLI structured output
# ------------------------------------------------------------------


class EnrichmentResult(BaseModel):
    """Validates enrichment JSON matches --json-schema contract."""

    description: str
    priority: Literal["P0", "P1", "P2"]


class PlanStep(BaseModel):
    """Single step in a plan, matching steps[].* in --json-schema."""

    step: str
    files: list[str] = []


class PlanResult(BaseModel):
    """Validates plan JSON matches --json-schema contract."""

    plan: str
    steps: list[PlanStep]
    acceptance_criteria: list[str]


# ------------------------------------------------------------------
# Plan generation error taxonomy (T-P1-74)
# ------------------------------------------------------------------


class PlanGenerationErrorType(enum.StrEnum):
    """Structured error types for plan generation failures.

    Enables smart retry decisions: transient errors (timeout) are retryable,
    while permanent errors (cli_unavailable, budget_exceeded) are not.
    """

    CLI_UNAVAILABLE = "cli_unavailable"
    TIMEOUT = "timeout"
    PARSE_FAILURE = "parse_failure"
    BUDGET_EXCEEDED = "budget_exceeded"
    CLI_ERROR = "cli_error"

    @property
    def retryable(self) -> bool:
        """Whether this error type is worth retrying."""
        return self in (
            PlanGenerationErrorType.TIMEOUT,
            PlanGenerationErrorType.PARSE_FAILURE,
            PlanGenerationErrorType.CLI_ERROR,
        )

    @property
    def user_message(self) -> str:
        """Actionable message for the frontend."""
        messages = {
            PlanGenerationErrorType.CLI_UNAVAILABLE: (
                "Claude CLI is not installed or not on PATH. "
                "Install it to enable plan generation."
            ),
            PlanGenerationErrorType.TIMEOUT: (
                "Plan generation timed out. "
                "The task may be too complex -- try again or simplify the description."
            ),
            PlanGenerationErrorType.PARSE_FAILURE: (
                "Could not parse the AI response into a valid plan. "
                "Try again -- the AI may produce a better response."
            ),
            PlanGenerationErrorType.BUDGET_EXCEEDED: (
                "API budget limit was exceeded. "
                "Increase the budget or wait before retrying."
            ),
            PlanGenerationErrorType.CLI_ERROR: (
                "Claude CLI returned an error. "
                "Check logs for details and try again."
            ),
        }
        return messages[self]


class PlanGenerationError(Exception):
    """Structured exception for plan generation failures.

    Carries an error_type for smart retry decisions and actionable
    user-facing messages.
    """

    def __init__(self, error_type: PlanGenerationErrorType, detail: str) -> None:
        self.error_type = error_type
        self.detail = detail
        super().__init__(f"[{error_type.value}] {detail}")

    @property
    def retryable(self) -> bool:
        """Whether the caller should retry this operation."""
        return self.error_type.retryable

    @property
    def user_message(self) -> str:
        """Actionable message suitable for display to users."""
        return self.error_type.user_message


# JSON schema for structured enrichment output
_ENRICHMENT_JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
    },
    "required": ["description", "priority"],
})

_ENRICHMENT_SYSTEM_PROMPT = (
    "You are a task planning assistant for a software project.\n\n"
    "Given a task title, generate:\n"
    "1. A concise but informative description (1-3 sentences) explaining "
    "what the task involves and why it matters.\n"
    "2. A priority level: P0 (must have / critical), P1 (should have / important), "
    "or P2 (nice to have / polish).\n\n"
    "Respond in JSON with this exact structure:\n"
    '{"description": "...", "priority": "P0"}'
)


def is_claude_cli_available() -> bool:
    """Check whether the ``claude`` CLI binary is on PATH.

    Reuses the same pre-flight pattern as CodeExecutor.
    """
    return shutil.which("claude") is not None


async def enrich_task_title(
    title: str,
    timeout_minutes: int = 60,
) -> dict[str, str]:
    """Call Claude CLI to generate a description and priority for a task title.

    Args:
        title: The raw task title to enrich.
        timeout_minutes: Maximum time in minutes before the subprocess is
            killed. 0 disables the timeout.

    Returns:
        Dict with ``description`` (str) and ``priority`` (str, e.g. "P0").

    Raises:
        RuntimeError: If the Claude CLI subprocess fails or times out.
    """
    args = [
        "claude", "-p", f"Task title: {title}",
        "--system-prompt", _ENRICHMENT_SYSTEM_PROMPT,
        "--model", "claude-haiku-4-5-20251001",
        "--output-format", "json",
        "--no-session-persistence",
        "--json-schema", _ENRICHMENT_JSON_SCHEMA,
    ]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise PlanGenerationError(
            PlanGenerationErrorType.TIMEOUT,
            f"Enrichment subprocess timed out after {timeout_minutes} minutes",
        ) from None

    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        error_type = _classify_cli_error(proc.returncode, stderr_text)
        raise PlanGenerationError(
            error_type,
            f"Claude CLI failed (exit {proc.returncode}): {stderr_text}",
        )

    # When --json-schema is used, CLI puts structured output in
    # "structured_output" (already a dict), NOT "result" (which is null).
    cli_output = json.loads(stdout_bytes.decode("utf-8"))
    result_data = cli_output.get("structured_output") or cli_output.get("result", "")

    return _parse_enrichment(result_data)


def _parse_enrichment(text: str | dict) -> dict[str, str]:
    """Parse the Claude CLI enrichment result into description + priority.

    Args:
        text: The ``structured_output`` (dict) or ``result`` (str) field
            from Claude CLI JSON output.

    Returns:
        Dict with ``description`` and ``priority`` keys.
    """
    try:
        data = text if isinstance(text, dict) else json.loads(text)
        result = EnrichmentResult.model_validate(data)
        description = result.description
        priority = result.priority
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
        raw_repr = str(text)
        logger.warning(
            "Failed to parse enrichment response: %s. Raw (%d chars): %.500s",
            exc, len(raw_repr), raw_repr,
        )
        description = ""
        priority = "P1"

    return {"description": description, "priority": priority}


def _classify_cli_error(returncode: int, stderr: str) -> PlanGenerationErrorType:
    """Classify a CLI subprocess error into a structured error type.

    Args:
        returncode: The process exit code.
        stderr: The stderr output from the process.

    Returns:
        The most specific error type matching the failure.
    """
    stderr_lower = stderr.lower()
    if "budget" in stderr_lower or "usage limit" in stderr_lower:
        return PlanGenerationErrorType.BUDGET_EXCEEDED
    if "not found" in stderr_lower or "no such file" in stderr_lower:
        return PlanGenerationErrorType.CLI_UNAVAILABLE
    return PlanGenerationErrorType.CLI_ERROR


# ------------------------------------------------------------------
# Plan generation via Claude CLI
# ------------------------------------------------------------------

_PLAN_JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "plan": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "string"},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["step"],
            },
        },
        "acceptance_criteria": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["plan", "steps", "acceptance_criteria"],
})

_PLAN_SYSTEM_PROMPT = (
    "You are a software architect generating structured implementation plans.\n\n"
    "Given a task title, description, and optional codebase context, generate:\n"
    "1. A concise plan summary (1-3 paragraphs) describing the approach.\n"
    "2. Ordered implementation steps, each with the files likely to be modified.\n"
    "3. Acceptance criteria that can be verified after implementation.\n\n"
    "Focus on practical, actionable steps. Reference specific files and "
    "patterns from the codebase when available. Keep the plan focused and "
    "avoid over-engineering.\n\n"
    "Respond in JSON with this exact structure:\n"
    '{"plan": "...", "steps": [{"step": "...", "files": ["..."]}], '
    '"acceptance_criteria": ["..."]}'
)


async def generate_task_plan(
    title: str,
    description: str = "",
    repo_path: Path | None = None,
    timeout_minutes: int = 60,
    on_log: Callable[[str], None] | None = None,
    heartbeat_seconds: int = 30,
    on_raw_artifact: Callable[[str], Awaitable[None]] | None = None,
    on_stream_event: Callable[[dict], None] | None = None,
    stream_log_dir: Path | None = None,
    task_id: str | None = None,
) -> dict:
    """Call Claude CLI to generate a structured implementation plan.

    Uses ``claude -p`` with ``--add-dir`` for codebase context when
    *repo_path* is provided.  Uses ``--output-format stream-json --verbose``
    for real-time streaming with JSONL persistence.

    Args:
        title: The task title.
        description: Existing task description (may be empty).
        repo_path: Optional path to the project repository for codebase
            context via ``--add-dir``.
        timeout_minutes: Maximum time in minutes before the subprocess is
            killed. 0 disables the timeout.
        on_log: Optional callback invoked per simplified log line for
            real-time streaming.  Signature: ``(line: str) -> None``.
        heartbeat_seconds: If no stdout line arrives within this many
            seconds, emit a synthetic ``[PROGRESS] heartbeat`` line via
            *on_log*.  Set to 0 to disable.
        on_raw_artifact: Optional async callback to persist raw CLI output.
        on_stream_event: Optional callback invoked for each parsed
            stream-json event dict.  Signature: ``(event: dict) -> None``.
        stream_log_dir: Optional directory for JSONL stream log persistence.
        task_id: Optional task ID used for log file naming.

    Returns:
        Dict with ``plan`` (str), ``steps`` (list of dicts), and
        ``acceptance_criteria`` (list of str).

    Raises:
        RuntimeError: If the Claude CLI subprocess fails or times out.
    """
    user_prompt = f"Task: {title}"
    if description.strip():
        user_prompt += f"\n\nExisting description:\n{description}"

    args = [
        "claude", "-p", user_prompt,
        "--system-prompt", _PLAN_SYSTEM_PROMPT,
        "--model", "claude-sonnet-4-5",
        "--output-format", "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--json-schema", _PLAN_JSON_SCHEMA,
    ]

    # Give Claude codebase context if repo_path exists
    # NOTE: --permission-mode plan is NOT used here because it conflicts
    # with --json-schema (ExitPlanMode gets denied in structured output mode).
    if repo_path is not None and repo_path.is_dir():
        args.extend(["--add-dir", str(repo_path)])

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # -- JSONL log persistence --
    jsonl_file = None
    raw_file = None
    if task_id is not None and stream_log_dir is not None:
        log_dir = stream_log_dir / task_id.replace(":", "_")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        jsonl_file = open(  # noqa: SIM115
            log_dir / f"plan_stream_{ts}.jsonl", "w", encoding="utf-8",
        )
        raw_file = open(  # noqa: SIM115
            log_dir / f"plan_raw_{ts}.log", "w", encoding="utf-8",
        )

    timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
    log_lines: list[str] = []
    last_output_time = time.monotonic()
    buffer = _StreamJsonBuffer()
    cli_output: dict = {}  # Will be populated from the result event

    def _emit(line: str) -> None:
        if on_log is not None:
            on_log(line)

    def _process_parsed_events(
        parsed_events: list[dict], non_json: list[str],
    ) -> None:
        """Write parsed events to JSONL and call callbacks."""
        nonlocal cli_output
        for event_dict in parsed_events:
            if jsonl_file is not None:
                jsonl_file.write(
                    json.dumps(event_dict, ensure_ascii=False) + "\n"
                )
                jsonl_file.flush()

            if on_stream_event is not None:
                on_stream_event(event_dict)

            # Capture the result event as the final CLI output
            if event_dict.get("type") == "result":
                cli_output = event_dict

            simplified = _simplify_stream_event(event_dict)
            if simplified:
                _emit(simplified)

        for raw_text in non_json:
            _emit(raw_text)

    try:
        async with asyncio.timeout(timeout_seconds):
            assert proc.stdout is not None
            while True:
                # Per-line read with heartbeat timeout
                try:
                    if heartbeat_seconds > 0:
                        raw_line = await asyncio.wait_for(
                            proc.stdout.readline(),
                            timeout=heartbeat_seconds,
                        )
                    else:
                        raw_line = await proc.stdout.readline()
                except TimeoutError:
                    # No output for heartbeat_seconds -- emit heartbeat
                    elapsed = int(time.monotonic() - last_output_time)
                    _emit(
                        f"[PROGRESS] heartbeat -- no output for {elapsed}s"
                    )
                    continue

                if not raw_line:  # EOF: subprocess closed stdout
                    break

                decoded = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                log_lines.append(decoded)  # always preserve for raw artifact

                if not decoded.strip():
                    continue

                # Persist raw line
                if raw_file is not None:
                    raw_file.write(decoded + "\n")
                    raw_file.flush()

                last_output_time = time.monotonic()

                # Parse stream-json events
                parsed_events = buffer.feed(decoded + "\n")
                _process_parsed_events(parsed_events, buffer.non_json)

            # -- EOF: flush partial buffer --
            if buffer._partial:
                remainder = buffer._partial.strip()
                buffer._partial = ""
                if remainder:
                    if raw_file is not None:
                        raw_file.write(remainder + "\n")
                        raw_file.flush()
                    try:
                        obj = json.loads(remainder)
                        if isinstance(obj, dict):
                            if jsonl_file is not None:
                                jsonl_file.write(
                                    json.dumps(obj, ensure_ascii=False) + "\n"
                                )
                                jsonl_file.flush()
                            if on_stream_event is not None:
                                on_stream_event(obj)
                            if obj.get("type") == "result":
                                cli_output = obj
                            simplified = _simplify_stream_event(obj)
                            if simplified:
                                _emit(simplified)
                    except (json.JSONDecodeError, ValueError):
                        pass

            await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise PlanGenerationError(
            PlanGenerationErrorType.TIMEOUT,
            f"Plan generation subprocess timed out after {timeout_minutes} minutes",
        ) from None
    finally:
        if jsonl_file is not None:
            jsonl_file.close()
        if raw_file is not None:
            raw_file.close()

    # PERSIST-FIRST: save full raw output before ANY parsing or validation.
    # Even if returncode != 0 or JSON parsing fails, the raw output is recoverable.
    full_output = "\n".join(log_lines)
    if on_raw_artifact is not None:
        try:
            await on_raw_artifact(full_output)
        except Exception:
            logger.warning(
                "Failed to persist raw artifact for plan, continuing",
                exc_info=True,
            )

    if proc.returncode != 0:
        stderr_bytes = b""
        if proc.stderr is not None:
            stderr_bytes = await proc.stderr.read()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        error_type = _classify_cli_error(proc.returncode, stderr_text)
        raise PlanGenerationError(
            error_type,
            f"Claude CLI failed (exit {proc.returncode}): {stderr_text}",
        )

    # If we captured a result event from stream-json, extract from it.
    # Otherwise fall back to parsing full output as JSON.
    if not cli_output:
        try:
            cli_output = json.loads(full_output)
        except json.JSONDecodeError:
            cli_output = {}
            for line in reversed(log_lines):
                try:
                    cli_output = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

    # structured_output is a dict when --json-schema was used; fall back to result
    result_data = cli_output.get("structured_output") or cli_output.get("result", "")
    plan_data = _parse_plan(result_data)

    # Structural validation: reject empty/incomplete plans before caller marks "ready"
    is_valid, reason = _validate_plan_structure(plan_data)
    if not is_valid:
        raise PlanGenerationError(
            PlanGenerationErrorType.PARSE_FAILURE,
            f"Plan generation produced invalid structure ({reason}). "
            f"Raw output length: {len(full_output)} chars. "
            f"Raw output preserved in execution_logs for re-parse.",
        )

    return plan_data


def _validate_plan_structure(plan_data: dict) -> tuple[bool, str]:
    """Validate that parsed plan has meaningful content.

    Returns (is_valid, reason).
    """
    if not plan_data.get("plan", "").strip():
        return False, "empty_plan_text"
    if not plan_data.get("steps"):
        return False, "empty_steps"
    if not plan_data.get("acceptance_criteria"):
        return False, "empty_acceptance_criteria"
    return True, "ok"


def _parse_plan(text: str | dict) -> dict:
    """Parse the Claude CLI plan generation result.

    Args:
        text: The ``structured_output`` (dict) or ``result`` (str) field
            from Claude CLI JSON output.

    Returns:
        Dict with ``plan``, ``steps``, and ``acceptance_criteria`` keys.
    """
    try:
        data = text if isinstance(text, dict) else json.loads(text)
        result = PlanResult.model_validate(data)
        plan = result.plan
        steps = [s.model_dump() for s in result.steps]
        acceptance_criteria = result.acceptance_criteria
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
        raw_repr = str(text)
        logger.warning(
            "Failed to parse plan response: %s. Raw (%d chars): %.500s",
            exc, len(raw_repr), raw_repr,
        )
        plan = text if isinstance(text, str) and text else ""
        steps = []
        acceptance_criteria = []

    return {
        "plan": plan,
        "steps": steps,
        "acceptance_criteria": acceptance_criteria,
    }


def format_plan_as_text(plan_data: dict) -> str:
    """Format structured plan data into readable text for task.description.

    Converts the JSON structure returned by ``generate_task_plan`` into
    a human-readable text format suitable for storing as task description
    and displaying in the ReviewPanel.

    Args:
        plan_data: Dict with ``plan``, ``steps``, ``acceptance_criteria``.

    Returns:
        Formatted plan text.
    """
    lines: list[str] = []

    plan_summary = plan_data.get("plan", "")
    if plan_summary:
        lines.append(plan_summary)
        lines.append("")

    steps = plan_data.get("steps", [])
    if steps:
        lines.append("## Implementation Steps")
        lines.append("")
        for i, step in enumerate(steps, 1):
            step_text = step.get("step", "") if isinstance(step, dict) else str(step)
            lines.append(f"{i}. {step_text}")
            files = step.get("files", []) if isinstance(step, dict) else []
            for f in files:
                lines.append(f"   - {f}")
        lines.append("")

    criteria = plan_data.get("acceptance_criteria", [])
    if criteria:
        lines.append("## Acceptance Criteria")
        lines.append("")
        for ac in criteria:
            lines.append(f"- {ac}")

    return "\n".join(lines).strip()
