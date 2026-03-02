"""Scheduler -- tick-based task execution with concurrency control.

Implements the core scheduling loop from PRD Section 8: periodically checks
for QUEUED tasks, verifies dependencies and concurrency limits, and dispatches
executions via the appropriate executor.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from src.config import OrchestratorConfig, ProjectRegistry
from src.env_loader import EnvLoader
from src.events import EventBus
from src.executors.base import BaseExecutor
from src.executors.code_executor import CodeExecutor
from src.models import ExecutorType, Project, Task, TaskStatus
from src.task_manager import TaskManager

logger = logging.getLogger(__name__)

TICK_INTERVAL_SECONDS = 5


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
    # Task execution
    # ------------------------------------------------------------------

    async def _execute_task(
        self,
        executor: BaseExecutor,
        task: Task,
        project: Project,
    ) -> None:
        """Execute a task and handle success/failure.

        On success: status -> DONE, emit ``status_change`` event.
        On failure: status -> FAILED, emit ``alert`` event.

        Args:
            executor: The executor to run the task with.
            task: The task to execute.
            project: The project this task belongs to.
        """
        try:
            env = self._env_loader.get_project_env(project)
            result = await executor.execute(
                task,
                project,
                env=env,
                on_log=lambda line: self._event_bus.emit("log", task.id, line),
            )

            if result.success:
                await self._task_manager.update_status(
                    task.id, TaskStatus.DONE
                )
                self._event_bus.emit(
                    "status_change", task.id, {"status": "done"}
                )
            else:
                await self._task_manager.update_status(
                    task.id, TaskStatus.FAILED
                )
                self._event_bus.emit(
                    "alert",
                    task.id,
                    {"error": result.error_summary or "Execution failed"},
                )
        except Exception:
            logger.exception("Unhandled error executing task %s", task.id)
            with contextlib.suppress(ValueError):
                await self._task_manager.update_status(
                    task.id, TaskStatus.FAILED
                )
            self._event_bus.emit(
                "alert", task.id, {"error": "Unhandled execution error"}
            )
        finally:
            self.running.pop(task.id, None)
