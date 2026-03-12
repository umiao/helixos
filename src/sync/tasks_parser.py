"""Task sync: tasks.db -> state.db via TaskStoreBridge.

Replaces the old regex-based TASKS.md parser with direct SQL-to-SQL sync.
The bridge reads tasks.db (Claude Code source of truth) and upserts into
state.db (server source of truth).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.config import ProjectRegistry
from src.models import Task
from src.sync.task_store_bridge import TaskStoreBridge
from src.task_manager import TaskManager, UpsertResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes (SyncResult kept for route response schemas)
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Result of syncing tasks from tasks.db to the server database."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sync function
# ---------------------------------------------------------------------------


async def sync_project_tasks(
    project_id: str,
    task_manager: TaskManager,
    registry: ProjectRegistry,
) -> SyncResult:
    """Read tasks.db for *project_id* via bridge and upsert into state.db.

    New tasks enter the database with their mapped status preserved.
    Tasks marked done in tasks.db are force-updated to DONE in state.db.
    Tasks removed from tasks.db are left untouched in state.db.
    """
    project = registry.get_project(project_id)

    if project.repo_path is None:
        return SyncResult(
            warnings=[f"Project {project_id}: no repo_path configured"],
        )

    # Check that tasks.db exists
    db_path = project.repo_path / ".claude" / "tasks.db"
    if not db_path.is_file():
        # Fall back to checking TASKS.md for backwards compat warning
        tasks_path = project.repo_path / project.tasks_file
        if not tasks_path.is_file():
            return SyncResult(
                warnings=[f"Neither tasks.db nor TASKS.md found for {project_id}"],
            )
        return SyncResult(
            warnings=[
                f"tasks.db not found at {db_path}; "
                f"TASKS.md exists but direct SQL sync requires tasks.db"
            ],
        )

    try:
        bridge = TaskStoreBridge(project.repo_path)
    except (FileNotFoundError, ImportError) as exc:
        return SyncResult(
            warnings=[f"Failed to load task_store bridge: {exc}"],
        )

    try:
        bridge_tasks = bridge.read_all_tasks()
    except Exception as exc:
        logger.warning("Failed to read tasks.db for %s: %s", project_id, exc)
        return SyncResult(
            warnings=[f"Failed to read tasks.db: {exc}"],
        )
    finally:
        bridge.close()

    result = SyncResult()
    parsed_ids: set[str] = set()

    for bt in bridge_tasks:
        global_id = f"{project_id}:{bt.local_task_id}"
        parsed_ids.add(global_id)
        task = Task(
            id=global_id,
            project_id=project_id,
            local_task_id=bt.local_task_id,
            title=bt.title,
            original_title=bt.title,
            description=bt.description,
            status=bt.status,
            executor_type=project.executor_type,
        )
        # plan_status is not in tasks.db; pass None to preserve DB value
        upsert_result = await task_manager.upsert_task(
            task, plan_status=None,
        )
        if upsert_result == UpsertResult.created:
            result.added += 1
        elif upsert_result == UpsertResult.skipped_deleted:
            result.skipped += 1
        elif upsert_result == UpsertResult.unchanged:
            result.unchanged += 1
        else:
            result.updated += 1

    # Mark tasks removed from tasks.db as sync-deleted
    removed = await task_manager.sync_mark_removed(project_id, parsed_ids)
    if removed:
        logger.info(
            "Sync-deleted %d tasks removed from tasks.db in %s",
            removed, project_id,
        )

    logger.info(
        "Synced %s: +%d ~%d =%d skipped=%d",
        project_id,
        result.added,
        result.updated,
        result.unchanged,
        result.skipped,
    )

    return result
