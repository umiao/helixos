"""SessionStart hook: auto-archive PROGRESS.md entries and completed tasks.

Runs BEFORE session_context.py so context sees trimmed files.

DB-first: uses .claude/tasks.db for task archival when available,
falls back to regex-based TASKS.md parsing.

Uses hysteresis thresholds to avoid frequent IO:
  - PROGRESS.md: trigger at >80 entries, keep 40
  - tasks.db completed: trigger at >20 entries, keep 5

Always exits 0 -- archival failure must not block sessions.
"""

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402

# Regex matching PROGRESS.md entry headers (same as session_context.py)
_PROGRESS_ENTRY_RE = re.compile(r"(?=^## \d{4}-\d{2}-\d{2})", re.MULTILINE)

# Legacy regex patterns for TASKS.md fallback (no DB available)
_COMPLETED_BLOCK_RE = re.compile(
    r"^####\s+\[x\]\s+T-P\d+-\d+:.+?(?=\n####|\n##|\n- (?:\[x\]|T-P)|\Z)",
    re.MULTILINE | re.DOTALL,
)
_COMPLETED_ONELINER_RE = re.compile(
    r"^- (?:\[x\]\s+)?(?:\*\*\d{4}-\d{2}-\d{2}\*\*\s+--\s+)?T-P\d+-\d+:.+$",
    re.MULTILINE,
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


def _atomic_write(filepath: Path, content: str) -> None:
    """Write content to file atomically via temp file + os.replace()."""
    dirpath = filepath.parent
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp_path, filepath)
    except Exception:
        os.close(fd) if not os.get_inheritable(fd) else None
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _parse_archive_counter(header_text: str) -> int:
    """Extract archived count from header like '> 147 session entries archived...'."""
    match = re.search(r"> (\d+) (?:session entries|completed tasks) archived", header_text)
    return int(match.group(1)) if match else 0


def archive_progress(root: Path, max_entries: int = 80, keep_entries: int = 40) -> int:
    """Archive old PROGRESS.md entries when count exceeds max_entries.

    Keeps the most recent keep_entries entries. Appends the rest to
    archive/progress_log.md in chronological order (oldest first).

    Args:
        root: Project root directory.
        max_entries: Trigger archival when entry count exceeds this.
        keep_entries: Number of recent entries to keep.

    Returns:
        Number of entries archived (0 if threshold not reached).
    """
    progress_file = root / "PROGRESS.md"
    if not progress_file.exists():
        return 0

    content = progress_file.read_text(encoding="utf-8")
    parts = _PROGRESS_ENTRY_RE.split(content)

    # First part is the header (before any ## YYYY-MM-DD entry)
    header = parts[0] if parts else ""
    entries = [p for p in parts[1:] if p.strip()]

    if len(entries) <= max_entries:
        return 0

    # Split: entries to archive (older) and entries to keep (newer)
    to_archive = entries[: len(entries) - keep_entries]
    to_keep = entries[len(entries) - keep_entries :]

    # Read existing archive to get cumulative count
    archive_file = root / "archive" / "progress_log.md"
    archive_file.parent.mkdir(parents=True, exist_ok=True)

    existing_archive = ""
    if archive_file.exists():
        existing_archive = archive_file.read_text(encoding="utf-8")

    prev_count = _parse_archive_counter(existing_archive)
    new_count = prev_count + len(to_archive)

    # Build archive content: append new entries at the end (chronological)
    archive_header = (
        "# Progress Log Archive\n\n"
        f"> {new_count} session entries archived as of latest archival.\n\n"
    )
    # Strip old header if present, keep only entries
    if existing_archive:
        existing_entries_match = re.search(
            r"(?=^## \d{4}-\d{2}-\d{2})", existing_archive, re.MULTILINE
        )
        if existing_entries_match:
            existing_body = existing_archive[existing_entries_match.start() :]
        else:
            existing_body = ""
    else:
        existing_body = ""

    archive_content = archive_header + existing_body
    if existing_body and not existing_body.endswith("\n\n"):
        archive_content += "\n" if not existing_body.endswith("\n") else ""
    archive_content += "\n".join(e.rstrip() for e in to_archive) + "\n"

    _atomic_write(archive_file, archive_content)

    # Update PROGRESS.md header with new archived count
    total_archived = new_count
    updated_header = re.sub(
        r"> \d+ session entries archived.*",
        f"> {total_archived} session entries archived as of {_today()}.",
        header,
    )
    # If no counter line exists, add one after the size invariant line
    if f"> {total_archived} session entries archived" not in updated_header:
        updated_header = updated_header.rstrip() + f"\n> {total_archived} session entries archived as of {_today()}.\n\n"

    new_content = updated_header + "\n".join(e.rstrip() for e in to_keep) + "\n"
    _atomic_write(progress_file, new_content)

    return len(to_archive)


def archive_completed_tasks_db(root: Path, max_completed: int = 20, keep_completed: int = 5) -> int:
    """Archive completed tasks using the SQLite database.

    Args:
        root: Project root directory.
        max_completed: Trigger archival when completed count exceeds this.
        keep_completed: Number of recent completed tasks to keep.

    Returns:
        Number of tasks archived (0 if threshold not reached).
    """
    db_path = root / ".claude" / "tasks.db"
    if not db_path.exists():
        return 0

    from task_store import TaskStore

    store = TaskStore(str(db_path))
    try:
        archived = store.archive(max_completed=max_completed, keep_completed=keep_completed)

        if archived > 0:
            # Regenerate TASKS.md projection
            from task_db import _write_projection
            _write_projection(root, store)

            # Also update archive/completed_tasks.md from archived_tasks table
            _sync_archive_file(root, store)

        return archived
    finally:
        store.close()


def _sync_archive_file(root: Path, store: "TaskStore") -> None:  # noqa: F821
    """Sync archive/completed_tasks.md from the archived_tasks table."""
    archive_file = root / "archive" / "completed_tasks.md"
    archive_file.parent.mkdir(parents=True, exist_ok=True)

    archived = store.list_archived()
    if not archived:
        return

    lines = [
        "# Completed Tasks Archive",
        "",
        f"> {len(archived)} completed tasks archived as of latest archival.",
        "",
    ]
    for task in reversed(archived):  # Chronological order (oldest first)
        date = task.completed_at or "unknown"
        if task.summary:
            lines.append(f"- [x] **{date}** -- {task.id}: {task.title}. {task.summary[:120]}")
        else:
            lines.append(f"- [x] **{date}** -- {task.id}: {task.title}")
    lines.append("")

    _atomic_write(archive_file, "\n".join(lines))


def archive_completed_tasks_legacy(root: Path, max_completed: int = 20, keep_completed: int = 5) -> int:
    """Archive old completed task entries from TASKS.md (legacy regex-based).

    Used as fallback when .claude/tasks.db is not available.
    """
    tasks_file = root / "TASKS.md"
    if not tasks_file.exists():
        return 0

    content = tasks_file.read_text(encoding="utf-8")

    # Find the ## Completed Tasks section
    completed_match = re.search(
        r"(## Completed Tasks\s*\n)(.*?)(\Z)",
        content,
        re.DOTALL,
    )
    if not completed_match:
        return 0

    section_header = completed_match.group(1)
    section_body = completed_match.group(2)
    before_completed = content[: completed_match.start()]

    # Parse completed entries: both #### [x] blocks and - T-P... oneliners
    entries: list[tuple[str, int, str]] = []

    # Find #### [x] blocks
    for m in _COMPLETED_BLOCK_RE.finditer(section_body):
        entries.append(("block", m.start(), m.group(0).strip()))

    # Find oneliners (only if not inside a block)
    block_ranges = [(m.start(), m.end()) for m in _COMPLETED_BLOCK_RE.finditer(section_body)]
    for m in _COMPLETED_ONELINER_RE.finditer(section_body):
        in_block = any(start <= m.start() < end for start, end in block_ranges)
        if not in_block:
            entries.append(("oneliner", m.start(), m.group(0).strip()))

    # Sort by position (preserves document order)
    entries.sort(key=lambda x: x[1])
    entry_texts = [e[2] for e in entries]

    if len(entry_texts) <= max_completed:
        return 0

    to_archive = entry_texts[: len(entry_texts) - keep_completed]
    to_keep = entry_texts[len(entry_texts) - keep_completed :]

    # Read existing archive
    archive_file = root / "archive" / "completed_tasks.md"
    archive_file.parent.mkdir(parents=True, exist_ok=True)

    existing_archive = ""
    if archive_file.exists():
        existing_archive = archive_file.read_text(encoding="utf-8")

    prev_count = _parse_archive_counter(existing_archive)
    new_count = prev_count + len(to_archive)

    # Build archive
    archive_header = (
        "# Completed Tasks Archive\n\n"
        f"> {new_count} completed tasks archived as of latest archival.\n\n"
    )
    if existing_archive:
        first_entry = re.search(
            r"^(?:####\s+\[x\]\s+T-P\d+-\d+|- (?:\[x\]\s+)?(?:\*\*\d{4}-\d{2}-\d{2}\*\*\s+--\s+)?T-P\d+-\d+):",
            existing_archive,
            re.MULTILINE,
        )
        existing_body = existing_archive[first_entry.start() :] if first_entry else ""
    else:
        existing_body = ""

    archive_content = archive_header + existing_body
    if existing_body and not existing_body.endswith("\n"):
        archive_content += "\n"
    archive_content += "\n\n".join(to_archive) + "\n"

    _atomic_write(archive_file, archive_content)

    # Rebuild the Completed Tasks section
    new_section = section_header
    new_section += f"\n> {new_count} completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).\n\n"
    new_section += "\n\n".join(to_keep) + "\n"

    new_content = before_completed + new_section
    _atomic_write(tasks_file, new_content)

    return len(to_archive)


def archive_completed_tasks(
    root: Path,
    max_completed: int = 20,
    keep_completed: int = 5,
) -> int:
    """Archive completed tasks, using DB when available, legacy otherwise.

    Args:
        root: Project root directory.
        max_completed: Trigger archival when completed count exceeds this.
        keep_completed: Number of recent completed tasks to keep.

    Returns:
        Number of tasks archived (0 if threshold not reached).
    """
    db_path = root / ".claude" / "tasks.db"
    if db_path.is_file():
        return archive_completed_tasks_db(root, max_completed, keep_completed)
    return archive_completed_tasks_legacy(root, max_completed, keep_completed)


def _today() -> str:
    """Return today's date as YYYY-MM-DD."""
    import datetime
    return datetime.date.today().isoformat()


def main(hook_input: dict) -> None:
    """Run archival checks for PROGRESS.md and TASKS.md/tasks.db."""
    root = _find_project_root()

    archived_progress = 0
    archived_tasks = 0

    try:
        archived_progress = archive_progress(root)
    except Exception as exc:
        print(f"[ARCHIVE] PROGRESS.md archival failed: {exc}", file=sys.stderr)

    # Try DB-based archival first, fall back to legacy
    db_path = root / ".claude" / "tasks.db"
    try:
        if db_path.exists():
            archived_tasks = archive_completed_tasks_db(root)
        else:
            archived_tasks = archive_completed_tasks_legacy(root)
    except Exception as exc:
        print(f"[ARCHIVE] Task archival failed: {exc}", file=sys.stderr)

    if archived_progress or archived_tasks:
        print(
            f"[ARCHIVE] Archived {archived_progress} progress entries, "
            f"{archived_tasks} completed tasks.",
            file=sys.stderr,
        )

    sys.exit(0)


if __name__ == "__main__":
    run_hook("archive_check", main)
