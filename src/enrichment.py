"""AI-assisted task enrichment and plan generation via Claude CLI.

Provides two capabilities:
1. **Enrichment**: Takes a task title and returns AI-generated description
   and priority suggestion.
2. **Plan generation**: Takes task title + description + optional codebase
   path and returns a structured implementation plan. Uses ``claude -p``
   with ``--add-dir`` for codebase context and ``--permission-mode plan``
   for read-only access.

Note on ``--plan`` flag: Claude CLI does not have a ``--plan`` flag.
Instead, plan generation uses standard CLI features (``-p``,
``--system-prompt``, ``--json-schema``, ``--add-dir``) which are stable
and documented.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


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
        "--max-budget-usd", "0.10",
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
        raise RuntimeError(
            f"Enrichment subprocess timed out after {timeout_minutes} minutes"
        ) from None

    if proc.returncode != 0:
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Claude CLI failed (exit {proc.returncode}): {stderr_text}"
        )

    # Claude CLI wraps output in {"result": "..."}
    cli_output = json.loads(stdout_bytes.decode("utf-8"))
    result_text = cli_output.get("result", "")

    return _parse_enrichment(result_text)


def _parse_enrichment(text: str) -> dict[str, str]:
    """Parse the Claude CLI enrichment result into description + priority.

    Args:
        text: The ``result`` field from Claude CLI JSON output.

    Returns:
        Dict with ``description`` and ``priority`` keys.
    """
    try:
        data = json.loads(text)
        description = str(data.get("description", ""))
        priority = str(data.get("priority", "P1"))
        # Validate priority is one of P0/P1/P2
        if priority not in ("P0", "P1", "P2"):
            priority = "P1"
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Failed to parse enrichment response, using defaults")
        description = ""
        priority = "P1"

    return {"description": description, "priority": priority}


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
) -> dict:
    """Call Claude CLI to generate a structured implementation plan.

    Uses ``claude -p`` with ``--add-dir`` for codebase context when
    *repo_path* is provided.  Streams stdout line-by-line via *on_log*
    callback for real-time feedback (same pattern as CodeExecutor).

    Args:
        title: The task title.
        description: Existing task description (may be empty).
        repo_path: Optional path to the project repository for codebase
            context via ``--add-dir``.
        timeout_minutes: Maximum time in minutes before the subprocess is
            killed. 0 disables the timeout.
        on_log: Optional callback invoked per stdout line for real-time
            streaming.  Signature: ``(line: str) -> None``.
        heartbeat_seconds: If no stdout line arrives within this many
            seconds, emit a synthetic ``[PROGRESS] heartbeat`` line via
            *on_log*.  Set to 0 to disable.

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
        "--output-format", "json",
        "--no-session-persistence",
        "--max-budget-usd", "0.50",
        "--json-schema", _PLAN_JSON_SCHEMA,
    ]

    # Give Claude read-only codebase access if repo_path exists
    if repo_path is not None and repo_path.is_dir():
        args.extend(["--add-dir", str(repo_path)])
        args.extend(["--permission-mode", "plan"])

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    timeout_seconds = timeout_minutes * 60 if timeout_minutes > 0 else None
    log_lines: list[str] = []
    last_output_time = time.monotonic()

    def _emit(line: str) -> None:
        if on_log is not None:
            on_log(line)

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

                decoded = raw_line.decode("utf-8", errors="replace").strip()
                if decoded:
                    log_lines.append(decoded)
                    _emit(decoded)
                    last_output_time = time.monotonic()

            await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(
            f"Plan generation subprocess timed out after {timeout_minutes} minutes"
        ) from None

    if proc.returncode != 0:
        stderr_bytes = b""
        if proc.stderr is not None:
            stderr_bytes = await proc.stderr.read()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Claude CLI failed (exit {proc.returncode}): {stderr_text}"
        )

    # Reassemble all stdout lines and parse the JSON result
    full_output = "\n".join(log_lines)
    try:
        cli_output = json.loads(full_output)
    except json.JSONDecodeError:
        # If the output isn't valid JSON, try the last line (some CLIs
        # emit progress text before the final JSON blob)
        cli_output = {}
        for line in reversed(log_lines):
            try:
                cli_output = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    result_text = cli_output.get("result", "")
    return _parse_plan(result_text)


def _parse_plan(text: str) -> dict:
    """Parse the Claude CLI plan generation result.

    Args:
        text: The ``result`` field from Claude CLI JSON output.

    Returns:
        Dict with ``plan``, ``steps``, and ``acceptance_criteria`` keys.
    """
    try:
        data = json.loads(text)
        plan = str(data.get("plan", ""))
        steps = data.get("steps", [])
        acceptance_criteria = data.get("acceptance_criteria", [])

        # Validate steps structure
        validated_steps = []
        for s in steps:
            if isinstance(s, dict) and "step" in s:
                validated_steps.append({
                    "step": str(s["step"]),
                    "files": [str(f) for f in s.get("files", [])],
                })
        steps = validated_steps

        # Validate acceptance_criteria is list of strings
        if not isinstance(acceptance_criteria, list):
            acceptance_criteria = []
        acceptance_criteria = [str(ac) for ac in acceptance_criteria]

    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Failed to parse plan response, returning raw text")
        plan = text if text else ""
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
