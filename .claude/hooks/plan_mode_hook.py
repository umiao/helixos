"""PreToolUse hook: enforce plan mode by blocking mutating tools."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402
from plan_mode import is_active, record_drift  # noqa: E402

# Tools that are always allowed in plan mode (read-only + navigation)
READ_ONLY_TOOLS = frozenset({
    "Read",
    "Glob",
    "Grep",
    "Agent",
    "Skill",
    "ToolSearch",
    "AskUserQuestion",
    "TaskGet",
    "TaskList",
    "EnterPlanMode",
    "ExitPlanMode",
})

# Bash command prefixes that are read-only
ALLOWED_BASH_PREFIXES = (
    "cat ",
    "grep ",
    "rg ",
    "find ",
    "ls ",
    "ls\n",
    "head ",
    "tail ",
    "wc ",
    "diff ",
    "git log",
    "git diff",
    "git show",
    "git status",
    "git branch",
    "echo ",
    "pwd",
)

# Scripts that are allowed even though they modify state
ALLOWED_SCRIPT_FRAGMENTS = (
    "task_db.py",
    "plan_mode.py",
    "plan_validate.py",
)

# Shell operators that could chain a blocked command after an allowed one
SHELL_CHAIN_OPERATORS = (";", "&&", "||", "|")


def _is_bash_allowed(command: str) -> bool:
    """Check if a Bash command is allowed in plan mode.

    Args:
        command: The shell command string to check.

    Returns:
        True if the command is safe for plan mode.
    """
    stripped = command.strip()

    # Allow task_db / plan_mode / plan_validate scripts
    for fragment in ALLOWED_SCRIPT_FRAGMENTS:
        if fragment in stripped:
            return True

    # Check for shell chaining operators -- if present, reject
    # unless it's a simple allowed-script command
    for op in SHELL_CHAIN_OPERATORS:
        if op in stripped:
            return False

    # Check read-only prefixes
    for prefix in ALLOWED_BASH_PREFIXES:
        if stripped.startswith(prefix) or stripped == prefix.strip():
            return True

    return False


def main(hook_input: dict) -> None:
    """Check if tool is allowed in plan mode, block if not."""
    # Fast path: if plan mode is not active, allow everything
    if not is_active():
        sys.exit(0)

    tool_name = hook_input.get("tool_name", "")

    # Allow read-only tools
    if tool_name in READ_ONLY_TOOLS:
        sys.exit(0)

    # Check Bash commands individually
    if tool_name == "Bash":
        tool_input = hook_input.get("tool_input", {})
        command = tool_input.get("command", "")
        if _is_bash_allowed(command):
            sys.exit(0)

    # Block everything else
    record_drift()

    # Build block message
    msg = (
        f"[PLAN MODE] {tool_name} blocked. "
        "Only read-only tools and task_db.py allowed. "
        "Deactivate: python .claude/hooks/plan_mode.py deactivate"
    )

    # Escalate warning at 5+ drift attempts
    from plan_mode import _read_state
    state = _read_state()
    drift_count = state.get("drift_count", 0)
    if drift_count >= 5:
        msg += (
            f"\n[WARN] {drift_count} execution attempts blocked this session. "
            "You are in planning mode -- focus on task decomposition, not implementation."
        )

    print(json.dumps({"decision": "block", "reason": msg}))
    sys.exit(0)


if __name__ == "__main__":
    run_hook("plan_mode_hook", main)
