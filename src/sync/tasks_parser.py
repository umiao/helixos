"""TASKS.md parser and project sync for HelixOS orchestrator.

Provides one-way sync from TASKS.md files into the task database.
Tasks are extracted from markdown section headers and sub-headings
matching the ``T-P\\d+-\\d+`` convention.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.config import ProjectRegistry
from src.models import Task, TaskStatus
from src.task_manager import TaskManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Task ID pattern: T-P{priority}-{number}
TASK_ID_RE = re.compile(r"(T-P\d+-\d+)")

# Section header: ## level only (sets status context)
SECTION_RE = re.compile(r"^##\s+(.*)")

# ---------------------------------------------------------------------------
# Default section-to-status mapping
# ---------------------------------------------------------------------------

DEFAULT_STATUS_SECTIONS: dict[str, TaskStatus] = {
    "In Progress": TaskStatus.RUNNING,
    "Active Tasks": TaskStatus.BACKLOG,
    "Active": TaskStatus.BACKLOG,
    "Completed Tasks": TaskStatus.DONE,
    "Completed": TaskStatus.DONE,
    "Done": TaskStatus.DONE,
    "Blocked": TaskStatus.BLOCKED,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ParsedTask:
    """A task extracted from TASKS.md."""

    local_task_id: str
    title: str
    status: TaskStatus
    description: str


@dataclass
class SyncResult:
    """Result of syncing tasks from TASKS.md to the database."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TasksParser:
    """Parse TASKS.md markdown into structured task objects.

    Section headers (``##``) determine the status context for tasks found
    beneath them.  Individual tasks are identified by ``####``-level (or
    deeper) headings that contain a ``T-P\\d+-\\d+`` identifier.
    """

    def __init__(
        self,
        status_sections: dict[str, TaskStatus] | None = None,
    ) -> None:
        """Initialize with optional custom status-section mapping.

        Args:
            status_sections: Map of section header text to TaskStatus.
                Falls back to DEFAULT_STATUS_SECTIONS when *None*.
        """
        self.status_sections = (
            status_sections if status_sections is not None else dict(DEFAULT_STATUS_SECTIONS)
        )
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        """Warnings accumulated during the last ``parse`` call."""
        return list(self._warnings)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, content: str, project_id: str) -> list[ParsedTask]:
        """Parse TASKS.md *content* and return extracted tasks.

        Args:
            content: Raw markdown text of a TASKS.md file.
            project_id: Project identifier (used only for logging).

        Returns:
            Ordered list of ``ParsedTask`` objects.  When duplicate IDs
            are encountered the **last** occurrence wins.
        """
        self._warnings = []
        lines = content.split("\n")

        current_status: TaskStatus | None = None
        tasks: dict[str, ParsedTask] = {}

        # Accumulator for the task currently being built
        cur_id: str | None = None
        cur_title: str = ""
        cur_status: TaskStatus = TaskStatus.BACKLOG
        cur_desc: list[str] = []

        def _flush() -> None:
            """Persist the accumulated task (if any) into *tasks*."""
            nonlocal cur_id, cur_desc
            if cur_id is None:
                return
            desc = "\n".join(cur_desc).strip()
            # Strip trailing horizontal rules
            desc = re.sub(r"\n---\s*$", "", desc).strip()
            if cur_id in tasks:
                self._warnings.append(
                    f"Duplicate task ID: {cur_id}, keeping last occurrence"
                )
            tasks[cur_id] = ParsedTask(
                local_task_id=cur_id,
                title=cur_title,
                status=cur_status,
                description=desc,
            )
            cur_id = None
            cur_desc = []

        for line in lines:
            stripped = line.strip()

            # -- Heading detection --
            heading_match = re.match(r"^(#{1,6})\s+(.*)", stripped)
            if heading_match:
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2).strip()

                if level <= 2:
                    # ## section header -> update status context
                    _flush()
                    current_status = self._match_section(heading_text)
                    continue

                if level == 3:
                    # ### subsection (e.g. "P0 -- Must Have") -> flush only
                    _flush()
                    continue

                # level 4+: potential task heading
                tid_match = TASK_ID_RE.search(heading_text)
                if tid_match:
                    if current_status is None:
                        self._warnings.append(
                            f"Task {tid_match.group(1)} found outside "
                            f"status section, skipped"
                        )
                        continue
                    _flush()
                    cur_id = tid_match.group(1)
                    cur_title = _extract_title(heading_text, tid_match)
                    cur_status = current_status
                    cur_desc = []
                else:
                    # Heading without task ID in a status section -> warn
                    if current_status is not None:
                        _flush()
                        self._warnings.append(
                            f"Heading without task ID: {heading_text!r}, "
                            f"skipped"
                        )
                continue

            # -- Regular content line -> accumulate description --
            if cur_id is not None:
                cur_desc.append(line)

        _flush()
        return list(tasks.values())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _match_section(self, header_text: str) -> TaskStatus | None:
        """Match *header_text* against the configured status-section mapping.

        Uses case-insensitive substring matching so that
        ``"Completed Tasks"`` matches key ``"Completed"``.
        """
        normalized = header_text.strip().lower()
        for section_name, status in self.status_sections.items():
            if section_name.lower() in normalized:
                return status
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_title(text: str, match: re.Match[str]) -> str:
    """Extract the task title from *text* after the matched task ID.

    Strips leading separators (``:*]``) and trailing date suffixes.
    """
    after_id = text[match.end() :]
    # Remove leading punctuation/whitespace
    title = re.sub(r"^[:\s*\]]+", "", after_id)
    # Remove trailing "-- YYYY-MM-DD ..." date suffix
    title = re.sub(r"\s+--\s+\d{4}-\d{2}-\d{2}.*$", "", title)
    return title.strip() or match.group(1)


# ---------------------------------------------------------------------------
# Sync function
# ---------------------------------------------------------------------------


async def sync_project_tasks(
    project_id: str,
    task_manager: TaskManager,
    registry: ProjectRegistry,
) -> SyncResult:
    """Read TASKS.md for *project_id*, parse it, and upsert into the DB.

    New tasks enter the database with their parsed status preserved
    (typically BACKLOG for ``Active Tasks``).  The review gate decides
    whether a task may advance to QUEUED.

    Tasks marked done in TASKS.md are force-updated to ``DONE`` in the DB.
    Tasks removed from TASKS.md are left untouched in the DB.
    """
    project = registry.get_project(project_id)

    if project.repo_path is None:
        return SyncResult(
            warnings=[f"Project {project_id}: no repo_path configured"],
        )

    tasks_path = project.repo_path / project.tasks_file
    if not tasks_path.is_file():
        return SyncResult(
            warnings=[f"TASKS.md not found: {tasks_path}"],
        )

    content = tasks_path.read_text(encoding="utf-8")

    # Build parser with optional custom section mapping from config
    project_config = registry.get_project_config(project_id)
    status_sections: dict[str, TaskStatus] | None = None
    if project_config.status_sections is not None:
        status_sections = {
            k: TaskStatus(v) for k, v in project_config.status_sections.items()
        }

    parser = TasksParser(status_sections=status_sections)
    parsed_tasks = parser.parse(content, project_id)

    result = SyncResult(warnings=parser.warnings[:])

    # Fetch existing tasks for comparison
    existing_tasks = await task_manager.list_tasks(project_id=project_id)
    existing_map = {t.local_task_id: t for t in existing_tasks}

    now = datetime.now(UTC)

    for pt in parsed_tasks:
        global_id = f"{project_id}:{pt.local_task_id}"

        if pt.local_task_id in existing_map:
            existing = existing_map[pt.local_task_id]
            updates: dict[str, object] = {}

            if pt.title != existing.title:
                updates["title"] = pt.title

            if pt.description != existing.description:
                updates["description"] = pt.description

            # Force DONE transition when TASKS.md says completed
            if pt.status == TaskStatus.DONE and existing.status != TaskStatus.DONE:
                updates["status"] = TaskStatus.DONE

            if updates:
                updates["updated_at"] = now
                updated = existing.model_copy(update=updates)
                await task_manager.update_task(updated)
                result.updated += 1
            else:
                result.unchanged += 1
        else:
            # New task -- keep parsed status (no auto-promotion to QUEUED)
            task = Task(
                id=global_id,
                project_id=project_id,
                local_task_id=pt.local_task_id,
                title=pt.title,
                description=pt.description,
                status=pt.status,
                executor_type=project.executor_type,
            )
            await task_manager.create_task(task)
            result.added += 1

    logger.info(
        "Synced %s: +%d ~%d =%d",
        project_id,
        result.added,
        result.updated,
        result.unchanged,
    )

    return result
