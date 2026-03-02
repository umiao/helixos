"""PostToolUse hook: warn when watched files are modified.

<!-- CUSTOMIZE: Update WATCHED_PATHS with your critical file paths -->
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402

# <!-- CUSTOMIZE: Add paths to watch for changes (models, schemas, migrations, etc.) -->
WATCHED_PATHS = [
    "src/models/",
    "src/database/",
]


def main(hook_input: dict) -> None:
    """Emit a stderr warning when watched files are modified."""
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Normalize to forward slashes for matching
    normalized = file_path.replace("\\", "/")

    for watched in WATCHED_PATHS:
        if watched in normalized:
            print(
                f"[FILE WATCH] Modified watched file: {file_path}\n"
                "Remember to run tests to verify nothing is broken.",
                file=sys.stderr,
            )
            break

    # Non-blocking: always exit 0
    sys.exit(0)


if __name__ == "__main__":
    run_hook("file_watch_warn", main)
