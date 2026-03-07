"""AI-assisted task enrichment and plan generation via Claude Agent SDK.

Provides two capabilities:
1. **Enrichment**: Takes a task title and returns AI-generated description
   and priority suggestion.
2. **Plan generation**: Takes task title + description + optional codebase
   path and returns a structured implementation plan. Uses the Agent SDK
   ``run_claude_query()`` with ``QueryOptions`` for model, system prompt,
   JSON schema, and codebase context (``add_dirs``).

Migrated from raw ``asyncio.create_subprocess_exec`` (CLI) to the Agent
SDK adapter (``src.sdk_adapter``) in T-P1-87.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from src.executors.code_executor import _LazyFileWriter
from src.sdk_adapter import ClaudeEventType, QueryOptions, run_claude_query
from src.session_context_loader import get_session_context

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


class ProposedTask(BaseModel):
    """A task proposal from the plan agent (not yet assigned an ID)."""

    title: str
    description: str
    suggested_priority: Literal["P0", "P1", "P2", "P3"] = "P1"
    suggested_complexity: Literal["S", "M", "L"] = "M"
    dependencies: list[str] = []
    acceptance_criteria: list[str] = []


class PlanResult(BaseModel):
    """Validates plan JSON matches --json-schema contract."""

    plan: str
    steps: list[PlanStep]
    acceptance_criteria: list[str]
    proposed_tasks: list[ProposedTask] = []


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
    """Check whether the Claude Agent SDK is importable.

    Legacy name retained for API compatibility; checks SDK availability
    rather than ``claude`` CLI binary on PATH.
    """
    try:
        import claude_agent_sdk  # type: ignore[import-untyped]  # noqa: F401

        return True
    except ImportError:
        return False


async def enrich_task_title(
    title: str,
    timeout_minutes: int = 60,
) -> dict[str, str]:
    """Call Claude Agent SDK to generate a description and priority for a task title.

    Args:
        title: The raw task title to enrich.
        timeout_minutes: Maximum time in minutes before the operation is
            cancelled. 0 disables the timeout.

    Returns:
        Dict with ``description`` (str) and ``priority`` (str, e.g. "P0").

    Raises:
        PlanGenerationError: If the SDK call fails or times out.
    """
    options = QueryOptions(
        model="claude-haiku-4-5-20251001",
        system_prompt=_ENRICHMENT_SYSTEM_PROMPT,
        json_schema=_ENRICHMENT_JSON_SCHEMA,
    )

    timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
    result_data: Any = ""

    try:
        async with asyncio.timeout(timeout_seconds):
            async for event in run_claude_query(
                f"Task title: {title}", options,
            ):
                if event.type == ClaudeEventType.RESULT:
                    result_data = (
                        event.structured_output or event.result_text or ""
                    )
                elif event.type == ClaudeEventType.ERROR:
                    error_msg = event.error_message or "Unknown error"
                    error_type = _classify_cli_error(0, error_msg)
                    raise PlanGenerationError(
                        error_type,
                        f"Claude SDK error: {error_msg}",
                    )
    except TimeoutError:
        raise PlanGenerationError(
            PlanGenerationErrorType.TIMEOUT,
            f"Enrichment timed out after {timeout_minutes} minutes",
        ) from None

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

MAX_TASKS_PER_PLAN = 8

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
        "proposed_tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "suggested_priority": {
                        "type": "string",
                        "enum": ["P0", "P1", "P2", "P3"],
                    },
                    "suggested_complexity": {
                        "type": "string",
                        "enum": ["S", "M", "L"],
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["title", "description"],
            },
            "maxItems": 8,
        },
    },
    "required": ["plan", "steps", "acceptance_criteria"],
})

_TASK_SCHEMA_CONTEXT = """\
## Task Schema (from TASKS.md conventions)

Task IDs follow the format T-P{priority}-{number} (e.g., T-P0-1, T-P1-42).
Do NOT assign IDs in your proposals -- IDs are allocated downstream.

Each task spec must include:
- **Priority**: P0 (must have) | P1 (should have) | P2 (nice to have) | P3 (stretch)
- **Complexity**: S (< 1 session) | M (1-2 sessions) | L (3+ sessions)
- **Depends on**: other task titles from your proposals, or existing task IDs, or None
- **Description**: What and why (2-4 sentences)
- **Acceptance Criteria**: Specific, verifiable outcomes
  - At least one full user journey AC per task
  - Every conditional AC must specify the inverse case
"""

_PROJECT_RULES_CONTEXT = """\
## Project Rules (from CLAUDE.md)

### Task Planning Rules
1. Scenario matrix: list ALL condition branches with expected outcomes.
2. Journey-first ACs: at least one AC per task must be a full user journey.
3. Cross-boundary integration: when spanning backend + frontend, at least one
   AC must verify end-to-end wiring.
4. "Other case" gate: every conditional AC must specify what happens when false.
5. Manual smoke test AC: every UX task needs a manual verification AC.
6. New-field consumer audit: when adding a model field, list all components
   that render related data and verify each uses the correct source of truth.

### Key Constraints
- All API keys and cookies from .env, never hardcoded.
- Every function must have type hints and docstring.
- No emoji characters anywhere in the project.
- Explicit UTF-8 for all file I/O and subprocess calls.
- Windows-compatible: no bash-only commands without PowerShell alternatives.
- Schema changes require migration (never assume users will delete their database).
"""

_PLAN_SYSTEM_PROMPT = (
    "You are a software architect generating structured implementation plans.\n\n"
    "Given a task title, description, and optional codebase context, generate:\n"
    "1. A concise plan summary (1-3 paragraphs) describing the approach.\n"
    "2. Ordered implementation steps, each with the files likely to be modified.\n"
    "3. Acceptance criteria that can be verified after implementation.\n"
    "4. Optionally, a list of proposed sub-tasks (max 8) to decompose the work.\n"
    "   Each proposed task is a PROPOSAL, not a final entry. Do NOT assign task IDs.\n\n"
    + _TASK_SCHEMA_CONTEXT
    + "\n"
    + _PROJECT_RULES_CONTEXT
    + "\n"
    "Focus on practical, actionable steps. Reference specific files and "
    "patterns from the codebase when available. Keep the plan focused and "
    "avoid over-engineering.\n\n"
    "Respond in JSON with this structure:\n"
    '{"plan": "...", "steps": [{"step": "...", "files": ["..."]}], '
    '"acceptance_criteria": ["..."], '
    '"proposed_tasks": [{"title": "...", "description": "...", '
    '"suggested_priority": "P1", "suggested_complexity": "M", '
    '"dependencies": ["other task title"], '
    '"acceptance_criteria": ["..."]}]}'
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
    """Call Claude Agent SDK to generate a structured implementation plan.

    Uses ``run_claude_query()`` with ``add_dirs`` for codebase context when
    *repo_path* is provided.  Streams typed ``ClaudeEvent`` objects for
    real-time callbacks and JSONL persistence.

    Args:
        title: The task title.
        description: Existing task description (may be empty).
        repo_path: Optional path to the project repository for codebase
            context via ``add_dirs``.
        timeout_minutes: Maximum time in minutes before the operation is
            cancelled. 0 disables the timeout.
        on_log: Optional callback invoked per simplified log line for
            real-time streaming.  Signature: ``(line: str) -> None``.
        heartbeat_seconds: If no SDK event arrives within this many
            seconds, emit a synthetic ``[PROGRESS] heartbeat`` line via
            *on_log*.  Set to 0 to disable.
        on_raw_artifact: Optional async callback to persist raw event output.
        on_stream_event: Optional callback invoked for each event dict.
            Signature: ``(event: dict) -> None``.
        stream_log_dir: Optional directory for JSONL stream log persistence.
        task_id: Optional task ID used for log file naming.

    Returns:
        Dict with ``plan`` (str), ``steps`` (list of dicts), and
        ``acceptance_criteria`` (list of str).

    Raises:
        PlanGenerationError: If the SDK call fails or times out.
    """
    user_prompt = f"Task: {title}"
    if description.strip():
        user_prompt += f"\n\nExisting description:\n{description}"

    # Inject session context into system prompt (replaces SessionStart hook
    # which is not available as an SDK hook type).
    session_ctx = get_session_context(repo_path)
    plan_prompt_with_ctx = _PLAN_SYSTEM_PROMPT + "\n\n" + session_ctx

    options = QueryOptions(
        model="claude-opus-4-6",
        system_prompt=plan_prompt_with_ctx,
        json_schema=_PLAN_JSON_SCHEMA,
        permission_mode="plan",
        # Disable CLI hooks (block_dangerous, secret_guard, etc.) for plan
        # sessions.  Session context is injected above instead.
        setting_sources=[],
    )

    if repo_path is not None and repo_path.is_dir():
        options.add_dirs = [str(repo_path)]

    # -- JSONL log persistence (lazy: files created on first write) --
    jsonl_file: _LazyFileWriter | None = None
    raw_file: _LazyFileWriter | None = None
    if task_id is not None and stream_log_dir is not None:
        log_dir = stream_log_dir / task_id.replace(":", "_")
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        jsonl_file = _LazyFileWriter(log_dir / f"plan_stream_{ts}.jsonl")
        raw_file = _LazyFileWriter(log_dir / f"plan_raw_{ts}.log")

    timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
    all_event_dicts: list[dict] = []
    result_data: Any = ""
    last_output_time = time.monotonic()
    has_error = False
    error_detail = ""

    def _emit(line: str) -> None:
        if on_log is not None:
            on_log(line)

    try:
        async with asyncio.timeout(timeout_seconds):
            # Use a producer task + queue so heartbeat timeout does not
            # cancel the underlying async generator.
            event_queue: asyncio.Queue[object] = asyncio.Queue()
            _sentinel = object()

            async def _produce() -> None:
                async for ev in run_claude_query(user_prompt, options):
                    await event_queue.put(ev)
                await event_queue.put(_sentinel)

            producer = asyncio.create_task(_produce())
            try:
                while True:
                    try:
                        hb_timeout = (
                            heartbeat_seconds if heartbeat_seconds > 0 else None
                        )
                        item = await asyncio.wait_for(
                            event_queue.get(), timeout=hb_timeout,
                        )
                    except TimeoutError:
                        elapsed = int(time.monotonic() - last_output_time)
                        _emit(
                            f"[PROGRESS] heartbeat -- no output for {elapsed}s"
                        )
                        continue

                    if item is _sentinel:
                        break

                    event = item  # ClaudeEvent
                    last_output_time = time.monotonic()
                    event_dict = event.model_dump(exclude_none=True)
                    all_event_dicts.append(event_dict)

                    # JSONL persistence
                    if jsonl_file is not None:
                        jsonl_file.write(
                            json.dumps(event_dict, ensure_ascii=False) + "\n"
                        )
                        jsonl_file.flush()
                    if raw_file is not None:
                        raw_file.write(
                            json.dumps(event_dict, ensure_ascii=False) + "\n"
                        )
                        raw_file.flush()

                    # Stream event callback
                    if on_stream_event is not None:
                        on_stream_event(event_dict)

                    # Process by event type
                    if event.type == ClaudeEventType.RESULT:
                        result_data = (
                            event.structured_output or event.result_text or ""
                        )
                        _emit("[DONE]")
                    elif event.type == ClaudeEventType.ERROR:
                        has_error = True
                        error_detail = event.error_message or "Unknown error"
                    elif event.type == ClaudeEventType.TEXT:
                        if event.text:
                            _emit(event.text)
                    elif event.type == ClaudeEventType.TOOL_USE:
                        input_str = json.dumps(event.tool_input or {})[:200]
                        _emit(f"[TOOL] {event.tool_name}({input_str})")
                    elif event.type == ClaudeEventType.TOOL_RESULT:
                        content = (event.tool_result_content or "")[:200]
                        _emit(f"[RESULT] {content}")
                    elif event.type == ClaudeEventType.INIT:
                        _emit(f"[INIT] session={event.session_id}")
            finally:
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer
    except TimeoutError:
        raise PlanGenerationError(
            PlanGenerationErrorType.TIMEOUT,
            f"Plan generation timed out after {timeout_minutes} minutes",
        ) from None
    finally:
        if jsonl_file is not None:
            jsonl_file.close()
        if raw_file is not None:
            raw_file.close()

    # PERSIST-FIRST: save full raw output before ANY parsing or validation.
    full_output = "\n".join(
        json.dumps(e, ensure_ascii=False) for e in all_event_dicts
    )
    if on_raw_artifact is not None:
        try:
            await on_raw_artifact(full_output)
        except Exception:
            logger.warning(
                "Failed to persist raw artifact for plan, continuing",
                exc_info=True,
            )

    if has_error:
        error_type = _classify_cli_error(0, error_detail)
        raise PlanGenerationError(
            error_type,
            f"Claude SDK error: {error_detail}",
        )

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
    proposed = plan_data.get("proposed_tasks", [])
    if len(proposed) > MAX_TASKS_PER_PLAN:
        return False, f"too_many_proposed_tasks ({len(proposed)} > {MAX_TASKS_PER_PLAN})"
    return True, "ok"


def _parse_plan(text: str | dict) -> dict:
    """Parse the Claude CLI plan generation result.

    Args:
        text: The ``structured_output`` (dict) or ``result`` (str) field
            from Claude CLI JSON output.

    Returns:
        Dict with ``plan``, ``steps``, ``acceptance_criteria``, and
        ``proposed_tasks`` keys.
    """
    try:
        data = text if isinstance(text, dict) else json.loads(text)
        result = PlanResult.model_validate(data)
        plan = result.plan
        steps = [s.model_dump() for s in result.steps]
        acceptance_criteria = result.acceptance_criteria
        proposed_tasks = [t.model_dump() for t in result.proposed_tasks]
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
        raw_repr = str(text)
        logger.warning(
            "Failed to parse plan response: %s. Raw (%d chars): %.500s",
            exc, len(raw_repr), raw_repr,
        )
        plan = text if isinstance(text, str) and text else ""
        steps = []
        acceptance_criteria = []
        proposed_tasks = []

    return {
        "plan": plan,
        "steps": steps,
        "acceptance_criteria": acceptance_criteria,
        "proposed_tasks": proposed_tasks,
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

    proposed = plan_data.get("proposed_tasks", [])
    if proposed:
        if lines:
            lines.append("")
        lines.append("## Proposed Tasks")
        lines.append("")
        for i, task in enumerate(proposed, 1):
            title = task.get("title", "") if isinstance(task, dict) else str(task)
            lines.append(f"{i}. {title}")
            desc = task.get("description", "") if isinstance(task, dict) else ""
            if desc:
                lines.append(f"   {desc}")

    return "\n".join(lines).strip()
