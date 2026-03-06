"""Tests for the Scheduler (tick loop, concurrency, dependency, retry, recovery, cancel)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import OrchestratorConfig, ProjectConfig, ProjectRegistry
from src.events import EventBus
from src.executors.base import BaseExecutor, ErrorType, ExecutorResult
from src.executors.code_executor import CodeExecutor
from src.models import ExecutorType, Project, Task, TaskStatus
from src.scheduler import RETRY_BACKOFF_SECONDS, Scheduler
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
        self._cancel_called = False

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
        if self._cancel_called:
            return ExecutorResult(
                success=False,
                exit_code=-1,
                log_lines=[],
                error_summary="Cancelled",
                duration_seconds=0.0,
            )
        return self._result

    def release(self) -> None:
        """Release a hanging executor."""
        if self._release_event is not None:
            self._release_event.set()

    async def cancel(self) -> None:
        """Record that cancel was called."""
        self._cancel_called = True
        if self._release_event is not None:
            self._release_event.set()


class FailThenSucceedExecutor(BaseExecutor):
    """Executor that fails a configurable number of times then succeeds.

    This executor is shared across retries (the scheduler's _get_executor
    is overridden to always return the same instance).
    """

    def __init__(self, fail_count: int = 1) -> None:
        """Initialize with the number of failures before success.

        Args:
            fail_count: How many times to fail before returning success.
        """
        self._fail_count = fail_count
        self.calls: list[str] = []

    async def execute(
        self,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
    ) -> ExecutorResult:
        """Fail for the first N calls, then succeed."""
        self.calls.append(task.id)
        on_log(f"attempt {len(self.calls)}")
        if len(self.calls) <= self._fail_count:
            return ExecutorResult(
                success=False,
                exit_code=1,
                log_lines=[],
                error_summary="Execution failed",
                duration_seconds=1.0,
            )
        return ExecutorResult(
            success=True,
            exit_code=0,
            log_lines=[],
            duration_seconds=1.0,
        )

    async def cancel(self) -> None:
        """No-op."""


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
        event_type: str, task_id: str, data: object, **kwargs: object
    ) -> None:
        emitted.append((event_type, task_id, data))
        original_emit(event_type, task_id, data, **kwargs)

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
# Tests -- Tick dispatch
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

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_tick_failure_emits_alert(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """On executor failure, task should go to BLOCKED and emit alert."""
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

        # Task should be BLOCKED (RUNNING -> FAILED -> BLOCKED)
        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.BLOCKED

        # Alert event should have been emitted
        alert_events = [e for e in emitted if e[0] == "alert"]
        assert len(alert_events) >= 1
        assert alert_events[0][1] == "proj:t1"

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_tick_executor_exception(
        self, mock_sleep, scheduler_env,
    ) -> None:
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

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_tick_log_events(self, mock_sleep, scheduler_env) -> None:
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


# ---------------------------------------------------------------------------
# Tests -- Concurrency
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests -- Dependencies
# ---------------------------------------------------------------------------


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

        # Clean up any dispatched tasks to avoid leaking into other tests
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_deps_fulfilled(self, mock_sleep, scheduler_env) -> None:
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


# ---------------------------------------------------------------------------
# Tests -- Lifecycle
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests -- Executor factory
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests -- _project_is_busy
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests -- _deps_fulfilled
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tests -- Retry with exponential backoff
# ---------------------------------------------------------------------------


class TestRetry:
    """Tests for _run_with_retry and retry behavior."""

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_succeeds_on_second_attempt(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """Task that fails once then succeeds should end up DONE."""
        scheduler, task_manager, _eb, emitted = scheduler_env

        # Fails first attempt, succeeds on retry 1
        exec_instance = FailThenSucceedExecutor(fail_count=1)
        scheduler._get_executor = lambda _: exec_instance

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.DONE

        # Should have been called twice (initial + 1 retry)
        assert len(exec_instance.calls) == 2

        # Sleep was called once with 30s backoff
        mock_sleep.assert_awaited_once_with(30)

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_succeeds_on_third_attempt(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """Task that fails twice then succeeds should end up DONE."""
        scheduler, task_manager, _eb, emitted = scheduler_env

        exec_instance = FailThenSucceedExecutor(fail_count=2)
        scheduler._get_executor = lambda _: exec_instance

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.DONE

        assert len(exec_instance.calls) == 3
        assert mock_sleep.await_count == 2

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_max_retries_leads_to_blocked(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """After max retries exhausted, task goes FAILED -> BLOCKED."""
        scheduler, task_manager, _eb, emitted = scheduler_env

        fail_result = ExecutorResult(
            success=False,
            exit_code=1,
            log_lines=[],
            error_summary="Build failed",
            duration_seconds=1.0,
        )
        mock_exec = MockExecutor(result=fail_result)
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        # 1 initial + 3 retries = 4 total attempts
        assert len(mock_exec.calls) == 4

        # Task should be BLOCKED
        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.BLOCKED

        # Alert emitted with error summary
        alert_events = [e for e in emitted if e[0] == "alert"]
        assert len(alert_events) >= 1

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_backoff_intervals(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """Backoff intervals should be 30s, 60s, 120s."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        fail_result = ExecutorResult(
            success=False, exit_code=1, log_lines=[],
            error_summary="fail", duration_seconds=1.0,
        )
        mock_exec = MockExecutor(result=fail_result)
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        # Sleep called 3 times with expected backoff
        sleep_calls = [call.args[0] for call in mock_sleep.await_args_list]
        assert sleep_calls == RETRY_BACKOFF_SECONDS

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_emits_log_events(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """Each retry attempt should emit a log event."""
        scheduler, task_manager, _eb, emitted = scheduler_env

        fail_result = ExecutorResult(
            success=False, exit_code=1, log_lines=[],
            error_summary="fail", duration_seconds=1.0,
        )
        mock_exec = MockExecutor(result=fail_result)
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        # Retry log events
        retry_logs = [
            e for e in emitted
            if e[0] == "log" and isinstance(e[2], str) and "Retry" in e[2]
        ]
        assert len(retry_logs) == 3
        assert "1/3" in retry_logs[0][2]
        assert "2/3" in retry_logs[1][2]
        assert "3/3" in retry_logs[2][2]

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_no_retry_on_success(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """Successful execution should not trigger any retries."""
        scheduler, task_manager, _eb, emitted = scheduler_env

        mock_exec = MockExecutor()  # Default: success
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        assert len(mock_exec.calls) == 1
        mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests -- Startup recovery
# ---------------------------------------------------------------------------


class TestStartupRecovery:
    """Tests for startup_recovery()."""

    async def test_recovers_running_tasks(self, scheduler_env) -> None:
        """Running tasks on startup should be marked FAILED."""
        scheduler, task_manager, _eb, emitted = scheduler_env

        # Create a task and move to RUNNING
        task = _make_task()
        await task_manager.create_task(task)
        await task_manager.update_status("proj:t1", TaskStatus.RUNNING)

        count = await scheduler.startup_recovery()

        assert count == 1

        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.FAILED

    async def test_emits_alerts_for_recovered_tasks(
        self, scheduler_env,
    ) -> None:
        """An alert event should be emitted for each recovered task."""
        scheduler, task_manager, _eb, emitted = scheduler_env

        # Create two tasks in RUNNING state
        t1 = _make_task("proj:t1", "proj", "t1", "Task 1")
        t2 = _make_task("proj:t2", "proj", "t2", "Task 2")
        await task_manager.create_task(t1)
        await task_manager.create_task(t2)
        await task_manager.update_status("proj:t1", TaskStatus.RUNNING)
        await task_manager.update_status("proj:t2", TaskStatus.RUNNING)

        count = await scheduler.startup_recovery()

        assert count == 2

        alert_events = [e for e in emitted if e[0] == "alert"]
        assert len(alert_events) == 2

        alerted_ids = {e[1] for e in alert_events}
        assert alerted_ids == {"proj:t1", "proj:t2"}

        # Each alert should mention crash recovery
        for _, _, data in alert_events:
            assert "Recovered from crash" in data["error"]

    async def test_no_running_tasks(self, scheduler_env) -> None:
        """Startup recovery with no running tasks returns 0."""
        scheduler, _tm, _eb, emitted = scheduler_env

        count = await scheduler.startup_recovery()

        assert count == 0
        assert len(emitted) == 0

    async def test_recovery_does_not_affect_queued(
        self, scheduler_env,
    ) -> None:
        """Startup recovery should only affect RUNNING tasks."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        # One QUEUED, one RUNNING
        t1 = _make_task("proj:q1", "proj", "q1", "Queued Task")
        t2 = _make_task("proj:r1", "proj", "r1", "Running Task")
        await task_manager.create_task(t1)
        await task_manager.create_task(t2)
        await task_manager.update_status("proj:r1", TaskStatus.RUNNING)

        count = await scheduler.startup_recovery()

        assert count == 1

        q1 = await task_manager.get_task("proj:q1")
        assert q1 is not None
        assert q1.status == TaskStatus.QUEUED

        r1 = await task_manager.get_task("proj:r1")
        assert r1 is not None
        assert r1.status == TaskStatus.FAILED


# ---------------------------------------------------------------------------
# Tests -- Cancel task
# ---------------------------------------------------------------------------


class TestCancelTask:
    """Tests for cancel_task()."""

    async def test_cancel_nonexistent_returns_false(
        self, scheduler_env,
    ) -> None:
        """Cancelling a task that is not running should return False."""
        scheduler, _tm, _eb, _emitted = scheduler_env
        result = await scheduler.cancel_task("proj:nonexistent")
        assert result is False

    async def test_cancel_running_task(self, scheduler_env) -> None:
        """cancel_task should stop a running task and mark it FAILED."""
        scheduler, task_manager, _eb, emitted = scheduler_env

        hanging_exec = MockExecutor(hang=True)
        scheduler._get_executor = lambda _: hanging_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()
        # Let the execution task start and reach the hang point
        await asyncio.sleep(0)

        assert "proj:t1" in scheduler.running

        result = await scheduler.cancel_task("proj:t1")
        assert result is True

        # Task removed from running
        assert "proj:t1" not in scheduler.running

        # Task status should be FAILED
        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.FAILED

        # Alert emitted
        alert_events = [e for e in emitted if e[0] == "alert"]
        cancelled_alerts = [
            e for e in alert_events
            if "cancelled" in str(e[2]).lower()
        ]
        assert len(cancelled_alerts) >= 1

    async def test_cancel_calls_executor_cancel(
        self, scheduler_env,
    ) -> None:
        """cancel_task should call executor.cancel()."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        hanging_exec = MockExecutor(hang=True)
        scheduler._get_executor = lambda _: hanging_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()
        # Let the execution task start and reach the hang point
        await asyncio.sleep(0)

        await scheduler.cancel_task("proj:t1")

        assert hanging_exec._cancel_called is True

    async def test_cancel_cleans_up_state(self, scheduler_env) -> None:
        """After cancel, task should be removed from running and executors."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        hanging_exec = MockExecutor(hang=True)
        scheduler._get_executor = lambda _: hanging_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()
        # Let the execution task start and reach the hang point
        await asyncio.sleep(0)

        await scheduler.cancel_task("proj:t1")

        assert "proj:t1" not in scheduler.running
        assert "proj:t1" not in scheduler._executors
        assert "proj:t1" not in scheduler._cancelled


# ---------------------------------------------------------------------------
# Tests -- Auto-commit hook
# ---------------------------------------------------------------------------


class TestAutoCommitHook:
    """Tests for _auto_commit_hook placeholder."""

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_hook_called_on_success(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """_auto_commit_hook should be called after successful execution."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        hook_called: list[str] = []
        original_hook = scheduler._auto_commit_hook

        async def tracking_hook(task: Task, project: Project) -> None:
            hook_called.append(task.id)
            await original_hook(task, project)

        scheduler._auto_commit_hook = tracking_hook  # type: ignore[assignment]

        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        assert hook_called == ["proj:t1"]

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_hook_not_called_on_failure(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """_auto_commit_hook should NOT be called on failed execution."""
        scheduler, task_manager, _eb, _emitted = scheduler_env

        hook_called: list[str] = []

        async def tracking_hook(task: Task, project: Project) -> None:
            hook_called.append(task.id)

        scheduler._auto_commit_hook = tracking_hook  # type: ignore[assignment]

        fail_result = ExecutorResult(
            success=False, exit_code=1, log_lines=[],
            error_summary="fail", duration_seconds=1.0,
        )
        mock_exec = MockExecutor(result=fail_result)
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        assert hook_called == []


# ---------------------------------------------------------------------------
# Tests -- available_slots concurrency limit
# ---------------------------------------------------------------------------


class TestAvailableSlots:
    """Tests for the available_slots concurrency computation."""

    async def test_slots_capped_by_project_count(self, session_factory) -> None:
        """With high global_concurrency_limit, project count caps slots."""
        task_manager = TaskManager(session_factory)

        config = _make_config(
            projects={
                "p1": ProjectConfig(
                    name="P1", repo_path=Path("/tmp/p1"),
                    executor_type=ExecutorType.CODE, max_concurrency=5,
                ),
                "p2": ProjectConfig(
                    name="P2", repo_path=Path("/tmp/p2"),
                    executor_type=ExecutorType.CODE, max_concurrency=5,
                ),
                "p3": ProjectConfig(
                    name="P3", repo_path=Path("/tmp/p3"),
                    executor_type=ExecutorType.CODE, max_concurrency=5,
                ),
            },
            global_limit=10,
        )
        registry = ProjectRegistry(config)
        env_loader = MagicMock()
        env_loader.get_project_env.return_value = {}
        event_bus = EventBus()

        scheduler = Scheduler(config, task_manager, registry, env_loader, event_bus)

        # With 3 projects and global_limit=10, min(10, 3) = 3
        assert scheduler.available_slots == 3

    async def test_slots_reduced_by_running_tasks(self, session_factory) -> None:
        """Running tasks reduce available slots."""
        task_manager = TaskManager(session_factory)

        config = _make_config(
            projects={
                "p1": ProjectConfig(
                    name="P1", repo_path=Path("/tmp/p1"),
                    executor_type=ExecutorType.CODE, max_concurrency=5,
                ),
                "p2": ProjectConfig(
                    name="P2", repo_path=Path("/tmp/p2"),
                    executor_type=ExecutorType.CODE, max_concurrency=5,
                ),
                "p3": ProjectConfig(
                    name="P3", repo_path=Path("/tmp/p3"),
                    executor_type=ExecutorType.CODE, max_concurrency=5,
                ),
            },
            global_limit=10,
        )
        registry = ProjectRegistry(config)
        env_loader = MagicMock()
        env_loader.get_project_env.return_value = {}
        event_bus = EventBus()

        scheduler = Scheduler(config, task_manager, registry, env_loader, event_bus)

        # Simulate 1 running task: min(10, 3) - 1 = 2
        scheduler.running["fake:t1"] = asyncio.create_task(asyncio.sleep(10))
        assert scheduler.available_slots == 2

        # Simulate 3 running tasks -- should be at limit
        scheduler.running["fake:t2"] = asyncio.create_task(asyncio.sleep(10))
        scheduler.running["fake:t3"] = asyncio.create_task(asyncio.sleep(10))
        assert scheduler.available_slots == 0

        # Clean up
        for key in list(scheduler.running):
            scheduler.running[key].cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler.running[key]
        scheduler.running.clear()


# ---------------------------------------------------------------------------
# Tests -- Error details in SSE alerts
# ---------------------------------------------------------------------------


class TestErrorDetailsInAlerts:
    """Tests for structured error info in SSE alerts and execution logs."""

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_error_type_in_alert(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """Alert events include error_type when set."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        fail_result = ExecutorResult(
            success=False,
            exit_code=1,
            log_lines=[],
            error_summary="Build failed",
            error_type=ErrorType.NON_ZERO_EXIT,
            stderr_output="some error text",
            duration_seconds=1.0,
        )
        mock_exec = MockExecutor(result=fail_result)
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        alert_events = [e for e in emitted if e[0] == "alert"]
        assert len(alert_events) >= 1
        alert_data = alert_events[0][2]
        assert alert_data["error_type"] == "non_zero_exit"
        assert "stderr" in alert_data

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_exception_details_in_alert(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """Unhandled exceptions include exception type and message in alert."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        class CrashingExecutor(BaseExecutor):
            """Executor that raises a specific exception."""

            async def execute(self, task, project, env, on_log):
                """Raise ValueError."""
                raise ValueError("missing config key")

            async def cancel(self) -> None:
                """No-op."""

        scheduler._get_executor = lambda _: CrashingExecutor()

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        alert_events = [e for e in emitted if e[0] == "alert"]
        assert len(alert_events) >= 1
        alert_data = alert_events[0][2]
        assert "ValueError" in alert_data["error"]
        assert "missing config key" in alert_data["error"]
        assert alert_data["error_type"] == "infra"

    @patch("src.scheduler.asyncio.sleep", new_callable=AsyncMock)
    async def test_alert_without_error_type(
        self, mock_sleep, scheduler_env,
    ) -> None:
        """Alert without error_type still works (backward compatibility)."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        fail_result = ExecutorResult(
            success=False,
            exit_code=1,
            log_lines=[],
            error_summary="Generic failure",
            duration_seconds=1.0,
        )
        mock_exec = MockExecutor(result=fail_result)
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        alert_events = [e for e in emitted if e[0] == "alert"]
        assert len(alert_events) >= 1
        alert_data = alert_events[0][2]
        assert "Generic failure" in alert_data["error"]
        # No error_type key when not set
        assert "error_type" not in alert_data


# ---------------------------------------------------------------------------
# Tests -- Pause / Resume
# ---------------------------------------------------------------------------


class TestSchedulerPauseResume:
    """Tests for the per-project pause/resume gate."""

    async def test_paused_project_tasks_not_dispatched(
        self, scheduler_env,
    ) -> None:
        """Tick should skip QUEUED tasks for a paused project."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.pause_project("proj")
        await scheduler.tick()

        # No task dispatched
        assert len(mock_exec.calls) == 0
        assert len(scheduler.running) == 0

    async def test_resumed_project_tasks_dispatched(
        self, scheduler_env,
    ) -> None:
        """After resume, tick dispatches QUEUED tasks normally."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.pause_project("proj")
        await scheduler.tick()
        assert len(mock_exec.calls) == 0

        await scheduler.resume_project("proj")
        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        assert len(mock_exec.calls) == 1

    async def test_pause_emits_sse_event(self, scheduler_env) -> None:
        """Pausing should emit an execution_paused SSE event."""
        scheduler, _task_manager, _event_bus, emitted = scheduler_env

        await scheduler.pause_project("proj")

        pause_events = [e for e in emitted if e[0] == "execution_paused"]
        assert len(pause_events) == 1
        assert pause_events[0][1] == "proj"
        assert pause_events[0][2] == {"paused": True}

    async def test_resume_emits_sse_event(self, scheduler_env) -> None:
        """Resuming should emit an execution_paused SSE event with paused=False."""
        scheduler, _task_manager, _event_bus, emitted = scheduler_env

        await scheduler.pause_project("proj")
        await scheduler.resume_project("proj")

        pause_events = [e for e in emitted if e[0] == "execution_paused"]
        assert len(pause_events) == 2
        assert pause_events[1][2] == {"paused": False}

    async def test_is_project_paused(self, scheduler_env) -> None:
        """is_project_paused should reflect the current pause state."""
        scheduler, _task_manager, _event_bus, _emitted = scheduler_env

        assert scheduler.is_project_paused("proj") is False
        await scheduler.pause_project("proj")
        assert scheduler.is_project_paused("proj") is True
        await scheduler.resume_project("proj")
        assert scheduler.is_project_paused("proj") is False

    async def test_in_flight_tasks_continue_when_paused(
        self, scheduler_env,
    ) -> None:
        """In-flight tasks should continue to completion even after pausing."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env
        hang_exec = MockExecutor(hang=True)
        scheduler._get_executor = lambda _: hang_exec

        task = _make_task()
        await task_manager.create_task(task)

        # Start the task
        await scheduler.tick()
        assert len(scheduler.running) == 1

        # Pause the project -- in-flight task should not be cancelled
        await scheduler.pause_project("proj")

        # Release the hanging executor
        hang_exec.release()
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        # Task should have completed successfully
        updated = await task_manager.get_task(task.id)
        assert updated is not None
        assert updated.status == TaskStatus.DONE

    async def test_pause_idempotent(self, scheduler_env) -> None:
        """Pausing twice should not error and emit two events."""
        scheduler, _task_manager, _event_bus, emitted = scheduler_env

        await scheduler.pause_project("proj")
        await scheduler.pause_project("proj")

        pause_events = [e for e in emitted if e[0] == "execution_paused"]
        assert len(pause_events) == 2
        assert scheduler.is_project_paused("proj") is True

    async def test_resume_when_not_paused(self, scheduler_env) -> None:
        """Resuming a non-paused project should not error."""
        scheduler, _task_manager, _event_bus, emitted = scheduler_env

        await scheduler.resume_project("proj")

        resume_events = [
            e for e in emitted
            if e[0] == "execution_paused" and e[2]["paused"] is False
        ]
        assert len(resume_events) == 1
        assert scheduler.is_project_paused("proj") is False

    async def test_pause_persists_with_settings_store(
        self, session_factory,
    ) -> None:
        """Pause state should be persisted to DB via settings_store."""
        from src.project_settings import ProjectSettingsStore

        task_manager = TaskManager(session_factory)
        config = _make_config()
        registry = ProjectRegistry(config)
        env_loader = MagicMock()
        env_loader.get_project_env.return_value = {}
        event_bus = EventBus()
        settings_store = ProjectSettingsStore(session_factory)

        scheduler = Scheduler(
            config=config,
            task_manager=task_manager,
            registry=registry,
            env_loader=env_loader,
            event_bus=event_bus,
            settings_store=settings_store,
        )

        await scheduler.pause_project("proj")

        # Verify DB state
        assert await settings_store.is_paused("proj") is True

        # Create a new scheduler and load state from DB
        scheduler2 = Scheduler(
            config=config,
            task_manager=task_manager,
            registry=registry,
            env_loader=env_loader,
            event_bus=EventBus(),
            settings_store=settings_store,
        )
        await scheduler2.start()
        try:
            assert scheduler2.is_project_paused("proj") is True
        finally:
            await scheduler2.stop()


# ---------------------------------------------------------------------------
# Regression: T-P0-49 -- scheduler state guards
# ---------------------------------------------------------------------------


class TestTimeoutRaceGuards:
    """Regression tests for T-P0-49: scheduler state guards."""

    async def test_success_path_idempotent_when_already_done(
        self, scheduler_env,
    ) -> None:
        """Task already DONE -> scheduler success path fires -> no ValueError.

        Regression: T-P0-49 AC #8 -- if completion fires twice (e.g., timeout
        fires after success), the duplicate DONE transition must be skipped.
        """
        scheduler, task_manager, _event_bus, emitted = scheduler_env
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        # Task should be DONE
        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.DONE

        # Now simulate the success path firing again by calling _execute_task
        # directly with a mock that returns success -- should NOT raise
        task2 = await task_manager.get_task("proj:t1")
        assert task2 is not None
        # Manually set back to RUNNING for the test setup, then call
        # update_status(DONE) to put it back.  But the point is: if the task
        # is already DONE, the success path should be idempotent.
        # We test this by directly invoking the guarded code path:
        # Re-fetch, confirm DONE, ensure no crash.
        current = await task_manager.get_task("proj:t1")
        assert current is not None
        assert current.status == TaskStatus.DONE

        # Simulate: the guard in the success path should detect DONE
        # and skip the transition.  We verify this by checking that a second
        # execution with success=True doesn't crash.
        task_for_exec = _make_task(task_id="proj:t2", local_task_id="t2")
        await task_manager.create_task(task_for_exec)

        # Pre-transition to DONE manually to simulate the race
        await task_manager.update_status("proj:t2", TaskStatus.RUNNING)
        await task_manager.update_status("proj:t2", TaskStatus.DONE)

        # Now run _execute_task with a successful executor
        # The guard should see it's already DONE and skip
        from src.models import Project
        proj = Project(
            id="proj",
            name="Test Project",
            repo_path="/tmp/test-repo",
            executor_type=ExecutorType.CODE,
        )
        scheduler._executors["proj:t2"] = mock_exec
        scheduler.running["proj:t2"] = asyncio.current_task()  # placeholder
        await scheduler._execute_task(mock_exec, task_for_exec, proj)

        # Should still be DONE, no ValueError raised
        final = await task_manager.get_task("proj:t2")
        assert final is not None
        assert final.status == TaskStatus.DONE

    async def test_failure_path_skips_when_task_not_running(
        self, scheduler_env,
    ) -> None:
        """Failure path skips FAILED transition when task already left RUNNING.

        Regression: T-P0-49 AC #2 -- verify task is still RUNNING before
        RUNNING->FAILED transition.
        """
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        # Create task that will fail
        fail_result = ExecutorResult(
            success=False,
            exit_code=1,
            log_lines=["error"],
            error_summary="Execution failed",
            duration_seconds=1.0,
        )
        mock_exec = MockExecutor(result=fail_result)
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task(task_id="proj:t3", local_task_id="t3")
        await task_manager.create_task(task)

        # Transition to RUNNING, then immediately to DONE (simulating
        # the completion path winning the race)
        await task_manager.update_status("proj:t3", TaskStatus.RUNNING)
        await task_manager.update_status("proj:t3", TaskStatus.DONE)

        # Now run _execute_task with the failing executor
        # The guard should see it's DONE (not RUNNING) and skip FAILED
        from src.models import Project
        proj = Project(
            id="proj",
            name="Test Project",
            repo_path="/tmp/test-repo",
            executor_type=ExecutorType.CODE,
        )
        scheduler._executors["proj:t3"] = mock_exec
        scheduler.running["proj:t3"] = asyncio.current_task()
        await scheduler._execute_task(mock_exec, task, proj)

        # Should still be DONE -- NOT FAILED or BLOCKED
        final = await task_manager.get_task("proj:t3")
        assert final is not None
        assert final.status == TaskStatus.DONE


# ---------------------------------------------------------------------------
# Tests -- Immediate next-task dispatch (T-P0-52)
# ---------------------------------------------------------------------------


class TestImmediateDispatch:
    """Tests for immediate tick dispatch after task completion."""

    async def test_next_task_dispatched_immediately(
        self, scheduler_env,
    ) -> None:
        """After task A completes, queued task B should be dispatched
        within <1 second (not waiting for the 5s periodic tick)."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        # Track execution order and timing
        executed: list[tuple[str, float]] = []
        import time

        class TimingExecutor(BaseExecutor):
            """Executor that records execution times."""

            async def execute(
                self, task: Task, project: Project,
                env: dict[str, str],
                on_log: Callable[[str], None],
            ) -> ExecutorResult:
                """Record task execution time."""
                executed.append((task.id, time.monotonic()))
                on_log("done")
                return ExecutorResult(
                    success=True, exit_code=0,
                    log_lines=["done"], duration_seconds=0.01,
                )

            async def cancel(self) -> None:
                """No-op."""

        scheduler._get_executor = lambda _: TimingExecutor()

        # concurrency=1 for the project, so only one at a time
        task_a = _make_task(task_id="proj:a", local_task_id="a", title="Task A")
        task_b = _make_task(task_id="proj:b", local_task_id="b", title="Task B")
        await task_manager.create_task(task_a)
        await task_manager.create_task(task_b)

        # Dispatch first task via tick
        await scheduler.tick()

        # Wait for both tasks to complete (task B should be dispatched
        # immediately after task A finishes via the finally-block tick)
        for _ in range(50):
            await asyncio.sleep(0.05)
            if len(executed) >= 2:
                break

        assert len(executed) == 2, (
            f"Expected 2 executions but got {len(executed)}"
        )
        assert executed[0][0] == "proj:a"
        assert executed[1][0] == "proj:b"

        # Task B should start within 1 second of task A
        gap = executed[1][1] - executed[0][1]
        assert gap < 1.0, (
            f"Task B started {gap:.2f}s after A -- should be <1s"
        )

    async def test_slot_freed_dispatches_next(
        self, scheduler_env,
    ) -> None:
        """When all slots are full and one task completes, the next queued
        task should be dispatched immediately."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        release_event = asyncio.Event()
        dispatched: list[str] = []

        class SlotExecutor(BaseExecutor):
            """Executor that blocks task A until released."""

            async def execute(
                self, task: Task, project: Project,
                env: dict[str, str],
                on_log: Callable[[str], None],
            ) -> ExecutorResult:
                """Block on task A, return immediately for others."""
                dispatched.append(task.id)
                on_log("started")
                if task.id == "proj:a":
                    await release_event.wait()
                return ExecutorResult(
                    success=True, exit_code=0,
                    log_lines=["done"], duration_seconds=0.01,
                )

            async def cancel(self) -> None:
                """No-op."""

        scheduler._get_executor = lambda _: SlotExecutor()

        # Project max_concurrency=1, so only one task at a time
        task_a = _make_task(task_id="proj:a", local_task_id="a", title="Task A")
        task_b = _make_task(task_id="proj:b", local_task_id="b", title="Task B")
        await task_manager.create_task(task_a)
        await task_manager.create_task(task_b)

        # Tick dispatches task A (blocks)
        await scheduler.tick()
        await asyncio.sleep(0.05)

        assert "proj:a" in dispatched
        assert "proj:b" not in dispatched

        # Release task A -> should immediately dispatch task B
        release_event.set()

        for _ in range(50):
            await asyncio.sleep(0.05)
            if "proj:b" in dispatched:
                break

        assert "proj:b" in dispatched, (
            "Task B should have been dispatched after task A completed"
        )

    async def test_concurrent_completions_no_duplicate_dispatch(
        self, scheduler_env,
    ) -> None:
        """Two tasks completing nearly simultaneously should not cause
        duplicate execution of the same queued task."""
        # Use global_limit=2 so two tasks run concurrently
        scheduler, task_manager, _event_bus, emitted = scheduler_env
        config = _make_config(global_limit=3)
        registry = ProjectRegistry(config)

        # Override project concurrency to allow 2
        proj_config = config.projects["proj"]
        proj_config.max_concurrency = 2

        scheduler._config = config
        scheduler._registry = registry

        execution_count: dict[str, int] = {}
        release_event = asyncio.Event()

        class CountingExecutor(BaseExecutor):
            """Executor that counts how many times each task is executed."""

            async def execute(
                self, task: Task, project: Project,
                env: dict[str, str],
                on_log: Callable[[str], None],
            ) -> ExecutorResult:
                """Count execution per task."""
                execution_count[task.id] = execution_count.get(task.id, 0) + 1
                on_log("done")
                if task.id in ("proj:a", "proj:b"):
                    await release_event.wait()
                return ExecutorResult(
                    success=True, exit_code=0,
                    log_lines=["done"], duration_seconds=0.01,
                )

            async def cancel(self) -> None:
                """No-op."""

        scheduler._get_executor = lambda _: CountingExecutor()

        # Create 3 tasks: a and b run concurrently, c waits
        for tid in ("a", "b", "c"):
            t = _make_task(task_id=f"proj:{tid}", local_task_id=tid, title=f"Task {tid}")
            await task_manager.create_task(t)

        # Dispatch a and b
        await scheduler.tick()
        await asyncio.sleep(0.05)

        # Release both simultaneously -> both trigger immediate ticks
        release_event.set()

        for _ in range(50):
            await asyncio.sleep(0.05)
            if execution_count.get("proj:c", 0) >= 1:
                break

        # Task c should be executed exactly once (lock prevents double dispatch)
        assert execution_count.get("proj:c", 0) == 1, (
            f"Task C executed {execution_count.get('proj:c', 0)} times, expected 1"
        )

    async def test_tick_exception_releases_lock(
        self, scheduler_env,
    ) -> None:
        """If tick() throws an exception, the lock should be released
        and subsequent ticks should still execute normally."""
        scheduler, task_manager, _event_bus, emitted = scheduler_env

        call_count = 0
        original_get_ready = task_manager.get_ready_tasks

        async def exploding_get_ready(limit: int = 10) -> list[Task]:
            """Raise on first call, then work normally."""
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated DB error")
            return await original_get_ready(limit=limit)

        task_manager.get_ready_tasks = exploding_get_ready

        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task()
        await task_manager.create_task(task)

        # First tick should raise inside the lock
        with pytest.raises(RuntimeError, match="Simulated DB error"):
            await scheduler.tick()

        # Lock should be released -- second tick should work
        await scheduler.tick()

        # Wait for execution to complete
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        assert len(mock_exec.calls) == 1
        updated = await task_manager.get_task("proj:t1")
        assert updated is not None
        assert updated.status == TaskStatus.DONE
