"""PostToolUse hook: warn when TASKS.md has malformed task headers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import find_malformed_task_headers, run_hook  # noqa: E402


def _find_project_root() -> Path:
    """Find the project root by looking for CLAUDE.md."""
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent.parent,
    ]
    for candidate in candidates:
        if (candidate / "CLAUDE.md").exists():
            return candidate
    return Path.cwd()


def main(hook_input: dict) -> None:
    """Warn if TASKS.md contains #### headers without T-PX-NN: IDs.

    Only triggers when the edited file is TASKS.md. Non-blocking (always exit 0).

    Args:
        hook_input: Parsed JSON dict from stdin with tool_input.file_path.
    """
    # Short-circuit: only check when TASKS.md was edited
    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")
    if not file_path or not file_path.replace("\\", "/").rstrip("/").endswith("TASKS.md"):
        sys.exit(0)

    root = _find_project_root()
    tasks_file = root / "TASKS.md"

    try:
        content = tasks_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"[TASK HEADER] Could not read TASKS.md: {exc}",
            file=sys.stderr,
        )
        sys.exit(0)

    errors = find_malformed_task_headers(content)
    if errors:
        lines = "\n".join(
            f"  Line {e.line_num}: {e.line_text}" for e in errors
        )
        print(
            f"[TASK HEADER] {len(errors)} task header(s) in Active/In Progress "
            f"sections missing T-PX-NN: ID prefix:\n{lines}\n"
            f"Assign the next sequential task ID before stopping.",
            file=sys.stderr,
        )

    sys.exit(0)


if __name__ == "__main__":
    run_hook("task_header_check", main)
