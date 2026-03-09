"""PreToolUse hook: block writes containing secrets, personal paths, or targeting sensitive files."""
import json
import re
import sys
from fnmatch import fnmatch
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
    ("Generic secret assignment", re.compile(
        r"""(?:api[_-]?key|secret|token|password)\s*[=:]\s*["'][^"']{8,}["']""",
        re.IGNORECASE,
    )),
    ("Private key block", re.compile(r"-----BEGIN.*PRIVATE KEY-----")),
    ("Windows user profile path", re.compile(r"[Cc]:[/\\]+Users[/\\]+[^/\\]+[/\\]")),
]

SENSITIVE_FILE_PATTERNS: list[str] = [
    ".env",
    ".env.*",
    "*.cookie",
    "*.pem",
    "*.key",
    "credentials*",
    "settings.local.json",
]


def _is_sensitive_file(file_path: str) -> bool:
    """Check if the path targets a sensitive file that should not be written by AI."""
    for cls in (PurePosixPath, PureWindowsPath):
        name = cls(file_path).name
        for pattern in SENSITIVE_FILE_PATTERNS:
            if fnmatch(name, pattern):
                return True
    return False


def main(hook_input: dict) -> None:
    """Block file writes that contain secrets/personal paths or target sensitive files."""
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Block writes to sensitive files
    if _is_sensitive_file(file_path):
        print(
            json.dumps({
                "decision": "block",
                "reason": f"Blocked: writing to sensitive file '{file_path}'. "
                "Secrets and local settings must be managed manually, not by AI.",
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
                    "Never hardcode secrets or personal paths in source files.",
                })
            )
            sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    run_hook("secret_guard", main)
