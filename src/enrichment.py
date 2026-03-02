"""AI-assisted task enrichment via Claude CLI.

Takes a task title and returns an AI-generated description and priority
suggestion. Reuses the review_pipeline pattern for Claude CLI invocation
and JSON extraction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil

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


async def enrich_task_title(title: str) -> dict[str, str]:
    """Call Claude CLI to generate a description and priority for a task title.

    Args:
        title: The raw task title to enrich.

    Returns:
        Dict with ``description`` (str) and ``priority`` (str, e.g. "P0").

    Raises:
        RuntimeError: If the Claude CLI subprocess fails.
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
    stdout_bytes, stderr_bytes = await proc.communicate()

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
