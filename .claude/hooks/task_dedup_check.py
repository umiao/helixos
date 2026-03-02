"""Stop hook: detect tasks appearing in both Active/In Progress and Completed."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402


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


def _extract_section_task_ids(content: str, section_name: str) -> set[str]:
    """Extract all task IDs (T-P\\d+-\\d+) from a named ## section."""
    match = re.search(
        rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)",
        content,
        re.DOTALL,
    )
    if not match:
        return set()
    return set(re.findall(r"(T-P\d+-\d+)", match.group(1)))


def main(hook_input: dict) -> None:
    """Block stop if tasks appear in both Active/In Progress and Completed.

    Args:
        hook_input: Parsed JSON dict from stdin (Stop hook payload).
    """
    root = _find_project_root()
    tasks_file = root / "TASKS.md"
    if not tasks_file.exists():
        sys.exit(0)

    content = tasks_file.read_text(encoding="utf-8")

    # Collect task IDs from active sections
    active_ids: set[str] = set()
    for section in ["In Progress", "Active Tasks", "Blocked"]:
        active_ids |= _extract_section_task_ids(content, section)

    completed_ids = _extract_section_task_ids(content, "Completed Tasks")

    overlap = active_ids & completed_ids
    if overlap:
        sorted_overlap = sorted(overlap)
        print(
            f"[TASK DEDUP] Found {len(overlap)} task(s) in both Active and "
            f"Completed sections: {', '.join(sorted_overlap)}. "
            f"Remove their spec blocks from Active Tasks before stopping.",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    run_hook("task_dedup_check", main)
