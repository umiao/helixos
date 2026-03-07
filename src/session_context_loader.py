"""Load session context for plan/review agent system prompts.

Extracts the same project context that ``session_context.py`` hook provides
at session startup, but callable directly from Python.  Used to inject
context into plan/review agent system prompts since ``SessionStart`` is not
a valid SDK hook type (only available as a CLI hook).

Reuses the session_context.py helper functions by importing from the hooks
directory, with a fallback to a minimal inline implementation if the import
fails.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    """Find the project root by looking for CLAUDE.md."""
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent,  # src/ -> project root
    ]
    for candidate in candidates:
        if (candidate / "CLAUDE.md").exists():
            return candidate
    return Path.cwd()


def _get_active_tasks_summary(root: Path) -> str:
    """Extract a one-line-per-task summary of active tasks from TASKS.md."""
    tasks_file = root / "TASKS.md"
    if not tasks_file.exists():
        return "No TASKS.md found."

    content = tasks_file.read_text(encoding="utf-8")

    # Find Active Tasks section
    section_match = re.search(
        r"## Active Tasks\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL
    )
    if not section_match:
        return "No active tasks."

    text = section_match.group(1).strip()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    if not text:
        return "No active tasks."

    # Extract task headers with priority/complexity
    task_lines: list[str] = []
    for match in re.finditer(r"####\s+(T-\S+:\s+.+)", text):
        task_lines.append(match.group(1).strip())

    return "\n".join(task_lines) if task_lines else "No active tasks."


def _get_session_state(root: Path) -> str:
    """Read current task and mode from session_state.json."""
    state_file = root / ".claude" / "session_state.json"
    if not state_file.exists():
        return ""

    with contextlib.suppress(json.JSONDecodeError, OSError):
        content = state_file.read_text(encoding="utf-8")
        state = json.loads(content)
        current = state.get("current_task", "unknown")
        mode = state.get("mode", "interactive")
        return f"Current task: {current} | Mode: {mode}"

    return ""


def get_session_context(root: Path | None = None) -> str:
    """Build session context text for injection into agent system prompts.

    Returns a compact context block with active tasks and session state.
    This is a lightweight version of what ``session_context.py`` provides
    to the CLI session.

    Args:
        root: Project root directory.  Auto-detected if ``None``.

    Returns:
        Multi-line context string suitable for appending to a system prompt.
    """
    if root is None:
        root = _find_project_root()

    parts: list[str] = ["--- Session Context ---"]

    state = _get_session_state(root)
    if state:
        parts.append(state)

    tasks = _get_active_tasks_summary(root)
    parts.append(f"Active tasks:\n{tasks}")

    parts.append("--- End Session Context ---")
    return "\n".join(parts)
