"""Tests for plan_status sync semantics between tasks.db and state.db.

Covers:
- upsert_task plan_status=None -> DB wins (AC4, AC7)
- Round-trip: bridge sync -> DB preserves plan_status (AC6)
- Absence: DB=ready, tasks.db has no plan_status -> sync -> DB still ready (AC7)

Note: The old TasksParser/TasksWriter plan_status tests (AC1-AC3) have been
removed because TASKS.md is now auto-generated from tasks.db (which does not
store plan_status). plan_status is a state.db-only concept.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.config import OrchestratorConfig, ProjectConfig, ProjectRegistry
from src.models import ExecutorType, PlanStatus, Task, TaskStatus
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import TaskManager, UpsertResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(
    project_id: str,
    repo_path: Path,
    tasks_file: str = "TASKS.md",
) -> ProjectRegistry:
    """Create a minimal ProjectRegistry for testing."""
    config = OrchestratorConfig(
        projects={
            project_id: ProjectConfig(
                name=project_id,
                repo_path=repo_path,
                executor_type=ExecutorType.CODE,
                tasks_file=tasks_file,
            ),
        },
    )
    return ProjectRegistry(config)


def _setup_repo_with_bridge(tmp_path: Path) -> Path:
    """Create a repo with .claude/hooks/task_store.py for bridge tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    hooks_dir = repo / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)

    # Copy real task_store.py
    real_store = Path(__file__).parent.parent / ".claude" / "hooks" / "task_store.py"
    if not real_store.is_file():
        real_store = (
            Path(__file__).parent.parent.parent
            / "claude-code-project-template"
            / "shared"
            / "hooks"
            / "task_store.py"
        )
    if not real_store.is_file():
        pytest.skip("task_store.py not found for integration test")

    shutil.copy2(str(real_store), str(hooks_dir / "task_store.py"))
    return repo


# ===================================================================
# AC4 + AC7: upsert_task plan_status semantics
# ===================================================================


class TestUpsertPlanStatus:
    """upsert_task respects plan_status=None (DB wins) vs explicit value."""

    @pytest.fixture
    def task_manager(self, session_factory) -> TaskManager:
        """Create a TaskManager with in-memory DB."""
        return TaskManager(session_factory)

    async def test_create_with_plan_status(
        self, task_manager: TaskManager,
    ) -> None:
        """New task with explicit plan_status sets it in DB."""
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task, plan_status="ready")
        assert result == UpsertResult.created

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "ready"

    async def test_create_without_plan_status(
        self, task_manager: TaskManager,
    ) -> None:
        """New task without plan_status gets default 'none'."""
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task)
        assert result == UpsertResult.created

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == PlanStatus.NONE

    async def test_absence_preserves_db_value(
        self, task_manager: TaskManager,
    ) -> None:
        """AC7: DB=ready, plan_status=None on upsert -> DB still ready."""
        # Create task with plan_status=ready
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
            plan_status="ready",
        )
        await task_manager.create_task(task)

        # Upsert with plan_status=None (bridge sync always passes None)
        task2 = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task2, plan_status=None)
        assert result == UpsertResult.unchanged

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "ready"

    async def test_explicit_overwrite(
        self, task_manager: TaskManager,
    ) -> None:
        """Explicit plan_status overwrites DB value."""
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
            plan_status="ready",
        )
        await task_manager.create_task(task)

        task2 = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task2, plan_status="failed")
        assert result == UpsertResult.updated

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "failed"

    async def test_explicit_none_resets(
        self, task_manager: TaskManager,
    ) -> None:
        """Explicit plan_status='none' resets DB to 'none'."""
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
            plan_status="ready",
        )
        await task_manager.create_task(task)

        task2 = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task2, plan_status="none")
        assert result == UpsertResult.updated

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "none"


# ===================================================================
# AC6: Round-trip test via sync_project_tasks (bridge-based)
# ===================================================================


class TestRoundTrip:
    """End-to-end: tasks.db -> bridge -> sync -> state.db."""

    @pytest.fixture
    def task_manager(self, session_factory) -> TaskManager:
        """Create a TaskManager with in-memory DB."""
        return TaskManager(session_factory)

    async def test_round_trip_sync(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """Tasks from tasks.db appear in state.db after sync."""
        repo = _setup_repo_with_bridge(tmp_path)

        # Add a task to tasks.db via the store directly
        from src.sync.task_store_bridge import TaskStoreBridge
        bridge = TaskStoreBridge(repo)
        bridge.add_task(
            title="My task",
            priority="P0",
            description="Test description",
            task_id="T-P0-1",
        )
        bridge.reproject()

        registry = _make_registry("proj", repo)
        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.added == 1

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.title == "My task"
        assert db_task.status == TaskStatus.BACKLOG

    async def test_plan_status_preserved_through_sync(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """AC7: plan_status in state.db is preserved through bridge sync.

        Since tasks.db does not store plan_status, the bridge always passes
        plan_status=None to upsert_task, which preserves the DB value.
        """
        repo = _setup_repo_with_bridge(tmp_path)

        from src.sync.task_store_bridge import TaskStoreBridge
        bridge = TaskStoreBridge(repo)
        bridge.add_task(
            title="My task",
            priority="P0",
            task_id="T-P0-1",
        )
        bridge.reproject()

        registry = _make_registry("proj", repo)

        # First sync creates the task
        await sync_project_tasks("proj", task_manager, registry)

        # Set plan_status=ready in state.db (via proper state machine)
        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        await task_manager.set_plan_state("proj:T-P0-1", "generating")
        await task_manager.set_plan_state(
            "proj:T-P0-1", "ready",
            description="Test plan",
            plan_json='{"plan": "test", "steps": [], "acceptance_criteria": []}',
        )

        # Re-sync: plan_status should be preserved (bridge passes None)
        result2 = await sync_project_tasks("proj", task_manager, registry)
        # May be updated (description changed by set_plan_state) or unchanged
        assert result2.added == 0

        db_task2 = await task_manager.get_task("proj:T-P0-1")
        assert db_task2 is not None
        assert db_task2.plan_status == "ready"  # Preserved through sync
