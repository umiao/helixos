"""Scheduler -- tick-based task execution with concurrency control.

Implements the core scheduling loop from PRD Section 8: periodically checks
for QUEUED tasks, verifies dependencies and concurrency limits, and dispatches
executions via the appropriate executor.  Includes retry with exponential
backoff, startup crash recovery, and task cancellation.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from src.config import OrchestratorConfig, ProjectRegistry
from src.env_loader import EnvLoader
from src.events import EventBus
from src.executors.base import BaseExecutor, ExecutorResult
from src.executors.code_executor import CodeExecutor
from src.git_ops import GitOps
from src.models import ExecutorType, Project, Task, TaskStatus
from src.task_manager import TaskManager

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 5
RETRY_BACKOFF_SECONDS: list[int] = [30, 60, 120]


class Scheduler:
    """Core scheduler that dispatches task executions.

    Periodically calls ``tick()`` to check for QUEUED tasks with fulfilled
    dependencies, respects per-project and global concurrency limits, and
    dispatches executions through the appropriate executor.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        task_manager: TaskManager,
        registry: ProjectRegistry,
        env_loader: EnvLoader,
        event_bus: EventBus,
    ) -> None:
        """Initialize the scheduler with all required service dependencies.

        Args:
            config: Top-level orchestrator configuration.
            task_manager: Task CRUD and state machine manager.
            registry: Project registry for looking up projects.
            env_loader: Environment variable loader.
            event_bus: Event bus for emitting real-time events.
        """
        self._config = config
        self._task_manager = task_manager
        self._registry = registry
        self._env_loader = env_loader
        self._event_bus = event_bus
        self.running: dict[str, asyncio.Task[None]] = {}
        self._executors: dict[str, BaseExecutor] = {}
        self._cancelled: set[str] = set()
        self._tick_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Tick loop lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background tick loop via asyncio.create_task."""
        self._tick_task = asyncio.create_task(self._tick_loop())
        logger.info("Scheduler started (interval=%ds)", TICK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Stop the tick loop and wait for it to finish."""
        if self._tick_task is not None:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None
        logger.info("Scheduler stopped")

    async def _tick_loop(self) -> None:
        """Run tick() every TICK_INTERVAL_SECONDS until cancelled."""
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Error in scheduler tick")
            await asyncio.sleep(TICK_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # Startup recovery
    # ------------------------------------------------------------------

    async def startup_recovery(self) -> int:
        """Mark all RUNNING tasks as FAILED on boot (crash recovery).

        Emits an alert for each recovered task and logs a warning with
        the count of orphaned tasks.

        Returns:
            The number of orphaned tasks recovered.
        """
        running_tasks = await self._task_manager.list_tasks(
            status=TaskStatus.RUNNING,
        )
        count = await self._task_manager.mark_running_as_failed()

        for task in running_tasks:
            self._event_bus.emit(
                "alert",
                task.id,
                {"error": "Recovered from crash -- was RUNNING when process exited"},
            )

        if count > 0:
            logger.warning(
                "Startup recovery: %d orphaned tasks marked FAILED", count,
            )

        return count

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task execution.

        Calls executor.cancel() on the running task, updates status to
        FAILED, and removes from self.running.

        Args:
            task_id: The task to cancel.

        Returns:
            True if the task was running and cancelled, False otherwise.
        """
        if task_id not in self.running:
            return False

        self._cancelled.add(task_id)

        # Cancel the executor (terminates subprocess)
        executor = self._executors.get(task_id)
        if executor is not None:
            await executor.cancel()

        # Cancel the asyncio task (interrupts retry backoff sleep)
        asyncio_task = self.running.get(task_id)
        if asyncio_task is not None:
            asyncio_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio_task

        # Update status and emit events (the _execute_task CancelledError
        # handler may not have been able to do async DB work)
        with contextlib.suppress(ValueError):
            await self._task_manager.update_status(
                task_id, TaskStatus.FAILED,
            )
        self._event_bus.emit(
            "alert", task_id, {"error": "Task cancelled"},
        )

        # Ensure cleanup (idempotent with _execute_task's finally block)
        self.running.pop(task_id, None)
        self._executors.pop(task_id, None)
        self._cancelled.discard(task_id)

        return True

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        """Main scheduling loop iteration (called every 5s).

        Checks available concurrency slots, fetches QUEUED tasks, verifies
        per-project concurrency and dependency constraints, and dispatches
        executions.
        """
        slots = self.available_slots
        if slots <= 0:
            return

        candidates = await self._task_manager.get_ready_tasks(limit=slots)

        for task in candidates:
            if self.available_slots <= 0:
                break

            if await self._project_is_busy(task.project_id):
                continue

            if not await self._deps_fulfilled(task):
                continue

            try:
                project = self._registry.get_project(task.project_id)
            except KeyError:
                logger.warning(
                    "Unknown project %s for task %s", task.project_id, task.id
                )
                continue

            executor = self._get_executor(project.executor_type)

            await self._task_manager.update_status(task.id, TaskStatus.RUNNING)
            self._event_bus.emit(
                "status_change", task.id, {"status": "running"}
            )

            self.running[task.id] = asyncio.create_task(
                self._execute_task(executor, task, project)
            )

    # ------------------------------------------------------------------
    # Concurrency control
    # ------------------------------------------------------------------

    async def _project_is_busy(self, project_id: str) -> bool:
        """Check if a project has reached its per-project concurrency limit.

        Args:
            project_id: The project to check.

        Returns:
            True if no more tasks can be launched for this project.
        """
        try:
            project = self._registry.get_project(project_id)
        except KeyError:
            return True

        if project.max_concurrency == 0:
            return True  # Manual-only project

        running_count = await self._task_manager.count_running_by_project(
            project_id
        )
        return running_count >= project.max_concurrency

    @property
    def available_slots(self) -> int:
        """Number of additional tasks that can be launched.

        Computed as ``min(global_limit, active_projects) - len(running)``.
        """
        active_projects = len(self._registry.list_projects())
        limit = min(
            self._config.orchestrator.global_concurrency_limit,
            active_projects,
        )
        return max(0, limit - len(self.running))

    # ------------------------------------------------------------------
    # Dependency checking
    # ------------------------------------------------------------------

    async def _deps_fulfilled(self, task: Task) -> bool:
        """Check if all upstream dependencies for *task* are DONE.

        Args:
            task: The task whose dependencies to check.

        Returns:
            True if all dependencies are satisfied (or there are none).
        """
        for dep_id in task.depends_on:
            dep = await self._task_manager.get_task(dep_id)
            if dep is None or dep.status != TaskStatus.DONE:
                return False
        return True

    # ------------------------------------------------------------------
    # Executor factory
    # ------------------------------------------------------------------

    def _get_executor(self, executor_type: ExecutorType) -> BaseExecutor:
        """Return the appropriate executor for the given type.

        For MVP, all types return a ``CodeExecutor``. ``AgentExecutor``
        and ``ScheduledExecutor`` will be added in Phase 2.

        Args:
            executor_type: The type of executor requested.

        Returns:
            A BaseExecutor instance.
        """
        return CodeExecutor(self._config.orchestrator)

    # ------------------------------------------------------------------
    # Task execution with retry
    # ------------------------------------------------------------------

    async def _execute_task(
        self,
        executor: BaseExecutor,
        task: Task,
        project: Project,
    ) -> None:
        """Execute a task with retry logic and handle success/failure.

        On success: status -> DONE, emit ``status_change``, call auto-commit hook.
        On cancel: status -> FAILED, emit ``alert``.
        On max retries exhausted: status -> FAILED -> BLOCKED, emit ``alert``.

        Args:
            executor: The executor to run the task with.
            task: The task to execute.
            project: The project this task belongs to.
        """
        self._executors[task.id] = executor
        try:
            env = self._env_loader.get_project_env(project)

            def on_log(line: str) -> None:
                self._event_bus.emit("log", task.id, line)
            result = await self._run_with_retry(
                executor, task, project, env, on_log,
            )

            if result.success:
                await self._task_manager.update_status(
                    task.id, TaskStatus.DONE,
                )
                self._event_bus.emit(
                    "status_change", task.id, {"status": "done"},
                )
                await self._auto_commit_hook(task, project)
            elif task.id in self._cancelled:
                # Cancelled by user -- just FAILED, not BLOCKED
                with contextlib.suppress(ValueError):
                    await self._task_manager.update_status(
                        task.id, TaskStatus.FAILED,
                    )
                self._event_bus.emit(
                    "alert", task.id, {"error": "Task cancelled"},
                )
            else:
                # All retries exhausted -> FAILED -> BLOCKED
                await self._task_manager.update_status(
                    task.id, TaskStatus.FAILED,
                )
                self._event_bus.emit(
                    "alert",
                    task.id,
                    {"error": result.error_summary or "Max retries exhausted"},
                )
                await self._task_manager.update_status(
                    task.id, TaskStatus.BLOCKED,
                )
                self._event_bus.emit(
                    "status_change", task.id, {"status": "blocked"},
                )
        except Exception:
            logger.exception("Unhandled error executing task %s", task.id)
            with contextlib.suppress(ValueError):
                await self._task_manager.update_status(
                    task.id, TaskStatus.FAILED,
                )
            self._event_bus.emit(
                "alert", task.id, {"error": "Unhandled execution error"},
            )
        finally:
            self.running.pop(task.id, None)
            self._executors.pop(task.id, None)
            self._cancelled.discard(task.id)

    async def _run_with_retry(
        self,
        executor: BaseExecutor,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
    ) -> ExecutorResult:
        """Execute with exponential backoff retry.

        Retries up to 3 times with backoff of 30s, 60s, 120s.
        Stops retrying if the task has been cancelled.

        Args:
            executor: The executor to run the task with.
            task: The task to execute.
            project: The project this task belongs to.
            env: Environment variables for the execution.
            on_log: Callback for log output.

        Returns:
            The final ExecutorResult (success or last failure).
        """
        result = await executor.execute(task, project, env=env, on_log=on_log)

        if result.success:
            return result

        for attempt, delay in enumerate(RETRY_BACKOFF_SECONDS, 1):
            if task.id in self._cancelled:
                return result

            self._event_bus.emit(
                "log",
                task.id,
                f"Retry {attempt}/{len(RETRY_BACKOFF_SECONDS)} "
                f"after {delay}s backoff",
            )

            await asyncio.sleep(delay)

            if task.id in self._cancelled:
                return result

            # Create fresh executor for retry
            executor = self._get_executor(project.executor_type)
            self._executors[task.id] = executor
            result = await executor.execute(
                task, project, env=env, on_log=on_log,
            )

            if result.success:
                return result

        return result

    # ------------------------------------------------------------------
    # Auto-commit hook
    # ------------------------------------------------------------------

    async def _auto_commit_hook(
        self, task: Task, project: Project,
    ) -> None:
        """Run git auto-commit after successful task execution.

        Calls ``GitOps.auto_commit()`` with the project's git config.
        Errors are logged but never propagated -- the task is already
        DONE and git failures must not affect task status.

        Args:
            task: The successfully completed task.
            project: The project this task belongs to.
        """
        try:
            ok = await GitOps.auto_commit(
                project=project,
                task=task,
                config=self._config.git,
                event_bus=self._event_bus,
            )
            if not ok:
                logger.warning(
                    "Auto-commit returned False for task %s", task.id,
                )
        except Exception:
            logger.exception(
                "Auto-commit failed for task %s (ignored)", task.id,
            )
