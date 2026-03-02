"""<Hook type> hook: <description>.

Replace <Hook type> with PreToolUse, PostToolUse, Stop, or SessionStart.
Replace <description> with what this hook does.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402


def main(hook_input: dict) -> None:
    """<Describe what this hook checks or enforces>.

    Args:
        hook_input: Parsed JSON dict from stdin. Common fields:
            - tool_name (str): "Bash", "Write", "Edit", etc.
            - tool_input (dict): Tool-specific parameters
            - stop_hook_active (bool): True if stop hook already fired (Stop hooks only)
    """
    # Your hook logic here.
    # Use sys.exit(0) to allow / pass through.
    # Use sys.exit(2) to block (Stop hooks only).
    # Print JSON {"decision": "block", "reason": "..."} to stdout to block (PreToolUse).
    # Print warnings to stderr (non-blocking).
    sys.exit(0)


if __name__ == "__main__":
    run_hook("<hook_name>", main)
