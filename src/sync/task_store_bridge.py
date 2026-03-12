"""SQL-to-SQL bridge between tasks.db (Claude Code) and state.db (server).

Replaces the fragile TASKS.md intermediary (SQL -> Markdown -> Regex -> SQL)
with direct SQL-to-SQL sync via importlib-loaded task_store module.

Provides:
- Forward sync: read tasks.db -> list of BridgeTask for state.db upsert
- Reverse sync: write to tasks.db (add_task, update_task_title, update_task_status)
- ID allocation: generate_next_task_id, get_all_task_ids
- TASKS.md projection: reproject() for deterministic markdown generation
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import logging
import os
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.models import TaskStatus

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Status mappings (centralized)
# ---------------------------------------------------------------------------

# tasks.db status -> state.db TaskStatus
_FORWARD_STATUS_MAP: dict[str, TaskStatus] = {
    "active": TaskStatus.BACKLOG,
    "in_progress": TaskStatus.RUNNING,
    "completed": TaskStatus.DONE,
    "blocked": TaskStatus.BLOCKED,
}

# state.db TaskStatus -> tasks.db status
_REVERSE_STATUS_MAP: dict[TaskStatus, str] = {
    TaskStatus.BACKLOG: "active",
    TaskStatus.QUEUED: "active",
    TaskStatus.RUNNING: "in_progress",
    TaskStatus.DONE: "completed",
    TaskStatus.REVIEW: "active",
    TaskStatus.REVIEW_AUTO_APPROVED: "active",
    TaskStatus.REVIEW_NEEDS_HUMAN: "active",
    TaskStatus.FAILED: "blocked",
    TaskStatus.BLOCKED: "blocked",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BridgeTask:
    """A task read from tasks.db, mapped for state.db consumption."""

    local_task_id: str
    title: str
    description: str
    status: TaskStatus
    complexity: str
    depends_on: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------


def _load_task_store_module(repo_path: Path) -> Any:
    """Load task_store.py via importlib without polluting sys.path.

    Args:
        repo_path: Root of the repo containing .claude/hooks/task_store.py.

    Returns:
        The loaded module object.

    Raises:
        FileNotFoundError: If task_store.py is not found.
    """
    store_path = repo_path / ".claude" / "hooks" / "task_store.py"
    if not store_path.is_file():
        msg = f"task_store.py not found at {store_path}"
        raise FileNotFoundError(msg)

    spec = importlib.util.spec_from_file_location(
        "task_store_bridge._task_store", str(store_path),
    )
    if spec is None or spec.loader is None:
        msg = f"Failed to create module spec from {store_path}"
        raise ImportError(msg)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Projection helpers (replicate task_db._write_projection logic)
# ---------------------------------------------------------------------------


def _set_readonly(path: Path) -> None:
    """Set file to read-only (cross-platform)."""
    if sys.platform == "win32":
        os.system(f'attrib +R "{path}"')  # noqa: S605
    else:
        current = os.stat(path).st_mode
        os.chmod(path, current & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _remove_readonly(path: Path) -> None:
    """Remove read-only attribute (cross-platform)."""
    if sys.platform == "win32":
        os.system(f'attrib -R "{path}"')  # noqa: S605
    else:
        current = os.stat(path).st_mode
        os.chmod(path, current | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# TaskStoreBridge
# ---------------------------------------------------------------------------


class TaskStoreBridge:
    """Bridge between tasks.db and the HelixOS server.

    Uses short-lived connections to tasks.db for safe concurrent access
    with the Claude Code CLI.

    Args:
        repo_path: Root of the repository containing .claude/tasks.db.
    """

    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._db_path = repo_path / ".claude" / "tasks.db"
        self._tasks_md_path = repo_path / "TASKS.md"
        self._module = _load_task_store_module(repo_path)

    def _open_store(self) -> Any:
        """Open a fresh TaskStore connection.

        Returns:
            A TaskStore instance. Caller must call .close() when done.
        """
        return self._module.TaskStore(str(self._db_path))

    def close(self) -> None:
        """No-op -- connections are short-lived and closed per operation."""

    # ------------------------------------------------------------------
    # Forward sync: tasks.db -> state.db
    # ------------------------------------------------------------------

    def read_all_tasks(self) -> list[BridgeTask]:
        """Read all tasks from tasks.db and return as BridgeTask list.

        Maps tasks.db statuses to state.db TaskStatus values.
        """
        store = self._open_store()
        try:
            raw_tasks = store.list_tasks()
            result: list[BridgeTask] = []
            for t in raw_tasks:
                status = _FORWARD_STATUS_MAP.get(t.status, TaskStatus.BACKLOG)
                result.append(BridgeTask(
                    local_task_id=t.id,
                    title=t.title,
                    description=t.description,
                    status=status,
                    complexity=t.complexity,
                    depends_on=list(t.depends_on),
                ))
            return result
        finally:
            store.close()

    # ------------------------------------------------------------------
    # Reverse sync: state.db -> tasks.db
    # ------------------------------------------------------------------

    def add_task(
        self,
        title: str,
        priority: str = "P2",
        complexity: str = "S",
        description: str = "",
        depends_on: list[str] | None = None,
        task_id: str | None = None,
    ) -> str:
        """Add a task to tasks.db.

        Args:
            title: Task title.
            priority: P0-P3.
            complexity: S, M, or L.
            description: Full description.
            depends_on: List of upstream task IDs.
            task_id: Optional explicit ID. Auto-generated if None.

        Returns:
            The created task ID.
        """
        store = self._open_store()
        try:
            task = store.add(
                title=title,
                priority=priority,
                complexity=complexity,
                description=description,
                depends_on=depends_on,
                task_id=task_id,
            )
            return task.id
        finally:
            store.close()

    def update_task_title(self, local_task_id: str, title: str) -> bool:
        """Update a task's title in tasks.db.

        Args:
            local_task_id: Task ID (e.g. "T-P0-5").
            title: New title string.

        Returns:
            True if updated, False if task not found.
        """
        store = self._open_store()
        try:
            result = store.update(local_task_id, title=title)
            return result is not None
        finally:
            store.close()

    def update_task_status(self, local_task_id: str, status: TaskStatus) -> bool:
        """Update a task's status in tasks.db.

        Maps state.db TaskStatus to tasks.db status string.

        Args:
            local_task_id: Task ID (e.g. "T-P0-5").
            status: TaskStatus from state.db.

        Returns:
            True if updated, False if task not found.
        """
        db_status = _REVERSE_STATUS_MAP.get(status, "active")
        store = self._open_store()
        try:
            result = store.update(local_task_id, status=db_status)
            return result is not None
        finally:
            store.close()

    # ------------------------------------------------------------------
    # ID allocation
    # ------------------------------------------------------------------

    def generate_next_task_id(self, priority: str) -> str:
        """Generate the next sequential task ID for a given priority.

        Args:
            priority: Priority string like "P0", "P1", "P2".

        Returns:
            A task ID string like "T-P0-179".
        """
        store = self._open_store()
        try:
            task_id = store._next_id(priority)  # noqa: SLF001
            # Commit the counter update so subsequent calls see it
            store._get_conn().commit()  # noqa: SLF001
            return task_id
        finally:
            store.close()

    def get_all_task_ids(self) -> set[str]:
        """Get all existing task IDs from tasks.db.

        Returns:
            Set of task ID strings.
        """
        store = self._open_store()
        try:
            tasks = store.list_tasks()
            return {t.id for t in tasks}
        finally:
            store.close()

    # ------------------------------------------------------------------
    # TASKS.md projection
    # ------------------------------------------------------------------

    def reproject(self) -> None:
        """Regenerate TASKS.md from tasks.db state.

        Replicates task_db._write_projection(): removes readonly,
        performs atomic temp-file write, stores hash, restores readonly.
        """
        store = self._open_store()
        try:
            content = store.project()

            # Remove read-only if set
            if self._tasks_md_path.exists():
                _remove_readonly(self._tasks_md_path)

            # Atomic write via temp file
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._tasks_md_path.parent), suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                # Atomic rename (Windows: replace if exists)
                tmp = Path(tmp_path)
                tmp.replace(self._tasks_md_path)
            except Exception:
                # Clean up temp file on failure
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise

            # Store projection hash
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            store.set_projection_hash(h)

            # Restore read-only
            _set_readonly(self._tasks_md_path)
        finally:
            store.close()
