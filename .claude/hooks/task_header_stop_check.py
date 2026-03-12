"""Stop hook: block if TASKS.md has malformed task headers."""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import (  # noqa: E402
    check_stop_cache,
    find_malformed_task_headers,
    run_hook,
    write_stop_cache,
)


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


def _content_hash(content: str) -> str:
    """Compute a short hash of file content for cache comparison."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


CACHE_NAME = "task_header"


def main(hook_input: dict) -> None:
    """Block stop if TASKS.md contains #### headers without T-PX-NN: IDs.

    Uses content-hash-based caching to skip re-checks when nothing changed.
    Fail-open: if TASKS.md is missing or unreadable, exits 0.

    Args:
        hook_input: Parsed JSON dict from stdin (Stop hook payload).
    """
    # Cache check -- skip if nothing changed
    if check_stop_cache(CACHE_NAME):
        sys.exit(0)

    root = _find_project_root()
    tasks_file = root / "TASKS.md"

    if not tasks_file.exists():
        sys.exit(0)

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
            f"Fix these headers before stopping. Each task header must use "
            f"format: #### T-PX-NN: Title",
            file=sys.stderr,
        )
        sys.exit(2)

    # All good -- cache the pass
    write_stop_cache(CACHE_NAME)
    sys.exit(0)


if __name__ == "__main__":
    run_hook("task_header_stop_check", main)
