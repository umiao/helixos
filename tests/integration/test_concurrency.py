"""Integration test: 5 tasks across 2 projects, verify concurrency limits.

Tests that the scheduler respects per-project and global concurrency
limits when dispatching tasks.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config import ProjectConfig, ProjectRegistry
from src.events import EventBus
from src.models import ExecutorType, Task, TaskStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager

from .conftest import MockExecutor


@pytest.mark.integration
async def test_per_project_concurrency_limit(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    temp_project_repo,
    env_loader,
) -> None:
    """Only 1 task per project when max_concurrency=1."""
    config = make_config(per_project_concurrency=1)
    registry = ProjectRegistry(config)

    # Create 3 QUEUED tasks for proj_a
    for i in range(1, 4):
        task = Task(
            id=f"proj_a:T-P0-{i}",
            project_id="proj_a",
            local_task_id=f"T-P0-{i}",
            title=f"Task {i}",
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

    # Use a slow executor so tasks stay RUNNING during the tick
    mock_exec = MockExecutor(delay=10.0)  # Long delay

    with patch.object(scheduler, "_get_executor", return_value=mock_exec):
        await scheduler.tick()

    # Only 1 should be running (per-project limit)
    assert len(scheduler.running) == 1

    running = await task_manager.list_tasks(
        project_id="proj_a", status=TaskStatus.RUNNING,
    )
    assert len(running) == 1

    queued = await task_manager.list_tasks(
        project_id="proj_a", status=TaskStatus.QUEUED,
    )
    assert len(queued) == 2

    # Cleanup
    for task_id in list(scheduler.running.keys()):
        scheduler.running[task_id].cancel()


@pytest.mark.integration
async def test_global_concurrency_with_two_projects(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    temp_project_repo,
    tmp_path,
    env_loader,
) -> None:
    """5 tasks across 2 projects, global limit=3, per-project=1.

    With 2 projects, effective global limit = min(3, 2) = 2.
    Per-project limit = 1, so each project gets exactly 1 slot.
    Total should be 2 (one per project).
    """
    import subprocess

    # Create second project repo
    repo_b = tmp_path / "project_b"
    repo_b.mkdir()
    subprocess.run(
        ["git", "init"], cwd=str(repo_b),
        check=True, capture_output=True, encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.email", "test@helixos.test"],
        cwd=str(repo_b), check=True, capture_output=True, encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(repo_b), check=True, capture_output=True, encoding="utf-8",
    )
    (repo_b / "README.md").write_text("# B\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"], cwd=str(repo_b),
        check=True, capture_output=True, encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(repo_b),
        check=True, capture_output=True, encoding="utf-8",
    )

    config = make_config(
        global_concurrency_limit=3,
        per_project_concurrency=1,
        projects={
            "proj_a": ProjectConfig(
                name="Project A",
                repo_path=temp_project_repo,
                executor_type=ExecutorType.CODE,
                max_concurrency=1,
            ),
            "proj_b": ProjectConfig(
                name="Project B",
                repo_path=repo_b,
                executor_type=ExecutorType.CODE,
                max_concurrency=1,
            ),
        },
    )
    registry = ProjectRegistry(config)

    # Interleave task creation so get_ready_tasks (ordered by created_at)
    # returns tasks from both projects in the first N candidates.
    from datetime import UTC, datetime, timedelta

    base_time = datetime.now(UTC)

    # Create tasks interleaved: a1, b1, a2, b2, a3
    tasks_to_create = [
        ("proj_a", "T-P0-1", "A Task 1", 0),
        ("proj_b", "T-P0-1", "B Task 1", 1),
        ("proj_a", "T-P0-2", "A Task 2", 2),
        ("proj_b", "T-P0-2", "B Task 2", 3),
        ("proj_a", "T-P0-3", "A Task 3", 4),
    ]
    for proj, tid, title, offset in tasks_to_create:
        task = Task(
            id=f"{proj}:{tid}",
            project_id=proj,
            local_task_id=tid,
            title=title,
            status=TaskStatus.QUEUED,
            executor_type=ExecutorType.CODE,
            created_at=base_time + timedelta(seconds=offset),
            updated_at=base_time + timedelta(seconds=offset),
        )
        await task_manager.create_task(task)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    mock_exec = MockExecutor(delay=10.0)

    with patch.object(scheduler, "_get_executor", return_value=mock_exec):
        await scheduler.tick()

    # Global effective limit = min(3, 2 projects) = 2
    assert len(scheduler.running) == 2

    # Each project should have exactly 1 running (per-project limit = 1)
    running_a = await task_manager.list_tasks(
        project_id="proj_a", status=TaskStatus.RUNNING,
    )
    running_b = await task_manager.list_tasks(
        project_id="proj_b", status=TaskStatus.RUNNING,
    )

    assert len(running_a) == 1
    assert len(running_b) == 1

    # Cleanup
    for task_id in list(scheduler.running.keys()):
        scheduler.running[task_id].cancel()


@pytest.mark.integration
async def test_dependency_blocks_execution(
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
    temp_project_repo,
    env_loader,
) -> None:
    """Task with unfulfilled dependency should not be dispatched."""
    config = make_config()
    registry = ProjectRegistry(config)

    # Task 1: no deps, QUEUED
    task1 = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Independent task",
        status=TaskStatus.QUEUED,
        executor_type=ExecutorType.CODE,
    )
    await task_manager.create_task(task1)

    # Task 2: depends on task1, QUEUED
    task2 = Task(
        id="proj_a:T-P0-2",
        project_id="proj_a",
        local_task_id="T-P0-2",
        title="Dependent task",
        status=TaskStatus.QUEUED,
        executor_type=ExecutorType.CODE,
        depends_on=["proj_a:T-P0-1"],
    )
    await task_manager.create_task(task2)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    mock_exec = MockExecutor(delay=10.0)

    with patch.object(scheduler, "_get_executor", return_value=mock_exec):
        await scheduler.tick()

    # Only task1 should be running (task2 blocked by dependency)
    assert len(scheduler.running) == 1
    assert "proj_a:T-P0-1" in scheduler.running

    running = await task_manager.list_tasks(
        project_id="proj_a", status=TaskStatus.RUNNING,
    )
    assert len(running) == 1
    assert running[0].id == "proj_a:T-P0-1"

    # Cleanup
    for task_id in list(scheduler.running.keys()):
        scheduler.running[task_id].cancel()
