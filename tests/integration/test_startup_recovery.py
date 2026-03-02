"""Integration test: insert RUNNING tasks -> startup_recovery -> FAILED.

Tests that the scheduler's startup recovery correctly marks orphaned
RUNNING tasks as FAILED and emits alerts.
"""

from __future__ import annotations

import asyncio

import pytest

from src.config import ProjectRegistry
from src.events import EventBus
from src.models import ExecutorType, Task, TaskStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager


@pytest.mark.integration
async def test_startup_recovery_marks_running_as_failed(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    env_loader,
) -> None:
    """RUNNING tasks should become FAILED on startup recovery."""
    config = make_config()
    registry = ProjectRegistry(config)

    # Create tasks in RUNNING status (simulating a crash)
    for i in range(1, 4):
        task = Task(
            id=f"proj_a:T-P0-{i}",
            project_id="proj_a",
            local_task_id=f"T-P0-{i}",
            title=f"Orphaned task {i}",
            status=TaskStatus.QUEUED,
            executor_type=ExecutorType.CODE,
        )
        await task_manager.create_task(task)
        # Transition to RUNNING
        await task_manager.update_status(task.id, TaskStatus.RUNNING)

    # Verify they are RUNNING
    running = await task_manager.list_tasks(status=TaskStatus.RUNNING)
    assert len(running) == 3

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    # Run startup recovery
    recovered = await scheduler.startup_recovery()
    assert recovered == 3

    # All should now be FAILED
    failed = await task_manager.list_tasks(status=TaskStatus.FAILED)
    assert len(failed) == 3

    running_after = await task_manager.list_tasks(status=TaskStatus.RUNNING)
    assert len(running_after) == 0


@pytest.mark.integration
async def test_startup_recovery_emits_alerts(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    env_loader,
) -> None:
    """Startup recovery should emit an alert for each recovered task."""
    config = make_config()
    registry = ProjectRegistry(config)

    # Create 2 orphaned RUNNING tasks
    for i in range(1, 3):
        task = Task(
            id=f"proj_a:T-P0-{i}",
            project_id="proj_a",
            local_task_id=f"T-P0-{i}",
            title=f"Orphaned {i}",
            status=TaskStatus.QUEUED,
            executor_type=ExecutorType.CODE,
        )
        await task_manager.create_task(task)
        await task_manager.update_status(task.id, TaskStatus.RUNNING)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    # Collect events
    alerts: list[dict] = []

    async def _collect() -> None:
        count = 0
        async for event in event_bus.subscribe():
            if event.type == "alert":
                alerts.append({
                    "task_id": event.task_id,
                    "data": event.data,
                })
                count += 1
                if count >= 2:
                    break

    collector = asyncio.create_task(_collect())
    recovered = await scheduler.startup_recovery()
    assert recovered == 2

    # Give collector time to process
    await asyncio.sleep(0.05)
    if not collector.done():
        collector.cancel()

    assert len(alerts) == 2
    alert_task_ids = {a["task_id"] for a in alerts}
    assert "proj_a:T-P0-1" in alert_task_ids
    assert "proj_a:T-P0-2" in alert_task_ids


@pytest.mark.integration
async def test_startup_recovery_no_orphans(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    env_loader,
) -> None:
    """When no RUNNING tasks exist, recovery returns 0."""
    config = make_config()
    registry = ProjectRegistry(config)

    # Create some non-RUNNING tasks
    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Normal task",
        status=TaskStatus.QUEUED,
        executor_type=ExecutorType.CODE,
    )
    await task_manager.create_task(task)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    recovered = await scheduler.startup_recovery()
    assert recovered == 0

    # QUEUED task should still be QUEUED
    t = await task_manager.get_task("proj_a:T-P0-1")
    assert t is not None
    assert t.status == TaskStatus.QUEUED


@pytest.mark.integration
async def test_startup_recovery_sets_error_summary(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    env_loader,
) -> None:
    """Recovered tasks should have error_summary set."""
    config = make_config()
    registry = ProjectRegistry(config)

    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Crashed task",
        status=TaskStatus.QUEUED,
        executor_type=ExecutorType.CODE,
    )
    await task_manager.create_task(task)
    await task_manager.update_status(task.id, TaskStatus.RUNNING)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    await scheduler.startup_recovery()

    recovered = await task_manager.get_task("proj_a:T-P0-1")
    assert recovered is not None
    assert recovered.status == TaskStatus.FAILED
    assert recovered.execution is not None
    assert "crash" in recovered.execution.error_summary.lower()


@pytest.mark.integration
async def test_recovery_then_requeue(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    env_loader,
) -> None:
    """After recovery, tasks can be moved back to QUEUED for retry."""
    config = make_config()
    registry = ProjectRegistry(config)

    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Recoverable task",
        status=TaskStatus.QUEUED,
        executor_type=ExecutorType.CODE,
    )
    await task_manager.create_task(task)
    await task_manager.update_status(task.id, TaskStatus.RUNNING)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    await scheduler.startup_recovery()

    # Now move it back to QUEUED via FAILED -> QUEUED
    recovered = await task_manager.get_task("proj_a:T-P0-1")
    assert recovered is not None
    assert recovered.status == TaskStatus.FAILED

    await task_manager.update_status("proj_a:T-P0-1", TaskStatus.QUEUED)
    final = await task_manager.get_task("proj_a:T-P0-1")
    assert final is not None
    assert final.status == TaskStatus.QUEUED
