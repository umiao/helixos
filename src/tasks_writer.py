"""TasksWriter -- safely append tasks to a project's TASKS.md.

Uses filelock for cross-platform file locking, creates .bak backups
before every write, and validates file integrity after writing.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock

logger = logging.getLogger(__name__)

# Regex to extract task IDs like T-P0-1, T-P2-14
TASK_ID_RE = re.compile(r"T-P(\d+)-(\d+)")

# Section header regex (## level)
SECTION_RE = re.compile(r"^##\s+(.*)", re.MULTILINE)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class NewTask:
    """A task to be appended to TASKS.md."""

    title: str
    description: str = ""
    priority: str = "P0"


@dataclass
class WriteResult:
    """Result of a tasks_writer operation."""

    task_id: str
    success: bool
    backup_path: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def _scan_existing_ids(content: str) -> list[tuple[int, int]]:
    """Scan content for all T-P{priority}-{number} IDs.

    Returns list of (priority_int, number_int) tuples.
    """
    return [
        (int(m.group(1)), int(m.group(2)))
        for m in TASK_ID_RE.finditer(content)
    ]


def generate_next_task_id(content: str, priority: str) -> str:
    """Generate the next sequential task ID for a given priority.

    Scans existing IDs in the content and returns the next available
    number for the specified priority level.

    Args:
        content: The full TASKS.md content to scan.
        priority: Priority string like "P0", "P1", "P2".

    Returns:
        A task ID string like "T-P0-5".
    """
    priority_num = int(priority.lstrip("P"))
    existing = _scan_existing_ids(content)

    max_num = 0
    for p, n in existing:
        if p == priority_num and n >= max_num:
            max_num = n + 1

    return f"T-{priority}-{max_num}"


# ---------------------------------------------------------------------------
# Section finder
# ---------------------------------------------------------------------------


def _find_active_section_end(content: str) -> int | None:
    """Find the insertion point at the end of the Active Tasks section.

    Looks for ## Active Tasks (or ## Active) and returns the byte offset
    just before the next ## section header, or end of file if no next section.

    Returns None if no Active section is found.
    """
    lines = content.split("\n")
    in_active = False
    insert_line = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        if re.match(r"^##\s+", stripped):
            header_text = re.sub(r"^##\s+", "", stripped).strip().lower()

            if in_active:
                # We've hit the next ## section -- insert before it
                insert_line = i
                break

            if "active" in header_text:
                in_active = True
                continue

    if in_active and insert_line is None:
        # Active section goes to end of file
        insert_line = len(lines)

    return insert_line


def _build_task_block(task_id: str, task: NewTask) -> str:
    """Build the markdown block for a new task.

    Args:
        task_id: Generated task ID (e.g. "T-P0-5").
        task: The NewTask to format.

    Returns:
        Markdown string ready to insert.
    """
    block = f"#### {task_id}: {task.title}\n"
    if task.description:
        block += f"- {task.description}\n"
    return block


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_written_file(path: Path, expected_id: str) -> str | None:
    """Re-read and validate the file after writing.

    Returns an error string if validation fails, None on success.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"Failed to re-read file: {exc}"

    # Check that the expected ID exists
    if expected_id not in content:
        return f"Task ID {expected_id} not found after write"

    # Check markdown is parseable (basic: no null bytes, valid lines)
    if "\x00" in content:
        return "File contains null bytes after write"

    return None


# ---------------------------------------------------------------------------
# TasksWriter
# ---------------------------------------------------------------------------


class TasksWriter:
    """Safely append tasks to a project's TASKS.md file.

    Uses filelock for concurrent access protection, creates .bak
    backups before modifications, and validates file integrity
    after writing.
    """

    def __init__(self, tasks_md_path: Path) -> None:
        """Initialize with the path to the TASKS.md file.

        Args:
            tasks_md_path: Absolute path to the TASKS.md file.
        """
        self._path = tasks_md_path
        self._lock_path = tasks_md_path.parent / f".{tasks_md_path.name}.lock"
        self._file_lock = FileLock(self._lock_path, timeout=10)
        self._thread_lock = threading.Lock()

    @property
    def path(self) -> Path:
        """The path to the TASKS.md file."""
        return self._path

    def append_task(self, task: NewTask) -> WriteResult:
        """Append a new task to the Active Tasks section of TASKS.md.

        The entire read-modify-write cycle runs under both a threading
        lock (in-process safety) and a file lock (cross-process safety).
        A .bak backup is created before any modification.

        Args:
            task: The NewTask to append.

        Returns:
            WriteResult with the generated task ID and status.
        """
        with self._thread_lock, self._file_lock:
            return self._append_task_locked(task)

    def _append_task_locked(self, task: NewTask) -> WriteResult:
        """Internal: append task while holding the lock.

        Args:
            task: The NewTask to append.

        Returns:
            WriteResult with the generated task ID and status.
        """
        # Read current content (or start with template if empty/missing)
        content = self._path.read_text(encoding="utf-8") if self._path.is_file() else ""

        # Handle empty file -- create minimal structure
        if not content.strip():
            content = "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n"

        # Generate task ID inside lock (ensures uniqueness)
        task_id = generate_next_task_id(content, task.priority)

        # Find insertion point
        insert_line = _find_active_section_end(content)
        if insert_line is None:
            # No Active section found -- append one
            content = content.rstrip("\n") + "\n\n## Active Tasks\n\n"
            insert_line = len(content.split("\n"))

        # Build the task block
        task_block = _build_task_block(task_id, task)

        # Insert the task block
        lines = content.split("\n")
        # Insert before the next section (with a blank line separator)
        insert_lines = task_block.rstrip("\n").split("\n") + [""]
        lines[insert_line:insert_line] = insert_lines
        new_content = "\n".join(lines)

        # Create .bak backup before writing
        backup_path: str | None = None
        if self._path.is_file():
            bak_path = self._path.with_suffix(".md.bak")
            shutil.copy2(str(self._path), str(bak_path))
            backup_path = str(bak_path)
            logger.info("Created backup: %s", bak_path)

        # Write the new content
        self._path.write_text(new_content, encoding="utf-8")
        logger.info("Appended task %s to %s", task_id, self._path)

        # Post-write validation
        error = _validate_written_file(self._path, task_id)
        if error is not None:
            logger.error("Post-write validation failed: %s", error)
            # Restore from backup
            if backup_path is not None:
                shutil.copy2(backup_path, str(self._path))
                logger.info("Restored from backup after validation failure")
            return WriteResult(
                task_id=task_id,
                success=False,
                backup_path=backup_path,
                error=error,
            )

        return WriteResult(
            task_id=task_id,
            success=True,
            backup_path=backup_path,
        )
