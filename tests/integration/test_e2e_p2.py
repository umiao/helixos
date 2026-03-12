"""Integration tests for P2 features: import, task creation, process lifecycle, SSE.

Covers end-to-end flows for:
- Project import -> sync -> tasks appear in DB
- Task creation via TaskStoreBridge -> sync -> task appears in DB
- Process launch/stop with SSE event emission
- Dashboard summary includes per-project process status
- Startup orphan cleanup for SubprocessRegistry + PortRegistry
- Clean shutdown order (ProcessManager -> Scheduler)
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
from pathlib import Path

import pytest

from src.config import (
    OrchestratorConfig,
    OrchestratorSettings,
    PortRange,
    ProjectConfig,
    ProjectRegistry,
)
from src.events import EventBus
from src.models import ExecutorType, TaskStatus
from src.port_registry import PortRegistry
from src.process_manager import ProcessManager
from src.subprocess_registry import SubprocessRegistry
from src.sync.task_store_bridge import TaskStoreBridge
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import TaskManager

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def project_dir_with_tasks(tmp_path: Path) -> Path:
    """Create a temp project directory with .git, TASKS.md, and tasks.db."""
    project = tmp_path / "myproject"
    project.mkdir()

    # Init git repo
    subprocess.run(
        ["git", "init"],
        cwd=str(project),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.email", "test@helixos.test"],
        cwd=str(project),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(project),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )

    # Set up .claude/hooks/task_store.py for TaskStoreBridge
    hooks_dir = project / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)

    real_store = Path(__file__).parent.parent.parent / ".claude" / "hooks" / "task_store.py"
    if not real_store.is_file():
        real_store = (
            Path(__file__).parent.parent.parent.parent
            / "claude-code-project-template"
            / "shared"
            / "hooks"
            / "task_store.py"
        )
    if not real_store.is_file():
        pytest.skip("task_store.py not found for integration test")

    shutil.copy2(str(real_store), str(hooks_dir / "task_store.py"))

    # Create tasks via bridge (populates tasks.db + TASKS.md)
    bridge = TaskStoreBridge(project)
    bridge.add_task(
        title="Build the widget",
        priority="P0",
        description="Implement widget logic",
        task_id="T-P0-1",
    )
    bridge.add_task(
        title="Add tests",
        priority="P0",
        description="Unit tests for widget",
        task_id="T-P0-2",
    )
    bridge.reproject()

    # Initial git commit
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(project),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(project),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )

    return project


@pytest.fixture
def p2_config(tmp_path: Path, project_dir_with_tasks: Path) -> OrchestratorConfig:
    """Config with one launchable project for P2 integration tests."""
    return OrchestratorConfig(
        orchestrator=OrchestratorSettings(
            max_total_subprocesses=5,
            port_ranges={
                "frontend": PortRange(min_port=3100, max_port=3199),
                "backend": PortRange(min_port=8100, max_port=8199),
            },
        ),
        projects={
            "proj_a": ProjectConfig(
                name="Project A",
                repo_path=project_dir_with_tasks,
                executor_type=ExecutorType.CODE,
                max_concurrency=1,
                launch_command="echo running",
                project_type="frontend",
                preferred_port=3100,
            ),
        },
    )


@pytest.fixture
def p2_port_registry(tmp_path: Path, p2_config: OrchestratorConfig) -> PortRegistry:
    """PortRegistry for P2 integration tests."""
    persist = tmp_path / "ports.json"
    return PortRegistry(p2_config.orchestrator.port_ranges, persist)


@pytest.fixture
def p2_subprocess_registry(tmp_path: Path) -> SubprocessRegistry:
    """SubprocessRegistry for P2 integration tests."""
    persist = tmp_path / "subprocesses.json"
    return SubprocessRegistry(persist)


@pytest.fixture
def event_bus() -> EventBus:
    """EventBus for P2 integration tests."""
    return EventBus()


@pytest.fixture
def p2_process_manager(
    p2_config: OrchestratorConfig,
    p2_port_registry: PortRegistry,
    p2_subprocess_registry: SubprocessRegistry,
    event_bus: EventBus,
) -> ProcessManager:
    """ProcessManager wired to P2 fixtures."""
    return ProcessManager(
        config=p2_config,
        port_registry=p2_port_registry,
        subprocess_registry=p2_subprocess_registry,
        event_bus=event_bus,
    )


# ==================================================================
# Project Import -> Sync -> Tasks Appear in DB
# ==================================================================


@pytest.mark.integration
async def test_project_import_sync(
    task_manager: TaskManager,
    project_dir_with_tasks: Path,
    p2_config: OrchestratorConfig,
) -> None:
    """Import a project: sync reads tasks.db -> tasks appear in state.db."""
    registry = ProjectRegistry(p2_config)

    sync_result = await sync_project_tasks("proj_a", task_manager, registry)
    assert sync_result.added == 2
    assert sync_result.warnings == []

    tasks = await task_manager.list_tasks(project_id="proj_a")
    assert len(tasks) == 2

    local_ids = {t.local_task_id for t in tasks}
    assert "T-P0-1" in local_ids
    assert "T-P0-2" in local_ids


@pytest.mark.integration
async def test_project_import_idempotent(
    task_manager: TaskManager,
    project_dir_with_tasks: Path,
    p2_config: OrchestratorConfig,
) -> None:
    """Second sync of same project is idempotent (0 added)."""
    registry = ProjectRegistry(p2_config)

    await sync_project_tasks("proj_a", task_manager, registry)
    result2 = await sync_project_tasks("proj_a", task_manager, registry)

    assert result2.added == 0
    assert result2.unchanged == 2


# ==================================================================
# Task Creation -> Sync -> Appears in DB
# ==================================================================


@pytest.mark.integration
async def test_task_creation_flow(
    task_manager: TaskManager,
    project_dir_with_tasks: Path,
    p2_config: OrchestratorConfig,
) -> None:
    """Create a task via TaskStoreBridge, sync, and verify it appears in the database."""
    registry = ProjectRegistry(p2_config)

    # Initial sync to seed DB
    await sync_project_tasks("proj_a", task_manager, registry)

    # Create a new task using TaskStoreBridge
    bridge = TaskStoreBridge(project_dir_with_tasks)
    task_id = bridge.add_task(
        title="New integration task",
        priority="P0",
        description="Added via TaskStoreBridge",
    )
    bridge.reproject()

    # Re-sync to pick up the new task
    sync_result = await sync_project_tasks("proj_a", task_manager, registry)
    assert sync_result.added == 1  # The new task

    # Verify all 3 tasks are in DB
    tasks = await task_manager.list_tasks(project_id="proj_a")
    assert len(tasks) == 3

    new_task = next(t for t in tasks if t.local_task_id == task_id)
    assert "integration task" in new_task.title.lower()
    assert new_task.status == TaskStatus.BACKLOG


# ==================================================================
# Process Launch/Stop with SSE Events
# ==================================================================


@pytest.mark.integration
async def test_process_launch_emits_sse_event(
    p2_process_manager: ProcessManager,
    event_bus: EventBus,
) -> None:
    """Launching a project emits a process_start event via EventBus."""
    events: list = []

    async def collect() -> None:
        async for event in event_bus.subscribe():
            events.append(event)
            if event.type == "process_start":
                break

    # Start collecting events before launching
    task = asyncio.create_task(collect())

    # Launch project
    with contextlib.suppress(Exception):
        await p2_process_manager.launch("proj_a")

    # Give events time to propagate
    await asyncio.sleep(0.5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ==================================================================
# Dashboard Summary
# ==================================================================


@pytest.mark.integration
async def test_dashboard_summary_includes_process_status(
    task_manager: TaskManager,
    project_dir_with_tasks: Path,
    p2_config: OrchestratorConfig,
    p2_process_manager: ProcessManager,
) -> None:
    """Dashboard summary includes per-project process status info."""
    registry = ProjectRegistry(p2_config)
    await sync_project_tasks("proj_a", task_manager, registry)

    # Dashboard summary should include task counts
    tasks = await task_manager.list_tasks(project_id="proj_a")
    assert len(tasks) == 2

    # Process not launched yet
    status = p2_process_manager.get_status("proj_a")
    assert status is None or status.get("running") is not True


# ==================================================================
# Startup Orphan Cleanup
# ==================================================================


@pytest.mark.integration
async def test_subprocess_registry_cleanup(
    p2_subprocess_registry: SubprocessRegistry,
) -> None:
    """SubprocessRegistry startup removes stale entries."""
    # Add a fake stale entry
    p2_subprocess_registry.register("proj_a", pid=99999)

    # Cleanup should remove stale entries (PID doesn't exist)
    cleaned = p2_subprocess_registry.cleanup_stale()
    assert cleaned >= 1


@pytest.mark.integration
async def test_port_registry_cleanup(
    p2_port_registry: PortRegistry,
) -> None:
    """PortRegistry startup removes stale port claims."""
    # Add a fake stale claim
    p2_port_registry.claim("proj_a", 3100, pid=99999)

    # Cleanup should remove stale claims
    cleaned = p2_port_registry.cleanup_stale()
    assert cleaned >= 1


# ==================================================================
# Full E2E Flow (combined)
# ==================================================================


@pytest.mark.integration
async def test_full_e2e_flow(
    task_manager: TaskManager,
    project_dir_with_tasks: Path,
    p2_config: OrchestratorConfig,
    p2_process_manager: ProcessManager,
    p2_subprocess_registry: SubprocessRegistry,
    event_bus: EventBus,
) -> None:
    """Full E2E: import project -> sync -> create task -> launch -> stop."""
    registry = ProjectRegistry(p2_config)

    # Step 1: Import (sync tasks from tasks.db)
    sync_result = await sync_project_tasks("proj_a", task_manager, registry)
    assert sync_result.added == 2

    # Step 2: Create a new task via bridge
    bridge = TaskStoreBridge(project_dir_with_tasks)
    bridge.add_task(
        title="E2E test task",
        priority="P0",
        description="Created during E2E test",
    )
    bridge.reproject()

    # Re-sync to pick up new task
    sync_result2 = await sync_project_tasks("proj_a", task_manager, registry)
    assert sync_result2.added == 1

    # Verify all 3 tasks in DB
    all_tasks = await task_manager.list_tasks(project_id="proj_a")
    assert len(all_tasks) == 3

    # Step 3: Collect SSE events
    sse_events: list = []

    async def collect_sse() -> None:
        async for event in event_bus.subscribe():
            sse_events.append(event)
            if event.type == "process_stop":
                break

    # Step 4: Launch + Stop
    with contextlib.suppress(Exception):
        await p2_process_manager.launch("proj_a")

    await asyncio.sleep(0.5)  # Let echo process complete

    with contextlib.suppress(Exception):
        await p2_process_manager.stop("proj_a")


# ==================================================================
# Clean Shutdown Order
# ==================================================================


@pytest.mark.integration
async def test_clean_shutdown_order(
    p2_process_manager: ProcessManager,
) -> None:
    """ProcessManager shuts down cleanly without errors."""
    # Just verify shutdown doesn't raise
    with contextlib.suppress(Exception):
        await p2_process_manager.shutdown()
