"""Integration test: fail -> retry with backoff -> BLOCKED after max retries.

Tests the scheduler retry logic with a mock executor that always fails,
mocking asyncio.sleep to avoid real delays.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.config import ProjectRegistry
from src.events import EventBus
from src.executors.base import ExecutorResult
from src.models import ExecutorType, Task, TaskStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager

from .conftest import MockExecutor


@pytest.mark.integration
async def test_failure_then_retry_success(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    env_loader,
) -> None:
    """Task fails once, then succeeds on retry -> DONE."""
    config = make_config(auto_commit=False)
    registry = ProjectRegistry(config)

    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Flaky task",
        description="Sometimes fails",
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

    # First call fails, second succeeds
    mock_exec = MockExecutor(results=[
        ExecutorResult(
            success=False, exit_code=1,
            error_summary="Build failed", duration_seconds=0.1,
        ),
        ExecutorResult(
            success=True, exit_code=0, duration_seconds=0.1,
        ),
    ])

    sleep_calls: list[float] = []

    async def mock_sleep(delay: float) -> None:
        """Track sleep calls but don't actually wait."""
        sleep_calls.append(delay)

    with (
        patch.object(scheduler, "_get_executor", return_value=mock_exec),
        patch("src.scheduler.asyncio.sleep", side_effect=mock_sleep),
    ):
        await scheduler.tick()

        task_ids = list(scheduler.running.keys())
        assert len(task_ids) == 1
        await scheduler.running[task_ids[0]]

    # Task should be DONE (succeeded on retry)
    final = await task_manager.get_task(task.id)
    assert final is not None
    assert final.status == TaskStatus.DONE

    # Should have slept once (30s backoff for first retry)
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 30


@pytest.mark.integration
async def test_max_retries_exhausted_becomes_blocked(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    env_loader,
) -> None:
    """Task fails all retries -> FAILED -> BLOCKED."""
    config = make_config(auto_commit=False)
    registry = ProjectRegistry(config)

    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Always failing task",
        description="Will never succeed",
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

    # Always fails
    mock_exec = MockExecutor(results=[
        ExecutorResult(
            success=False, exit_code=1,
            error_summary="Fatal error", duration_seconds=0.1,
        ),
    ])

    sleep_calls: list[float] = []

    async def mock_sleep(delay: float) -> None:
        """Track sleep calls."""
        sleep_calls.append(delay)

    # Collect events
    collected_events: list[dict] = []

    async def _collect() -> None:
        async for event in event_bus.subscribe():
            collected_events.append({
                "type": event.type,
                "task_id": event.task_id,
                "data": event.data,
            })
            # Stop after we see "blocked" status
            if (
                event.type == "status_change"
                and isinstance(event.data, dict)
                and event.data.get("status") == "blocked"
            ):
                break

    real_sleep = asyncio.sleep

    with (
        patch.object(scheduler, "_get_executor", return_value=mock_exec),
        patch("src.scheduler.asyncio.sleep", side_effect=mock_sleep),
    ):
        collector = asyncio.create_task(_collect())
        await scheduler.tick()

        task_ids = list(scheduler.running.keys())
        assert len(task_ids) == 1
        await scheduler.running[task_ids[0]]

        # Give collector time (use real sleep, not the mocked one)
        await real_sleep(0.05)
        if not collector.done():
            collector.cancel()

    # Task should be BLOCKED
    final = await task_manager.get_task(task.id)
    assert final is not None
    assert final.status == TaskStatus.BLOCKED

    # Should have slept 3 times (30, 60, 120)
    assert sleep_calls == [30, 60, 120]

    # Events should include alert with error and status_change to blocked
    alert_events = [e for e in collected_events if e["type"] == "alert"]
    assert len(alert_events) >= 1


@pytest.mark.integration
async def test_retry_emits_log_events(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    env_loader,
) -> None:
    """Each retry attempt should emit a log event."""
    config = make_config(auto_commit=False)
    registry = ProjectRegistry(config)

    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Retry logging test",
        description="Test retry logs",
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

    # Fails twice, succeeds on third (second retry)
    mock_exec = MockExecutor(results=[
        ExecutorResult(
            success=False, exit_code=1,
            error_summary="Error 1", duration_seconds=0.1,
        ),
        ExecutorResult(
            success=False, exit_code=1,
            error_summary="Error 2", duration_seconds=0.1,
        ),
        ExecutorResult(
            success=True, exit_code=0, duration_seconds=0.1,
        ),
    ])

    log_events: list[str] = []

    async def _collect_logs() -> None:
        async for event in event_bus.subscribe():
            if event.type == "log" and isinstance(event.data, str):
                log_events.append(event.data)
            if (
                event.type == "status_change"
                and isinstance(event.data, dict)
                and event.data.get("status") == "done"
            ):
                break

    with (
        patch.object(scheduler, "_get_executor", return_value=mock_exec),
        patch("src.scheduler.asyncio.sleep", return_value=None),
    ):
        collector = asyncio.create_task(_collect_logs())
        await scheduler.tick()

        task_ids = list(scheduler.running.keys())
        await scheduler.running[task_ids[0]]

        await asyncio.sleep(0.05)
        if not collector.done():
            collector.cancel()

    # Should see retry log messages
    retry_logs = [entry for entry in log_events if "Retry" in entry]
    assert len(retry_logs) == 2  # Retry 1/3 and Retry 2/3

    final = await task_manager.get_task(task.id)
    assert final is not None
    assert final.status == TaskStatus.DONE
