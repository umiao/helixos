"""Integration test: sync -> QUEUED -> RUNNING -> DONE -> git commit.

Tests the full lifecycle from parsing TASKS.md through scheduler execution
to git auto-commit, using a mock executor (no real claude CLI).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config import ProjectRegistry
from src.events import EventBus
from src.executors.base import ExecutorResult
from src.models import TaskStatus
from src.scheduler import Scheduler
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import TaskManager

from .conftest import MockExecutor


@pytest.mark.integration
async def test_sync_creates_queued_tasks(
    task_manager: TaskManager,
    make_config,
    temp_project_repo,
    sample_tasks_md,
) -> None:
    """Syncing TASKS.md should create tasks in QUEUED status."""
    # Write TASKS.md to the project repo
    tasks_file = temp_project_repo / "TASKS.md"
    tasks_file.write_text(sample_tasks_md, encoding="utf-8")

    config = make_config()
    registry = ProjectRegistry(config)

    result = await sync_project_tasks("proj_a", task_manager, registry)

    assert result.added == 2
    assert result.updated == 0

    tasks = await task_manager.list_tasks(project_id="proj_a")
    assert len(tasks) == 2
    assert all(t.status == TaskStatus.QUEUED for t in tasks)


@pytest.mark.integration
async def test_sync_to_execute_full_lifecycle(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    temp_project_repo,
    sample_tasks_md,
    env_loader,
) -> None:
    """Full flow: sync -> tick -> RUNNING -> DONE -> git commit."""
    # Write TASKS.md
    tasks_file = temp_project_repo / "TASKS.md"
    tasks_file.write_text(sample_tasks_md, encoding="utf-8")

    config = make_config()
    registry = ProjectRegistry(config)

    # Sync tasks
    await sync_project_tasks("proj_a", task_manager, registry)

    # Create scheduler with mock executor
    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    mock_exec = MockExecutor()
    with patch.object(scheduler, "_get_executor", return_value=mock_exec):
        # First tick: picks one QUEUED task (per-project concurrency = 1)
        await scheduler.tick()

        assert len(scheduler.running) == 1
        running_tasks = await task_manager.list_tasks(
            project_id="proj_a", status=TaskStatus.RUNNING,
        )
        assert len(running_tasks) == 1

        # Wait for the execution task to complete
        running_task_ids = list(scheduler.running.keys())
        await scheduler.running[running_task_ids[0]]

    # Verify task is DONE
    task = await task_manager.get_task(running_task_ids[0])
    assert task is not None
    assert task.status == TaskStatus.DONE


@pytest.mark.integration
async def test_sync_to_execute_with_git_commit(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    temp_project_repo,
    env_loader,
) -> None:
    """After successful execution, git auto-commit should run."""
    # Write TASKS.md with one task
    tasks_md = (
        "# Task Backlog\n"
        "\n"
        "## Active Tasks\n"
        "\n"
        "#### T-P0-1: Add new file\n"
        "- Add something\n"
        "\n"
        "## Completed Tasks\n"
    )
    tasks_file = temp_project_repo / "TASKS.md"
    tasks_file.write_text(tasks_md, encoding="utf-8")

    config = make_config()
    registry = ProjectRegistry(config)

    await sync_project_tasks("proj_a", task_manager, registry)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    # The mock executor simulates file creation for git commit
    async def mock_execute_and_create_file(
        task, project, env, on_log,
    ) -> ExecutorResult:
        """Simulate work by creating a file in the repo."""
        new_file = temp_project_repo / "output.txt"
        new_file.write_text("Generated output\n", encoding="utf-8")
        on_log("Created output.txt")
        return ExecutorResult(
            success=True, exit_code=0, duration_seconds=0.1,
        )

    mock_exec = MockExecutor()
    mock_exec.execute = mock_execute_and_create_file  # type: ignore[assignment]

    with patch.object(scheduler, "_get_executor", return_value=mock_exec):
        await scheduler.tick()

        task_ids = list(scheduler.running.keys())
        assert len(task_ids) == 1
        await scheduler.running[task_ids[0]]

    # Verify task is DONE
    task = await task_manager.get_task(task_ids[0])
    assert task is not None
    assert task.status == TaskStatus.DONE

    # Verify git commit happened (output.txt should be committed)
    import subprocess

    result = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=str(temp_project_repo),
        capture_output=True,
        encoding="utf-8",
    )
    assert "[helixos]" in result.stdout


@pytest.mark.integration
async def test_events_emitted_during_lifecycle(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    temp_project_repo,
    sample_tasks_md,
    env_loader,
) -> None:
    """Status change and log events should be emitted during execution."""
    tasks_file = temp_project_repo / "TASKS.md"
    tasks_file.write_text(sample_tasks_md, encoding="utf-8")

    config = make_config()
    registry = ProjectRegistry(config)
    await sync_project_tasks("proj_a", task_manager, registry)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    # Collect events
    collected: list[dict] = []

    async def _collect() -> None:
        async for event in event_bus.subscribe():
            collected.append({"type": event.type, "task_id": event.task_id})
            if event.type == "status_change" and event.data.get("status") == "done":
                break

    collector = None
    mock_exec = MockExecutor()
    with patch.object(scheduler, "_get_executor", return_value=mock_exec):
        import asyncio

        collector = asyncio.create_task(_collect())
        await scheduler.tick()

        task_ids = list(scheduler.running.keys())
        await scheduler.running[task_ids[0]]

        # Give collector time to process
        await asyncio.sleep(0.1)
        if not collector.done():
            collector.cancel()

    # Verify we got status_change and log events
    event_types = {e["type"] for e in collected}
    assert "status_change" in event_types
    assert "log" in event_types
