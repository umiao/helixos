"""Tests for the Scheduler core (tick loop, concurrency, dependency checking)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import OrchestratorConfig, ProjectConfig, ProjectRegistry
from src.events import EventBus
from src.executors.base import BaseExecutor, ExecutorResult
from src.executors.code_executor import CodeExecutor
from src.models import ExecutorType, Project, Task, TaskStatus
from src.scheduler import Scheduler
from src.task_manager import TaskManager

# ---------------------------------------------------------------------------
# Mock executor
# ---------------------------------------------------------------------------


class MockExecutor(BaseExecutor):
    """Executor that returns a configurable result immediately."""

    def __init__(
        self,
        result: ExecutorResult | None = None,
        hang: bool = False,
    ) -> None:
        """Initialize with an optional result and hang flag.

        Args:
            result: The result to return from execute(). Defaults to success.
            hang: If True, block until release() is called.
        """
        self._result = result or ExecutorResult(
            success=True,
            exit_code=0,
            log_lines=["mock output"],
            duration_seconds=0.1,
        )
        self._hang = hang
        self._release_event: asyncio.Event | None = (
            asyncio.Event() if hang else None
        )
        self.calls: list[str] = []
        self.log_callbacks: list[Callable[[str], None]] = []

    async def execute(
        self,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
    ) -> ExecutorResult:
        """Record the call, invoke on_log, and return the result."""
        self.calls.append(task.id)
        self.log_callbacks.append(on_log)
        on_log("mock log line")
        if self._hang and self._release_event is not None:
            await self._release_event.wait()
        return self._result

    def release(self) -> None:
        """Release a hanging executor."""
        if self._release_event is not None:
            self._release_event.set()

    async def cancel(self) -> None:
        """Cancel (no-op for mock)."""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(
    *,
    projects: dict[str, ProjectConfig] | None = None,
    global_limit: int = 3,
) -> OrchestratorConfig:
    """Build an OrchestratorConfig for testing."""
    if projects is None:
        projects = {
            "proj": ProjectConfig(
                name="Test Project",
                repo_path=Path("/tmp/test-repo"),
                executor_type=ExecutorType.CODE,
                max_concurrency=1,
            ),
        }
    config = OrchestratorConfig(projects=projects)
    config.orchestrator.global_concurrency_limit = global_limit
    return config


def _make_task(
    task_id: str = "proj:t1",
    project_id: str = "proj",
    local_task_id: str = "t1",
    title: str = "Test Task",
    status: TaskStatus = TaskStatus.QUEUED,
    depends_on: list[str] | None = None,
) -> Task:
    """Build a Task for testing."""
    return Task(
        id=task_id,
        project_id=project_id,
        local_task_id=local_task_id,
        title=title,
        status=status,
        executor_type=ExecutorType.CODE,
        depends_on=depends_on or [],
    )


def _track_events(event_bus: EventBus) -> list[tuple[str, str, object]]:
    """Wrap event_bus.emit to track emitted events."""
    emitted: list[tuple[str, str, object]] = []
    original_emit = event_bus.emit

    def tracking_emit(
        event_type: str, task_id: str, data: object
    ) -> None:
        emitted.append((event_type, task_id, data))
        original_emit(event_type, task_id, data)

    event_bus.emit = tracking_emit  # type: ignore[assignment]
    return emitted


@pytest.fixture
async def scheduler_env(session_factory):
    """Set up a Scheduler with all dependencies wired for testing."""
    task_manager = TaskManager(session_factory)
    config = _make_config()
    registry = ProjectRegistry(config)

    env_loader = MagicMock()
    env_loader.get_project_env.return_value = {}

    event_bus = EventBus()
    emitted = _track_events(event_bus)

    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
    )

    return scheduler, task_manager, event_bus, emitted


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSchedulerTick:
    """Tests for the Scheduler tick() dispatch logic."""

    async def test_tick_dispatches_queued_task(self, scheduler_env) -> None:
        """Tick should dispatch a QUEUED task and complete it successfully."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        # Wait for execution to complete (task is scheduled but may not
        # have started yet -- gather runs it to completion)
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        # Execution task was invoked
        assert len(mock_exec.calls) == 1

        # Task should be DONE
        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.DONE

        # Events: status_change(running), log, status_change(done)
        event_types = [e[0] for e in emitted]
        assert "status_change" in event_types
        assert "log" in event_types

    async def test_tick_no_queued_tasks(self, scheduler_env) -> None:
        """Tick with no QUEUED tasks should be a no-op."""
        scheduler, _tm, _eb, emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        await scheduler.tick()

        assert len(mock_exec.calls) == 0
        assert len(emitted) == 0

    async def test_tick_failure_emits_alert(self, scheduler_env) -> None:
        """On executor failure, task should go to FAILED and emit alert."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        fail_result = ExecutorResult(
            success=False,
            exit_code=1,
            log_lines=["error occurred"],
            error_summary="Build failed",
            duration_seconds=5.0,
        )
        mock_exec = MockExecutor(result=fail_result)
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        # Wait for execution to complete
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        # Task should be FAILED
        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.FAILED

        # Alert event should have been emitted
        alert_events = [e for e in emitted if e[0] == "alert"]
        assert len(alert_events) >= 1
        assert alert_events[0][1] == "proj:t1"

    async def test_tick_executor_exception(self, scheduler_env) -> None:
        """Unhandled executor exception should mark task FAILED."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        class CrashingExecutor(BaseExecutor):
            """Executor that raises an exception."""

            async def execute(self, task, project, env, on_log):
                """Raise an unhandled error."""
                raise RuntimeError("kaboom")

            async def cancel(self) -> None:
                """No-op."""

        scheduler._get_executor = lambda _: CrashingExecutor()

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.FAILED

        alert_events = [e for e in emitted if e[0] == "alert"]
        assert len(alert_events) >= 1

    async def test_tick_log_events(self, scheduler_env) -> None:
        """Executor log callback should emit 'log' events."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        log_events = [e for e in emitted if e[0] == "log"]
        assert len(log_events) >= 1
        assert log_events[0][1] == "proj:t1"
        assert log_events[0][2] == "mock log line"


class TestConcurrency:
    """Tests for concurrency control."""

    async def test_project_concurrency_limit(self, session_factory) -> None:
        """Only 1 task should dispatch per project when max_concurrency=1."""
        task_manager = TaskManager(session_factory)
        config = _make_config(
            projects={
                "proj": ProjectConfig(
                    name="Test",
                    repo_path=Path("/tmp/test"),
                    executor_type=ExecutorType.CODE,
                    max_concurrency=1,
                ),
                "proj2": ProjectConfig(
                    name="Test2",
                    repo_path=Path("/tmp/test2"),
                    executor_type=ExecutorType.CODE,
                    max_concurrency=1,
                ),
            },
            global_limit=5,
        )
        registry = ProjectRegistry(config)
        env_loader = MagicMock()
        env_loader.get_project_env.return_value = {}
        event_bus = EventBus()

        scheduler = Scheduler(config, task_manager, registry, env_loader, event_bus)

        # Hanging executor so tasks don't complete during tick
        hanging_exec = MockExecutor(hang=True)
        scheduler._get_executor = lambda _: hanging_exec

        # Create 2 QUEUED tasks for same project
        await task_manager.create_task(_make_task("proj:a", "proj", "a", "Task A"))
        await task_manager.create_task(_make_task("proj:b", "proj", "b", "Task B"))

        await scheduler.tick()

        # Only 1 should have been dispatched
        assert len(hanging_exec.calls) == 1
        assert len(scheduler.running) == 1

        # Release and clean up
        hanging_exec.release()
        await asyncio.gather(*list(scheduler.running.values()))

    async def test_global_concurrency_limit(self, session_factory) -> None:
        """Global slots should limit total concurrent tasks."""
        task_manager = TaskManager(session_factory)

        config = _make_config(
            projects={
                "p1": ProjectConfig(
                    name="P1", repo_path=Path("/tmp/p1"),
                    executor_type=ExecutorType.CODE, max_concurrency=2,
                ),
                "p2": ProjectConfig(
                    name="P2", repo_path=Path("/tmp/p2"),
                    executor_type=ExecutorType.CODE, max_concurrency=2,
                ),
            },
            global_limit=1,
        )
        registry = ProjectRegistry(config)
        env_loader = MagicMock()
        env_loader.get_project_env.return_value = {}
        event_bus = EventBus()

        scheduler = Scheduler(config, task_manager, registry, env_loader, event_bus)

        hanging_exec = MockExecutor(hang=True)
        scheduler._get_executor = lambda _: hanging_exec

        await task_manager.create_task(_make_task("p1:a", "p1", "a", "P1-A"))
        await task_manager.create_task(_make_task("p2:a", "p2", "a", "P2-A"))

        await scheduler.tick()
        # Let the execution task start (it was scheduled but not yet running)
        await asyncio.sleep(0)

        # Global limit is 1 -> only 1 task dispatched total
        assert len(hanging_exec.calls) == 1
        assert len(scheduler.running) == 1

        hanging_exec.release()
        await asyncio.gather(*list(scheduler.running.values()))

    async def test_available_slots_property(self, scheduler_env) -> None:
        """available_slots should reflect global limit minus running count."""
        scheduler, _tm, _eb, _emitted = scheduler_env

        # 1 project, global_limit=3 -> min(3, 1) = 1
        assert scheduler.available_slots == 1

        # Simulate a running task
        scheduler.running["fake:t1"] = asyncio.create_task(asyncio.sleep(10))
        assert scheduler.available_slots == 0

        # Clean up
        scheduler.running["fake:t1"].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler.running["fake:t1"]
        del scheduler.running["fake:t1"]

    async def test_manual_only_project(self, session_factory) -> None:
        """Project with max_concurrency=0 should never have tasks dispatched."""
        task_manager = TaskManager(session_factory)

        config = _make_config(
            projects={
                "manual": ProjectConfig(
                    name="Manual",
                    repo_path=Path("/tmp/manual"),
                    executor_type=ExecutorType.CODE,
                    max_concurrency=0,
                ),
            },
        )
        registry = ProjectRegistry(config)
        env_loader = MagicMock()
        env_loader.get_project_env.return_value = {}
        event_bus = EventBus()

        scheduler = Scheduler(config, task_manager, registry, env_loader, event_bus)
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        await task_manager.create_task(
            _make_task("manual:t1", "manual", "t1", "Manual Task")
        )

        await scheduler.tick()

        assert len(mock_exec.calls) == 0


class TestDependencies:
    """Tests for dependency checking."""

    async def test_deps_blocking(self, scheduler_env) -> None:
        """Task with unfulfilled deps should not be dispatched."""
        scheduler, task_manager, _eb, emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        # Task B is QUEUED (not DONE)
        dep_task = _make_task("proj:dep", "proj", "dep", "Dependency")
        await task_manager.create_task(dep_task)

        # Task A depends on B
        task = _make_task("proj:main", "proj", "main", "Main", depends_on=["proj:dep"])
        await task_manager.create_task(task)

        await scheduler.tick()

        # dep task dispatched (no deps), but main should NOT be
        # (dep is QUEUED, not DONE)
        dispatched_ids = mock_exec.calls
        assert "proj:main" not in dispatched_ids

    async def test_deps_fulfilled(self, scheduler_env) -> None:
        """Task with all deps DONE should be dispatched."""
        scheduler, task_manager, _eb, _emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        # Create dependency task and manually set it to DONE
        dep_task = _make_task("proj:dep", "proj", "dep", "Dependency")
        await task_manager.create_task(dep_task)
        # Transition: QUEUED -> RUNNING -> DONE
        await task_manager.update_status("proj:dep", TaskStatus.RUNNING)
        await task_manager.update_status("proj:dep", TaskStatus.DONE)

        # Task that depends on the completed dep
        task = _make_task(
            "proj:main", "proj", "main", "Main Task", depends_on=["proj:dep"]
        )
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        # Main task should have been dispatched and completed
        assert "proj:main" in mock_exec.calls

        updated = await task_manager.get_task("proj:main")
        assert updated is not None
        assert updated.status == TaskStatus.DONE

    async def test_deps_missing_task(self, scheduler_env) -> None:
        """Task depending on a non-existent task should not dispatch."""
        scheduler, task_manager, _eb, _emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task(
            "proj:t1", "proj", "t1", "Task",
            depends_on=["proj:nonexistent"],
        )
        await task_manager.create_task(task)

        await scheduler.tick()

        assert "proj:t1" not in mock_exec.calls


class TestSchedulerLifecycle:
    """Tests for scheduler start/stop lifecycle."""

    async def test_start_creates_tick_task(self, scheduler_env) -> None:
        """start() should create a background tick task."""
        scheduler, _tm, _eb, _emitted = scheduler_env

        assert scheduler._tick_task is None

        await scheduler.start()
        assert scheduler._tick_task is not None
        assert not scheduler._tick_task.done()

        await scheduler.stop()
        assert scheduler._tick_task is None

    async def test_stop_cancels_tick_task(self, scheduler_env) -> None:
        """stop() should cancel the tick task cleanly."""
        scheduler, _tm, _eb, _emitted = scheduler_env

        await scheduler.start()
        tick_task = scheduler._tick_task
        assert tick_task is not None

        await scheduler.stop()
        assert tick_task.done()

    async def test_stop_when_not_started(self, scheduler_env) -> None:
        """stop() when never started should be a no-op."""
        scheduler, _tm, _eb, _emitted = scheduler_env
        await scheduler.stop()  # Should not raise


class TestGetExecutor:
    """Tests for the executor factory."""

    def test_get_executor_returns_code_executor(self, scheduler_env) -> None:
        """_get_executor should return a CodeExecutor for MVP."""
        scheduler, _tm, _eb, _emitted = scheduler_env

        executor = scheduler._get_executor(ExecutorType.CODE)
        assert isinstance(executor, CodeExecutor)

    def test_get_executor_all_types_return_code(self, scheduler_env) -> None:
        """All executor types should return CodeExecutor in MVP."""
        scheduler, _tm, _eb, _emitted = scheduler_env

        for exec_type in ExecutorType:
            executor = scheduler._get_executor(exec_type)
            assert isinstance(executor, CodeExecutor)


class TestProjectIsBusy:
    """Tests for _project_is_busy()."""

    async def test_not_busy_when_no_running(self, scheduler_env) -> None:
        """Project with no running tasks should not be busy."""
        scheduler, _tm, _eb, _emitted = scheduler_env
        assert not await scheduler._project_is_busy("proj")

    async def test_busy_when_at_limit(self, scheduler_env) -> None:
        """Project at max_concurrency should be busy."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        # Create and set a task to RUNNING
        task = _make_task()
        await task_manager.create_task(task)
        await task_manager.update_status("proj:t1", TaskStatus.RUNNING)

        assert await scheduler._project_is_busy("proj")

    async def test_unknown_project(self, scheduler_env) -> None:
        """Unknown project should be considered busy (safe default)."""
        scheduler, _tm, _eb, _emitted = scheduler_env
        assert await scheduler._project_is_busy("nonexistent")


class TestDepsFulfilled:
    """Tests for _deps_fulfilled()."""

    async def test_no_deps(self, scheduler_env) -> None:
        """Task with no dependencies should always be fulfilled."""
        scheduler, _tm, _eb, _emitted = scheduler_env
        task = _make_task(depends_on=[])
        assert await scheduler._deps_fulfilled(task)

    async def test_all_deps_done(self, scheduler_env) -> None:
        """All deps DONE -> fulfilled."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        dep = _make_task("proj:dep", "proj", "dep", "Dep")
        await task_manager.create_task(dep)
        await task_manager.update_status("proj:dep", TaskStatus.RUNNING)
        await task_manager.update_status("proj:dep", TaskStatus.DONE)

        task = _make_task(depends_on=["proj:dep"])
        assert await scheduler._deps_fulfilled(task)

    async def test_dep_not_done(self, scheduler_env) -> None:
        """Dep not DONE -> not fulfilled."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        dep = _make_task("proj:dep", "proj", "dep", "Dep")
        await task_manager.create_task(dep)

        task = _make_task(depends_on=["proj:dep"])
        assert not await scheduler._deps_fulfilled(task)
