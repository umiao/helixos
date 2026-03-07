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
import traceback
import uuid
from collections.abc import Callable

from src.config import OrchestratorConfig, ProjectRegistry
from src.env_loader import EnvLoader
from src.events import EventBus
from src.executors.base import BaseExecutor, ErrorType, ExecutorResult
from src.executors.code_executor import CodeExecutor
from src.git_ops import GitOps
from src.history_writer import HistoryWriter
from src.models import ExecutorType, Project, Task, TaskStatus
from src.project_settings import ProjectSettingsStore
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
        history_writer: HistoryWriter | None = None,
        settings_store: ProjectSettingsStore | None = None,
    ) -> None:
        """Initialize the scheduler with all required service dependencies.

        Args:
            config: Top-level orchestrator configuration.
            task_manager: Task CRUD and state machine manager.
            registry: Project registry for looking up projects.
            env_loader: Environment variable loader.
            event_bus: Event bus for emitting real-time events.
            history_writer: Optional DB-first log/review writer.
            settings_store: Optional DB-backed project settings.
        """
        self._config = config
        self._task_manager = task_manager
        self._registry = registry
        self._env_loader = env_loader
        self._event_bus = event_bus
        self._history_writer = history_writer
        self._settings_store = settings_store
        self.running: dict[str, asyncio.Task[None]] = {}
        self._executors: dict[str, BaseExecutor] = {}
        self._cancelled: set[str] = set()
        self._tick_task: asyncio.Task[None] | None = None
        self._tick_lock = asyncio.Lock()
        self._background_ticks: set[asyncio.Task[None]] = set()
        self._stopped: bool = False
        self._paused_projects: set[str] = set()
        # Projects where review gate is disabled (default: enabled for all)
        self._review_gate_disabled: set[str] = set()

    # ------------------------------------------------------------------
    # Tick loop lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background tick loop via asyncio.create_task.

        Loads persisted pause states from DB before starting the loop.
        """
        self._stopped = False
        if self._settings_store is not None:
            self._paused_projects = await self._settings_store.get_all_paused()
            if self._paused_projects:
                logger.info(
                    "Loaded %d paused projects: %s",
                    len(self._paused_projects),
                    ", ".join(sorted(self._paused_projects)),
                )
            self._review_gate_disabled = (
                await self._settings_store.get_all_review_gate_disabled()
            )
            if self._review_gate_disabled:
                logger.info(
                    "Review gate disabled for %d projects: %s",
                    len(self._review_gate_disabled),
                    ", ".join(sorted(self._review_gate_disabled)),
                )
        self._tick_task = asyncio.create_task(self._tick_loop())
        logger.info("Scheduler started (interval=%ds)", TICK_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Stop the tick loop, cancel background ticks, and wait for cleanup."""
        self._stopped = True
        if self._tick_task is not None:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
            self._tick_task = None
        for t in self._background_ticks:
            t.cancel()
        if self._background_ticks:
            await asyncio.gather(*self._background_ticks, return_exceptions=True)
            self._background_ticks.clear()
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
                origin="scheduler",
            )

        if count > 0:
            logger.warning(
                "Startup recovery: %d orphaned tasks marked FAILED", count,
            )

        return count

    # ------------------------------------------------------------------
    # Pause / Resume
    # ------------------------------------------------------------------

    async def pause_project(self, project_id: str) -> None:
        """Pause execution for a project.

        New tasks will not be dispatched.  In-flight tasks continue.
        The state is persisted to DB so it survives restarts.

        Args:
            project_id: The project to pause.
        """
        self._paused_projects.add(project_id)
        if self._settings_store is not None:
            await self._settings_store.set_paused(project_id, paused=True)
        self._event_bus.emit(
            "execution_paused", project_id, {"paused": True},
            origin="scheduler",
        )
        logger.info("Execution paused for project %s", project_id)

    async def resume_project(self, project_id: str) -> None:
        """Resume execution for a project.

        Previously-paused project can dispatch tasks again on the next tick.
        The state is persisted to DB so it survives restarts.

        Args:
            project_id: The project to resume.
        """
        self._paused_projects.discard(project_id)
        if self._settings_store is not None:
            await self._settings_store.set_paused(project_id, paused=False)
        self._event_bus.emit(
            "execution_paused", project_id, {"paused": False},
            origin="scheduler",
        )
        logger.info("Execution resumed for project %s", project_id)

    def is_project_paused(self, project_id: str) -> bool:
        """Check whether execution is paused for *project_id*.

        Args:
            project_id: The project to check.

        Returns:
            True if the project is paused.
        """
        return project_id in self._paused_projects

    # ------------------------------------------------------------------
    # Review gate
    # ------------------------------------------------------------------

    async def enable_review_gate(self, project_id: str) -> None:
        """Enable the review gate for a project.

        When enabled, BACKLOG -> QUEUED is blocked (Layer 1) and the
        scheduler refuses to execute tasks without an approved review
        record (Layer 2).

        Args:
            project_id: The project to enable the gate for.
        """
        self._review_gate_disabled.discard(project_id)
        if self._settings_store is not None:
            await self._settings_store.set_review_gate(
                project_id, enabled=True,
            )
        self._event_bus.emit(
            "review_gate_changed", project_id,
            {"review_gate_enabled": True},
            origin="scheduler",
        )
        logger.info("Review gate enabled for project %s", project_id)

    async def disable_review_gate(self, project_id: str) -> None:
        """Disable the review gate for a project.

        When disabled, tasks can skip review and go directly from
        BACKLOG to QUEUED.

        Args:
            project_id: The project to disable the gate for.
        """
        self._review_gate_disabled.add(project_id)
        if self._settings_store is not None:
            await self._settings_store.set_review_gate(
                project_id, enabled=False,
            )
        self._event_bus.emit(
            "review_gate_changed", project_id,
            {"review_gate_enabled": False},
            origin="scheduler",
        )
        logger.info("Review gate disabled for project %s", project_id)

    def is_review_gate_enabled(self, project_id: str) -> bool:
        """Check whether the review gate is enabled for *project_id*.

        Args:
            project_id: The project to check.

        Returns:
            True if the review gate is enabled (default).
        """
        return project_id not in self._review_gate_disabled

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
            origin="scheduler",
        )

        # Ensure cleanup (idempotent with _execute_task's finally block)
        self.running.pop(task_id, None)
        self._executors.pop(task_id, None)
        self._cancelled.discard(task_id)

        return True

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    async def _safe_tick(self) -> None:
        """Wrap tick() so fire-and-forget background calls never raise."""
        try:
            await self.tick()
        except Exception:
            if not self._stopped:
                logger.exception("Error in background tick")

    async def tick(self) -> None:
        """Main scheduling loop iteration (called every 5s or on task completion).

        Checks available concurrency slots, fetches QUEUED tasks, verifies
        per-project concurrency and dependency constraints, and dispatches
        executions.  Uses ``_tick_lock`` for re-entrancy safety so that
        concurrent tick calls (periodic + immediate post-completion) do not
        race.
        """
        async with self._tick_lock:
            slots = self.available_slots
            if slots <= 0:
                return

            candidates = await self._task_manager.get_ready_tasks(limit=slots)

            for task in candidates:
                if self.available_slots <= 0:
                    break

                if task.project_id in self._paused_projects:
                    continue

                if await self._project_is_busy(task.project_id):
                    continue

                if not await self._deps_fulfilled(task):
                    continue

                # Layer 2: review gate -- refuse to execute unreviewed tasks
                if not await self._can_execute(task):
                    continue

                try:
                    project = self._registry.get_project(task.project_id)
                except KeyError:
                    logger.warning(
                        "Unknown project %s for task %s", task.project_id, task.id
                    )
                    continue

                executor = self._get_executor(project.executor_type)

                epoch_id = uuid.uuid4().hex
                await self._task_manager.update_status(task.id, TaskStatus.RUNNING)
                await self._task_manager.set_execution_epoch(task.id, epoch_id)
                self._event_bus.emit(
                    "status_change", task.id, {"status": "running"},
                    origin="scheduler",
                )

                self.running[task.id] = asyncio.create_task(
                    self._execute_task(executor, task, project, epoch_id)
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
    # Layer 2: review gate execution check
    # ------------------------------------------------------------------

    async def _can_execute(self, task: Task) -> bool:
        """Layer 2 review gate: verify the task has been reviewed.

        When the review gate is enabled for the task's project, this
        checks that at least one approved review record exists in
        review_history.  This is the last line of defense -- even if
        some code path bypasses the transition gate, the scheduler will
        not execute an unreviewed task.

        When the review gate is disabled, always returns True.

        Args:
            task: The QUEUED task about to be executed.

        Returns:
            True if the task may proceed to execution.
        """
        if task.project_id in self._review_gate_disabled:
            return True

        if self._history_writer is None:
            # No history writer means we cannot verify review status;
            # allow execution to avoid deadlocking the pipeline.
            return True

        approved = await self._history_writer.has_approved_review(task.id)
        if not approved:
            logger.info(
                "Review gate: task %s has no approved review -- skipping",
                task.id,
            )
        return approved

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
        epoch_id: str = "",
    ) -> None:
        """Execute a task with retry logic and handle success/failure.

        On success: status -> DONE, emit ``status_change``, call auto-commit hook.
        On cancel: status -> FAILED, emit ``alert``.
        On max retries exhausted: status -> FAILED -> BLOCKED, emit ``alert``.

        The *epoch_id* identifies this specific execution attempt.  Before
        any finalization transition the scheduler verifies the epoch still
        matches the DB row.  A mismatch means another path (user drag,
        concurrent request) has taken ownership and the transition is skipped.

        Args:
            executor: The executor to run the task with.
            task: The task to execute.
            project: The project this task belongs to.
            epoch_id: Unique identifier for this execution attempt.
        """
        self._executors[task.id] = executor
        try:
            env = self._env_loader.get_project_env(project)

            def on_log(line: str) -> None:
                self._event_bus.emit("log", task.id, line, origin="execution")
                if self._history_writer is not None:
                    asyncio.ensure_future(
                        self._history_writer.write_log(task.id, line),
                    )

            def on_stream_event(event_dict: dict) -> None:
                self._event_bus.emit(
                    "execution_stream", task.id, event_dict,
                    origin="execution",
                )

            # DB-first: log execution start
            if self._history_writer is not None:
                await self._history_writer.write_log(
                    task.id, "Execution started", level="info", source="scheduler",
                )

            result = await self._run_with_retry(
                executor, task, project, env, on_log, on_stream_event,
            )

            if result.success:
                if self._history_writer is not None:
                    await self._history_writer.write_log(
                        task.id, "Execution completed successfully",
                        level="info", source="scheduler",
                    )
                # Epoch guard: verify this execution attempt still owns
                # the task.  If another path (user drag, concurrent request)
                # has taken ownership, skip the transition.
                if epoch_id and not await self._task_manager.verify_execution_epoch(
                    task.id, epoch_id,
                ):
                    logger.warning(
                        "Epoch mismatch for task %s (epoch=%s), "
                        "skipping DONE transition",
                        task.id, epoch_id,
                    )
                    return

                # Idempotent guard: re-fetch status before transitioning.
                # If timeout path already moved to DONE, skip the duplicate
                # transition instead of crashing with ValueError.
                current = await self._task_manager.get_task(task.id)
                if current is not None and current.status != TaskStatus.DONE:
                    await self._task_manager.update_status(
                        task.id, TaskStatus.DONE,
                    )
                elif current is not None:
                    logger.warning(
                        "Task %s already DONE, skipping duplicate transition",
                        task.id,
                    )
                self._event_bus.emit(
                    "status_change", task.id, {"status": "done"},
                    origin="execution",
                )
                await self._auto_commit_hook(task, project)
            elif task.id in self._cancelled:
                if self._history_writer is not None:
                    await self._history_writer.write_log(
                        task.id, "Task cancelled by user",
                        level="warn", source="scheduler",
                    )
                # Cancelled by user -- just FAILED, not BLOCKED
                with contextlib.suppress(ValueError):
                    await self._task_manager.update_status(
                        task.id, TaskStatus.FAILED,
                    )
                self._event_bus.emit(
                    "alert", task.id, {"error": "Task cancelled"},
                    origin="execution",
                )
            else:
                error_msg = result.error_summary or "Max retries exhausted"
                alert_data: dict[str, object] = {"error": error_msg}
                if result.error_type is not None:
                    alert_data["error_type"] = result.error_type.value
                if result.stderr_output:
                    alert_data["stderr"] = result.stderr_output[:500]

                if self._history_writer is not None:
                    log_msg = f"Execution failed: {error_msg}"
                    if result.error_type is not None:
                        log_msg = f"[{result.error_type.value}] {log_msg}"
                    await self._history_writer.write_log(
                        task.id, log_msg,
                        level="error", source="scheduler",
                    )
                    # Store full stderr in log DB if available
                    if result.stderr_output:
                        await self._history_writer.write_log(
                            task.id, f"stderr: {result.stderr_output}",
                            level="error", source="executor",
                        )

                # Epoch guard: verify this execution attempt still owns
                # the task before transitioning to FAILED.
                if epoch_id and not await self._task_manager.verify_execution_epoch(
                    task.id, epoch_id,
                ):
                    logger.warning(
                        "Epoch mismatch for task %s (epoch=%s), "
                        "skipping FAILED transition",
                        task.id, epoch_id,
                    )
                    return

                # State guard: verify task is still RUNNING before
                # transitioning to FAILED.  If the completion path already
                # moved it to DONE, skip the failure transition.
                current = await self._task_manager.get_task(task.id)
                if current is None or current.status != TaskStatus.RUNNING:
                    logger.warning(
                        "Task %s no longer RUNNING (status=%s), "
                        "skipping FAILED transition",
                        task.id,
                        current.status if current else "deleted",
                    )
                else:
                    # All retries exhausted -> FAILED -> BLOCKED
                    await self._task_manager.update_status(
                        task.id, TaskStatus.FAILED,
                    )
                    self._event_bus.emit(
                        "alert",
                        task.id,
                        alert_data,
                        origin="execution",
                    )
                    await self._task_manager.update_status(
                        task.id, TaskStatus.BLOCKED,
                    )
                    self._event_bus.emit(
                        "status_change", task.id, {"status": "blocked"},
                        origin="execution",
                    )
        except Exception as exc:
            exc_type = type(exc).__name__
            exc_msg = str(exc)
            tb_str = traceback.format_exc()
            logger.exception("Unhandled error executing task %s", task.id)

            error_detail = f"{exc_type}: {exc_msg}"
            if self._history_writer is not None:
                with contextlib.suppress(Exception):
                    await self._history_writer.write_log(
                        task.id,
                        f"[{ErrorType.INFRA.value}] Unhandled error: {error_detail}",
                        level="error", source="scheduler",
                    )
                with contextlib.suppress(Exception):
                    await self._history_writer.write_log(
                        task.id, f"Traceback:\n{tb_str}",
                        level="error", source="scheduler",
                    )
            with contextlib.suppress(ValueError):
                await self._task_manager.update_status(
                    task.id, TaskStatus.FAILED,
                )
            self._event_bus.emit(
                "alert", task.id, {
                    "error": f"Unhandled execution error: {error_detail}",
                    "error_type": ErrorType.INFRA.value,
                },
                origin="execution",
            )
        finally:
            self.running.pop(task.id, None)
            self._executors.pop(task.id, None)
            self._cancelled.discard(task.id)
            # Immediate next-task dispatch: trigger a tick right away
            # instead of waiting for the next periodic interval.
            if not self._stopped:
                bg = asyncio.create_task(self._safe_tick())
                self._background_ticks.add(bg)
                bg.add_done_callback(self._background_ticks.discard)

    async def _run_with_retry(
        self,
        executor: BaseExecutor,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
        on_stream_event: Callable[[dict], None] | None = None,
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
            on_stream_event: Optional callback for parsed stream-json events.

        Returns:
            The final ExecutorResult (success or last failure).
        """
        result = await executor.execute(
            task, project, env=env, on_log=on_log,
            on_stream_event=on_stream_event,
        )

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
                origin="execution",
            )

            await asyncio.sleep(delay)

            if task.id in self._cancelled:
                return result

            # Create fresh executor for retry
            executor = self._get_executor(project.executor_type)
            self._executors[task.id] = executor
            result = await executor.execute(
                task, project, env=env, on_log=on_log,
                on_stream_event=on_stream_event,
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
