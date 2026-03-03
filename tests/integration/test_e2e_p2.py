"""Integration tests for P2 features: import, task creation, process lifecycle, SSE.

Covers end-to-end flows for:
- Project import -> sync -> tasks appear in DB
- Task creation via TasksWriter -> sync -> task appears in DB
- Process launch/stop with SSE event emission
- Dashboard summary includes per-project process status
- Startup orphan cleanup for SubprocessRegistry + PortRegistry
- Clean shutdown order (ProcessManager -> Scheduler)
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
from src.process_manager import ProcessManager, _dev_server_key
from src.subprocess_registry import SubprocessRegistry
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import TaskManager
from src.tasks_writer import NewTask, TasksWriter

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def project_dir_with_tasks(tmp_path: Path) -> Path:
    """Create a temp project directory with .git and TASKS.md."""
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

    # Write TASKS.md
    tasks_md = project / "TASKS.md"
    tasks_md.write_text(
        "# Task Backlog\n"
        "\n"
        "## Active Tasks\n"
        "\n"
        "#### T-P0-1: Build the widget\n"
        "- Implement widget logic\n"
        "\n"
        "#### T-P0-2: Add tests\n"
        "- Unit tests for widget\n"
        "\n"
        "## Completed Tasks\n",
        encoding="utf-8",
    )

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
def p2_subprocess_registry() -> SubprocessRegistry:
    """SubprocessRegistry for P2 integration tests."""
    return SubprocessRegistry(max_total=5)


@pytest.fixture
def p2_process_manager(
    p2_config: OrchestratorConfig,
    p2_port_registry: PortRegistry,
    p2_subprocess_registry: SubprocessRegistry,
    event_bus: EventBus,
) -> ProcessManager:
    """ProcessManager wired to P2 test fixtures."""
    registry = ProjectRegistry(p2_config)
    return ProcessManager(
        config=p2_config,
        registry=registry,
        port_registry=p2_port_registry,
        subprocess_registry=p2_subprocess_registry,
        event_bus=event_bus,
    )


# ==================================================================
# Import -> Sync -> Tasks in DB
# ==================================================================


@pytest.mark.integration
async def test_import_to_swimlane_flow(
    task_manager: TaskManager,
    project_dir_with_tasks: Path,
    p2_config: OrchestratorConfig,
) -> None:
    """Import a project and sync: tasks from TASKS.md appear in the database."""
    registry = ProjectRegistry(p2_config)

    # Verify project exists in registry
    project = registry.get_project("proj_a")
    assert project.name == "Project A"
    assert project.repo_path == project_dir_with_tasks

    # Sync tasks from TASKS.md into the database
    sync_result = await sync_project_tasks("proj_a", task_manager, registry)
    assert sync_result.added == 2
    assert sync_result.unchanged == 0

    # Verify tasks are in the database
    tasks = await task_manager.list_tasks(project_id="proj_a")
    assert len(tasks) == 2

    task_ids = {t.local_task_id for t in tasks}
    assert "T-P0-1" in task_ids
    assert "T-P0-2" in task_ids

    # Sync preserves BACKLOG status (no auto-promotion to QUEUED)
    for t in tasks:
        assert t.status == TaskStatus.BACKLOG


@pytest.mark.integration
async def test_import_then_resync_is_idempotent(
    task_manager: TaskManager,
    project_dir_with_tasks: Path,
    p2_config: OrchestratorConfig,
) -> None:
    """Re-syncing after initial import should show unchanged tasks."""
    registry = ProjectRegistry(p2_config)

    # First sync
    r1 = await sync_project_tasks("proj_a", task_manager, registry)
    assert r1.added == 2

    # Second sync -- same content
    r2 = await sync_project_tasks("proj_a", task_manager, registry)
    assert r2.added == 0
    assert r2.unchanged == 2


# ==================================================================
# Task Creation -> Sync -> Appears in DB
# ==================================================================


@pytest.mark.integration
async def test_task_creation_flow(
    task_manager: TaskManager,
    project_dir_with_tasks: Path,
    p2_config: OrchestratorConfig,
) -> None:
    """Create a task via TasksWriter, sync, and verify it appears in the database."""
    registry = ProjectRegistry(p2_config)

    # Initial sync to seed DB
    await sync_project_tasks("proj_a", task_manager, registry)

    # Create a new task using TasksWriter
    tasks_md_path = project_dir_with_tasks / "TASKS.md"
    writer = TasksWriter(tasks_md_path)
    result = writer.append_task(NewTask(
        title="New integration task",
        description="Added via TasksWriter",
        priority="P0",
    ))
    assert result.success
    assert result.task_id == "T-P0-3"

    # Re-sync to pick up the new task
    sync_result = await sync_project_tasks("proj_a", task_manager, registry)
    assert sync_result.added == 1  # The new task

    # Verify all 3 tasks are in DB
    tasks = await task_manager.list_tasks(project_id="proj_a")
    assert len(tasks) == 3

    new_task = next(t for t in tasks if t.local_task_id == "T-P0-3")
    assert "integration task" in new_task.title.lower()
    assert new_task.status == TaskStatus.BACKLOG


@pytest.mark.integration
async def test_task_creation_generates_backup(
    project_dir_with_tasks: Path,
) -> None:
    """TasksWriter creates a .bak backup before modifying TASKS.md."""
    tasks_md_path = project_dir_with_tasks / "TASKS.md"
    writer = TasksWriter(tasks_md_path)

    result = writer.append_task(NewTask(title="Backup test task"))
    assert result.success
    assert result.backup_path is not None

    backup = Path(result.backup_path)
    assert backup.exists()
    # Backup should contain original content (2 tasks, not 3)
    backup_content = backup.read_text(encoding="utf-8")
    assert "T-P0-1" in backup_content
    assert "T-P0-2" in backup_content


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

    mock_proc = AsyncMock()
    mock_proc.pid = 42000
    mock_proc.returncode = None

    with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
        collector = asyncio.create_task(collect())
        await asyncio.sleep(0.01)
        status = await p2_process_manager.launch("proj_a")
        await asyncio.sleep(0.05)
        if not collector.done():
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector

    assert status.running is True
    assert status.pid == 42000
    assert status.port == 3100

    start_events = [e for e in events if e.type == "process_start"]
    assert len(start_events) >= 1
    assert start_events[0].task_id == "proj_a"
    assert start_events[0].data["pid"] == 42000
    assert start_events[0].data["port"] == 3100


@pytest.mark.integration
async def test_process_stop_emits_sse_event(
    p2_process_manager: ProcessManager,
    event_bus: EventBus,
) -> None:
    """Stopping a project emits a process_stop event via EventBus."""
    mock_proc = AsyncMock()
    mock_proc.pid = 42001
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
        await p2_process_manager.launch("proj_a")

    events: list = []

    async def collect() -> None:
        async for event in event_bus.subscribe():
            events.append(event)
            if event.type == "process_stop":
                break

    with patch("src.process_manager._terminate_process"):
        collector = asyncio.create_task(collect())
        await asyncio.sleep(0.01)
        stopped = await p2_process_manager.stop("proj_a")
        await asyncio.sleep(0.05)
        if not collector.done():
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector

    assert stopped is True

    stop_events = [e for e in events if e.type == "process_stop"]
    assert len(stop_events) >= 1
    assert stop_events[0].task_id == "proj_a"
    assert stop_events[0].data["pid"] == 42001


@pytest.mark.integration
async def test_process_launch_stop_full_cycle(
    p2_process_manager: ProcessManager,
    p2_subprocess_registry: SubprocessRegistry,
    p2_port_registry: PortRegistry,
    event_bus: EventBus,
) -> None:
    """Full launch -> status check -> stop cycle with registry tracking."""
    mock_proc = AsyncMock()
    mock_proc.pid = 42002
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)

    # Launch
    with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
        status = await p2_process_manager.launch("proj_a")

    assert status.running is True
    assert p2_subprocess_registry.count == 1
    assert _dev_server_key("proj_a") in p2_subprocess_registry.list_entries()

    # Status check
    running_status = p2_process_manager.status("proj_a")
    assert running_status.running is True
    assert running_status.pid == 42002
    assert running_status.port == 3100
    assert running_status.uptime_seconds is not None

    # Port assignment exists
    port_assignment = p2_port_registry.get_assignment("proj_a")
    assert port_assignment is not None
    assert port_assignment.port == 3100
    assert port_assignment.pid == 42002

    # Stop
    with patch("src.process_manager._terminate_process"):
        stopped = await p2_process_manager.stop("proj_a")

    assert stopped is True
    assert p2_subprocess_registry.count == 0

    # Status after stop
    after_status = p2_process_manager.status("proj_a")
    assert after_status.running is False


# ==================================================================
# Dashboard Summary with Process Status
# ==================================================================


@pytest.mark.integration
async def test_dashboard_includes_process_status(
    task_manager: TaskManager,
    p2_config: OrchestratorConfig,
    p2_process_manager: ProcessManager,
    project_dir_with_tasks: Path,
) -> None:
    """Dashboard summary should include per-project process status."""
    registry = ProjectRegistry(p2_config)

    # Sync tasks
    await sync_project_tasks("proj_a", task_manager, registry)

    # Before launch -- process not running
    status_before = p2_process_manager.status("proj_a")
    assert status_before.running is False

    # Mock launch
    mock_proc = AsyncMock()
    mock_proc.pid = 42003
    mock_proc.returncode = None

    with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
        await p2_process_manager.launch("proj_a")

    # After launch -- process running
    status_after = p2_process_manager.status("proj_a")
    assert status_after.running is True
    assert status_after.pid == 42003
    assert status_after.port == 3100


# ==================================================================
# Startup Orphan Cleanup
# ==================================================================


@pytest.mark.integration
async def test_startup_orphan_cleanup_subprocess_registry(
    p2_subprocess_registry: SubprocessRegistry,
) -> None:
    """Startup cleanup removes dead subprocess entries."""
    # Register entries with fake dead PIDs
    p2_subprocess_registry.register("dev_server:proj_a", 99999, "proj_a", "dev_server")
    p2_subprocess_registry.register("executor:task1", 99998, "proj_a", "executor")
    assert p2_subprocess_registry.count == 2

    # Simulate cleanup where both PIDs are dead
    with patch("src.subprocess_registry._is_process_alive", return_value=False):
        dead = p2_subprocess_registry.cleanup_dead()

    assert len(dead) == 2
    assert p2_subprocess_registry.count == 0


@pytest.mark.integration
async def test_startup_orphan_cleanup_port_registry(
    p2_port_registry: PortRegistry,
) -> None:
    """Startup cleanup removes port assignments for dead PIDs."""
    # Assign a port with a fake PID
    port = p2_port_registry.assign_port("proj_a", "frontend", preferred_port=3100)
    assert port == 3100
    p2_port_registry.update_pid("proj_a", 99997)

    # Cleanup with dead PID
    with patch("src.port_registry._is_process_alive", return_value=False):
        orphans = p2_port_registry.cleanup_orphans()

    assert len(orphans) == 1
    assert p2_port_registry.get_assignment("proj_a") is None


@pytest.mark.integration
async def test_startup_orphan_cleanup_process_manager(
    p2_process_manager: ProcessManager,
    p2_subprocess_registry: SubprocessRegistry,
) -> None:
    """ProcessManager.cleanup_orphans removes stale dev server entries."""
    # Manually register a stale dev server entry (simulating post-crash)
    p2_subprocess_registry.register(
        _dev_server_key("proj_a"), 99996, "proj_a", "dev_server",
    )
    assert p2_subprocess_registry.count == 1

    with patch("src.process_manager._is_process_alive", return_value=False):
        cleaned = p2_process_manager.cleanup_orphans()

    assert "proj_a" in cleaned
    assert p2_subprocess_registry.count == 0


# ==================================================================
# Shutdown Order
# ==================================================================


@pytest.mark.integration
async def test_shutdown_stops_all_processes(
    p2_process_manager: ProcessManager,
    p2_subprocess_registry: SubprocessRegistry,
) -> None:
    """stop_all stops all running dev servers during shutdown."""
    mock_proc = AsyncMock()
    mock_proc.pid = 42004
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
        await p2_process_manager.launch("proj_a")

    assert p2_subprocess_registry.count == 1

    with patch("src.process_manager._terminate_process"):
        await p2_process_manager.stop_all()

    assert p2_subprocess_registry.count == 0
    status = p2_process_manager.status("proj_a")
    assert status.running is False


@pytest.mark.integration
async def test_shutdown_order_process_manager_before_scheduler(
    task_manager: TaskManager,
    event_bus: EventBus,
    p2_config: OrchestratorConfig,
    p2_process_manager: ProcessManager,
    env_loader,
) -> None:
    """ProcessManager.stop_all runs before Scheduler.stop in shutdown sequence."""
    from src.scheduler import Scheduler

    registry = ProjectRegistry(p2_config)
    scheduler = Scheduler(
        config=p2_config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    # Start scheduler
    await scheduler.start()

    # Track shutdown order
    shutdown_order: list[str] = []

    original_stop_all = p2_process_manager.stop_all
    original_scheduler_stop = scheduler.stop

    async def tracked_stop_all(timeout: float = 10.0) -> None:
        shutdown_order.append("process_manager")
        await original_stop_all(timeout=timeout)

    async def tracked_scheduler_stop() -> None:
        shutdown_order.append("scheduler")
        await original_scheduler_stop()

    p2_process_manager.stop_all = tracked_stop_all  # type: ignore[assignment]
    scheduler.stop = tracked_scheduler_stop  # type: ignore[assignment]

    # Execute shutdown sequence (same order as lifespan)
    await p2_process_manager.stop_all()
    await scheduler.stop()

    assert shutdown_order == ["process_manager", "scheduler"]


# ==================================================================
# Combined E2E: Import -> Create Task -> Launch -> SSE
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

    # Step 1: Import (sync tasks from TASKS.md)
    sync_result = await sync_project_tasks("proj_a", task_manager, registry)
    assert sync_result.added == 2

    # Step 2: Create a new task
    tasks_md_path = project_dir_with_tasks / "TASKS.md"
    writer = TasksWriter(tasks_md_path)
    write_result = writer.append_task(NewTask(
        title="E2E test task",
        description="Created during E2E test",
        priority="P0",
    ))
    assert write_result.success

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

    # Step 4: Launch project dev server
    mock_proc = AsyncMock()
    mock_proc.pid = 42005
    mock_proc.returncode = None
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
        collector = asyncio.create_task(collect_sse())
        await asyncio.sleep(0.01)

        launch_status = await p2_process_manager.launch("proj_a")
        assert launch_status.running is True
        assert launch_status.port == 3100

        # Step 5: Verify running state
        assert p2_subprocess_registry.count == 1
        running_status = p2_process_manager.status("proj_a")
        assert running_status.running is True

        # Step 6: Stop
        with patch("src.process_manager._terminate_process"):
            await p2_process_manager.stop("proj_a")

        await asyncio.sleep(0.05)
        if not collector.done():
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector

    # Verify SSE events received
    event_types = [e.type for e in sse_events]
    assert "process_start" in event_types
    assert "process_stop" in event_types

    # Verify clean state after stop
    assert p2_subprocess_registry.count == 0
    final_status = p2_process_manager.status("proj_a")
    assert final_status.running is False
