"""PostToolUse hook: block direct edits to TASKS.md.

TASKS.md is auto-generated from .claude/tasks.db. Any direct edit is blocked
with a hard fail, directing the agent to use task_db.py instead.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402


def main(hook_input: dict) -> None:
    """Block Write/Edit operations targeting TASKS.md.

    Returns a block decision if the tool targets TASKS.md.
    Always exits 0 for non-TASKS.md files.

    Args:
        hook_input: Parsed JSON dict from stdin with tool_input.file_path.
    """
    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    # Normalize path separators and check if it ends with TASKS.md
    normalized = file_path.replace("\\", "/").rstrip("/")
    if not normalized.endswith("TASKS.md"):
        sys.exit(0)

    # Block the edit
    print(json.dumps({
        "decision": "block",
        "reason": (
            "TASKS.md is auto-generated from .claude/tasks.db. "
            "Use `python .claude/hooks/task_db.py <command>` instead. "
            "Run `task_db.py --help` for available commands."
        ),
    }))
    sys.exit(0)


if __name__ == "__main__":
    run_hook("tasks_md_guard", main)
