"""PreToolUse hook: block dangerous shell commands."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402

DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\s+/\b"),
    re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\|\s*bash\b"),
    re.compile(r"\bwget\b.*\|\s*bash\b"),
    re.compile(r"\bgit\s+push\s+--force\s+(origin\s+)?main\b"),
    re.compile(r"\bgit\s+push\s+-f\s+(origin\s+)?main\b"),
    re.compile(r"\bgit\s+push\s+--force\s+(origin\s+)?master\b"),
    re.compile(r"\bgit\s+push\s+-f\s+(origin\s+)?master\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    re.compile(r"\bgit\s+clean\s+-fd\b"),
    re.compile(r"\bformat\s+[cCdD]:\b"),
    re.compile(r"\b:(){ :\|:& };:\b"),  # fork bomb
]


def main(hook_input: dict) -> None:
    """Check Bash commands against dangerous patterns and block matches."""
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            print(
                json.dumps({
                    "decision": "block",
                    "reason": f"Dangerous command blocked: matches pattern '{pattern.pattern}'. "
                    f"Command: {command[:100]}",
                })
            )
            sys.exit(0)

    # Allow the command
    sys.exit(0)


if __name__ == "__main__":
    run_hook("block_dangerous", main)
