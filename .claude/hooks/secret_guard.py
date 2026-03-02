"""PreToolUse hook: block writes containing API key patterns or targeting .env files."""
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI API key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"ghp_[a-zA-Z0-9]{36}")),
    ("GitHub OAuth token", re.compile(r"gho_[a-zA-Z0-9]{36}")),
    ("Anthropic API key", re.compile(r"sk-ant-[a-zA-Z0-9-]{20,}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9a-zA-Z-]{10,}")),
    ("Generic secret assignment", re.compile(r"""(?:api[_-]?key|secret|token|password)\s*[=:]\s*["'][^"']{8,}["']""", re.IGNORECASE)),
]


def _is_env_file(file_path: str) -> bool:
    """Check if the path targets a .env file."""
    for cls in (PurePosixPath, PureWindowsPath):
        name = cls(file_path).name
        if name == ".env" or name.startswith(".env."):
            return True
    return False


def main(hook_input: dict) -> None:
    """Block file writes that contain API key patterns or target .env files."""
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Block writes to .env files
    if _is_env_file(file_path):
        print(
            json.dumps({
                "decision": "block",
                "reason": f"Blocked: writing to .env file '{file_path}'. "
                "Secrets must be managed manually, not by AI.",
            })
        )
        sys.exit(0)

    # Check content for secret patterns
    content = tool_input.get("content", "") + tool_input.get("new_string", "")
    for secret_name, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            print(
                json.dumps({
                    "decision": "block",
                    "reason": f"Blocked: content appears to contain a {secret_name}. "
                    "Never hardcode secrets in source files.",
                })
            )
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    run_hook("secret_guard", main)
