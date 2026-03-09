"""PreToolUse hook: block git commit messages containing CJK characters."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402

# CJK character ranges: Unified Ideographs, CJK Punctuation, Hiragana, Katakana, Korean
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\u4e00-\u9fff\uac00-\ud7af]",
)


def _extract_commit_message(command: str) -> str:
    """Extract the commit message from a git commit command string.

    Handles:
    - git commit -m "message"
    - git commit -m 'message'
    - git commit -m "$(cat <<'EOF' ... EOF )"  (heredoc)

    Args:
        command: The full shell command string.

    Returns:
        The extracted message, or empty string if not found.
    """
    # Heredoc pattern: $(cat <<'EOF' ... EOF ) or $(cat <<EOF ... EOF )
    heredoc_match = re.search(
        r"\$\(cat\s+<<'?(\w+)'?\s*\n(.*?)\n\s*\1",
        command,
        re.DOTALL,
    )
    if heredoc_match:
        return heredoc_match.group(2)

    # Standard -m with double or single quotes
    # Try double quotes first
    m_match = re.search(r'''-m\s+"((?:[^"\\]|\\.)*)"''', command)
    if m_match:
        return m_match.group(1)

    # Single quotes
    m_match = re.search(r"""-m\s+'((?:[^'\\]|\\.)*)'""", command)
    if m_match:
        return m_match.group(1)

    # Unquoted (single word after -m)
    m_match = re.search(r"-m\s+(\S+)", command)
    if m_match:
        return m_match.group(1)

    return ""


def main(hook_input: dict) -> None:
    """Block git commit commands whose message contains CJK characters.

    Args:
        hook_input: Parsed JSON dict from stdin with tool_name and tool_input.
    """
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Bash":
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # Only check git commit commands
    if not re.search(r"\bgit\s+commit\b", command):
        sys.exit(0)

    message = _extract_commit_message(command)
    if not message:
        sys.exit(0)

    if _CJK_RE.search(message):
        print(
            json.dumps({
                "decision": "block",
                "reason": "Commit message contains CJK characters. Use English only.",
            })
        )
        sys.exit(0)

    # Allow the command
    sys.exit(0)


if __name__ == "__main__":
    run_hook("commit_msg_guard", main)
