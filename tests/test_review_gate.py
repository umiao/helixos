"""Tests for the two-layer review gate (T-P0-18).

Layer 1: TaskManager blocks BACKLOG -> QUEUED when review_gate_enabled=True.
Layer 2: Scheduler._can_execute() checks for approved review history.
Also covers: ProjectSettingsStore persistence, Scheduler gate toggle + SSE,
and API endpoint behavior.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import OrchestratorConfig, ProjectConfig, ProjectRegistry
from src.events import EventBus
from src.executors.base import BaseExecutor, ExecutorResult
from src.history_writer import HistoryWriter
from src.models import ExecutorType, LLMReview, Project, Task, TaskStatus
from src.project_settings import ProjectSettingsStore
from src.scheduler import Scheduler
from src.task_manager import ReviewGateBlockedError, TaskManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockExecutor(BaseExecutor):
    """Executor that returns success immediately."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(
        self,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
    ) -> ExecutorResult:
        """Record the call and return success."""
        self.calls.append(task.id)
        on_log("mock log")
        return ExecutorResult(
            success=True,
            exit_code=0,
            log_lines=["ok"],
            duration_seconds=0.1,
        )

    async def cancel(self) -> None:
        """No-op."""


def _make_config() -> OrchestratorConfig:
    """Build a minimal config for testing."""
    return OrchestratorConfig(
        projects={
            "proj": ProjectConfig(
                name="Test Project",
                repo_path=Path("/tmp/test-repo"),
                executor_type=ExecutorType.CODE,
                max_concurrency=1,
            ),
        },
    )


def _make_task(
    task_id: str = "proj:t1",
    project_id: str = "proj",
    status: TaskStatus = TaskStatus.BACKLOG,
) -> Task:
    """Build a Task for testing."""
    return Task(
        id=task_id,
        project_id=project_id,
        local_task_id="t1",
        title="Test Task",
        status=status,
        executor_type=ExecutorType.CODE,
    )


def _track_events(event_bus: EventBus) -> list[tuple[str, str, object]]:
    """Track emitted events for assertions."""
    emitted: list[tuple[str, str, object]] = []
    original = event_bus.emit

    def tracking(event_type: str, task_id: str, data: object) -> None:
        emitted.append((event_type, task_id, data))
        original(event_type, task_id, data)

    event_bus.emit = tracking  # type: ignore[assignment]
    return emitted


def _make_scheduler(
    session_factory,
    *,
    history_writer: HistoryWriter | None = None,
    settings_store: ProjectSettingsStore | None = None,
) -> tuple[Scheduler, TaskManager, EventBus, list]:
    """Wire up a Scheduler with tracking."""
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
        history_writer=history_writer,
        settings_store=settings_store,
    )
    return scheduler, task_manager, event_bus, emitted


# ---------------------------------------------------------------------------
# Layer 1: TaskManager review gate
# ---------------------------------------------------------------------------


class TestTaskManagerReviewGate:
    """Layer 1 -- update_status blocks BACKLOG -> QUEUED when gate is on."""

    async def test_gate_on_blocks_backlog_to_queued(
        self, session_factory,
    ) -> None:
        """BACKLOG -> QUEUED should be rejected when review_gate_enabled=True."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(task)

        with pytest.raises(ReviewGateBlockedError, match="(?i)review gate"):
            await tm.update_status(
                task.id, TaskStatus.QUEUED,
                review_gate_enabled=True,
            )

    async def test_gate_off_allows_backlog_to_queued(
        self, session_factory,
    ) -> None:
        """BACKLOG -> QUEUED should succeed when review_gate_enabled=False."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(task)

        updated = await tm.update_status(
            task.id, TaskStatus.QUEUED,
            review_gate_enabled=False,
        )
        assert updated.status == TaskStatus.QUEUED

    async def test_gate_on_allows_backlog_to_review(
        self, session_factory,
    ) -> None:
        """BACKLOG -> REVIEW should still be allowed when gate is on."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(task)

        updated = await tm.update_status(
            task.id, TaskStatus.REVIEW,
            review_gate_enabled=True,
        )
        assert updated.status == TaskStatus.REVIEW

    async def test_gate_default_off_when_not_specified(
        self, session_factory,
    ) -> None:
        """Default review_gate_enabled=False should allow BACKLOG -> QUEUED."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(task)

        # Default parameter value is False (backward compatible)
        updated = await tm.update_status(task.id, TaskStatus.QUEUED)
        assert updated.status == TaskStatus.QUEUED

    async def test_gate_on_does_not_affect_other_transitions(
        self, session_factory,
    ) -> None:
        """Review gate should only affect BACKLOG -> QUEUED, not other transitions."""
        tm = TaskManager(session_factory)
        task = _make_task(status=TaskStatus.BACKLOG)
        await tm.create_task(task)

        # BACKLOG -> REVIEW should work
        updated = await tm.update_status(
            task.id, TaskStatus.REVIEW,
            review_gate_enabled=True,
        )
        assert updated.status == TaskStatus.REVIEW


# ---------------------------------------------------------------------------
# Layer 2: Scheduler._can_execute
# ---------------------------------------------------------------------------


class TestSchedulerCanExecute:
    """Layer 2 -- _can_execute checks review history before execution."""

    async def test_can_execute_skips_unreviewed_task(
        self, session_factory,
    ) -> None:
        """With gate on and no review history, scheduler should skip the task."""
        history_writer = HistoryWriter(session_factory)
        scheduler, tm, _eb, emitted = _make_scheduler(
            session_factory, history_writer=history_writer,
        )
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        # Create a QUEUED task (simulating a bypass of Layer 1)
        task = _make_task(status=TaskStatus.QUEUED)
        await tm.create_task(task)

        # Review gate is enabled by default
        assert scheduler.is_review_gate_enabled("proj") is True

        # Tick should NOT dispatch -- no approved review record
        await scheduler.tick()
        assert len(mock_exec.calls) == 0
        assert len(scheduler.running) == 0

    async def test_can_execute_allows_reviewed_task(
        self, session_factory,
    ) -> None:
        """With gate on and an approved review, scheduler should execute."""
        history_writer = HistoryWriter(session_factory)
        scheduler, tm, _eb, emitted = _make_scheduler(
            session_factory, history_writer=history_writer,
        )
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task(status=TaskStatus.QUEUED)
        await tm.create_task(task)

        # Write an approved review record
        from datetime import UTC, datetime

        review = LLMReview(
            model="test-model",
            focus="correctness",
            verdict="approve",
            summary="Looks good",
            suggestions=[],
            timestamp=datetime.now(UTC),
        )
        await history_writer.write_review(task.id, round_number=1, review=review)

        await scheduler.tick()

        # Wait for execution
        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        assert len(mock_exec.calls) == 1
        updated = await tm.get_task(task.id)
        assert updated is not None
        assert updated.status == TaskStatus.DONE

    async def test_can_execute_allows_when_gate_disabled(
        self, session_factory,
    ) -> None:
        """With gate disabled, scheduler should execute even without review."""
        history_writer = HistoryWriter(session_factory)
        scheduler, tm, _eb, emitted = _make_scheduler(
            session_factory, history_writer=history_writer,
        )
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task(status=TaskStatus.QUEUED)
        await tm.create_task(task)

        # Disable review gate
        await scheduler.disable_review_gate("proj")
        assert scheduler.is_review_gate_enabled("proj") is False

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        assert len(mock_exec.calls) == 1

    async def test_rejected_review_does_not_satisfy_gate(
        self, session_factory,
    ) -> None:
        """A rejected review should NOT satisfy the review gate."""
        history_writer = HistoryWriter(session_factory)
        scheduler, tm, _eb, emitted = _make_scheduler(
            session_factory, history_writer=history_writer,
        )
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task(status=TaskStatus.QUEUED)
        await tm.create_task(task)

        from datetime import UTC, datetime

        review = LLMReview(
            model="test-model",
            focus="correctness",
            verdict="reject",
            summary="Needs work",
            suggestions=["Fix bugs"],
            timestamp=datetime.now(UTC),
        )
        await history_writer.write_review(task.id, round_number=1, review=review)

        await scheduler.tick()

        # Should NOT execute because verdict is "reject"
        assert len(mock_exec.calls) == 0

    async def test_human_approved_review_satisfies_gate(
        self, session_factory,
    ) -> None:
        """A review with human_decision='approve' should satisfy the gate."""
        history_writer = HistoryWriter(session_factory)
        scheduler, tm, _eb, emitted = _make_scheduler(
            session_factory, history_writer=history_writer,
        )
        mock_exec = MockExecutor()
        scheduler._get_executor = lambda _: mock_exec

        task = _make_task(status=TaskStatus.QUEUED)
        await tm.create_task(task)

        from datetime import UTC, datetime

        # Review verdict is "needs_human" but human approved it
        review = LLMReview(
            model="test-model",
            focus="correctness",
            verdict="needs_human",
            summary="Needs human review",
            suggestions=[],
            timestamp=datetime.now(UTC),
        )
        await history_writer.write_review(
            task.id, round_number=1, review=review,
            human_decision="approve",
        )

        await scheduler.tick()

        running_tasks = list(scheduler.running.values())
        if running_tasks:
            await asyncio.gather(*running_tasks)

        assert len(mock_exec.calls) == 1


# ---------------------------------------------------------------------------
# Scheduler review gate toggle + SSE
# ---------------------------------------------------------------------------


class TestSchedulerReviewGateToggle:
    """Review gate enable/disable + SSE events."""

    async def test_enable_gate_emits_sse_event(
        self, session_factory,
    ) -> None:
        """Enabling the review gate should emit a review_gate_changed event."""
        scheduler, _tm, _eb, emitted = _make_scheduler(session_factory)

        # First disable so enable is a change
        await scheduler.disable_review_gate("proj")
        await scheduler.enable_review_gate("proj")

        gate_events = [
            e for e in emitted if e[0] == "review_gate_changed"
        ]
        assert len(gate_events) == 2
        assert gate_events[1][1] == "proj"
        assert gate_events[1][2] == {"review_gate_enabled": True}

    async def test_disable_gate_emits_sse_event(
        self, session_factory,
    ) -> None:
        """Disabling the review gate should emit a review_gate_changed event."""
        scheduler, _tm, _eb, emitted = _make_scheduler(session_factory)

        await scheduler.disable_review_gate("proj")

        gate_events = [
            e for e in emitted if e[0] == "review_gate_changed"
        ]
        assert len(gate_events) == 1
        assert gate_events[0][1] == "proj"
        assert gate_events[0][2] == {"review_gate_enabled": False}

    async def test_is_review_gate_enabled_default(
        self, session_factory,
    ) -> None:
        """Review gate should be enabled by default."""
        scheduler, _tm, _eb, _emitted = _make_scheduler(session_factory)
        assert scheduler.is_review_gate_enabled("proj") is True
        assert scheduler.is_review_gate_enabled("unknown") is True


# ---------------------------------------------------------------------------
# ProjectSettingsStore -- review gate persistence
# ---------------------------------------------------------------------------


class TestSettingsStoreReviewGate:
    """DB-backed review gate persistence."""

    async def test_review_gate_default_enabled(
        self, session_factory,
    ) -> None:
        """No row => gate enabled (default)."""
        store = ProjectSettingsStore(session_factory)
        assert await store.is_review_gate_enabled("proj") is True

    async def test_set_and_get_review_gate(
        self, session_factory,
    ) -> None:
        """set_review_gate should persist and is_review_gate_enabled should read it."""
        store = ProjectSettingsStore(session_factory)

        await store.set_review_gate("proj", enabled=False)
        assert await store.is_review_gate_enabled("proj") is False

        await store.set_review_gate("proj", enabled=True)
        assert await store.is_review_gate_enabled("proj") is True

    async def test_get_all_review_gate_disabled(
        self, session_factory,
    ) -> None:
        """get_all_review_gate_disabled should return only disabled projects."""
        store = ProjectSettingsStore(session_factory)

        await store.set_review_gate("proj1", enabled=False)
        await store.set_review_gate("proj2", enabled=True)
        await store.set_review_gate("proj3", enabled=False)

        disabled = await store.get_all_review_gate_disabled()
        assert disabled == {"proj1", "proj3"}

    async def test_gate_persists_with_settings_store(
        self, session_factory,
    ) -> None:
        """Review gate state should be persisted to DB via settings_store."""
        settings_store = ProjectSettingsStore(session_factory)
        scheduler, _tm, _eb, _emitted = _make_scheduler(
            session_factory, settings_store=settings_store,
        )

        await scheduler.disable_review_gate("proj")
        assert await settings_store.is_review_gate_enabled("proj") is False

        await scheduler.enable_review_gate("proj")
        assert await settings_store.is_review_gate_enabled("proj") is True

    async def test_gate_loaded_on_start(
        self, session_factory,
    ) -> None:
        """Scheduler.start() should load persisted review gate state."""
        settings_store = ProjectSettingsStore(session_factory)

        # Pre-set the gate to disabled
        await settings_store.set_review_gate("proj", enabled=False)

        scheduler, _tm, _eb, _emitted = _make_scheduler(
            session_factory, settings_store=settings_store,
        )

        # Before start, the cache is empty (gate appears enabled)
        assert scheduler.is_review_gate_enabled("proj") is True

        # After start, should load persisted state
        await scheduler.start()
        try:
            assert scheduler.is_review_gate_enabled("proj") is False
        finally:
            await scheduler.stop()


# ---------------------------------------------------------------------------
# HistoryWriter.has_approved_review
# ---------------------------------------------------------------------------


class TestHistoryWriterHasApprovedReview:
    """Tests for has_approved_review helper."""

    async def test_no_reviews_returns_false(
        self, session_factory,
    ) -> None:
        """No review records => False."""
        hw = HistoryWriter(session_factory)
        assert await hw.has_approved_review("proj:t1") is False

    async def test_approved_review_returns_true(
        self, session_factory,
    ) -> None:
        """Approved verdict => True."""
        from datetime import UTC, datetime

        hw = HistoryWriter(session_factory)
        review = LLMReview(
            model="m", focus="f", verdict="approve",
            summary="ok", suggestions=[], timestamp=datetime.now(UTC),
        )
        await hw.write_review("proj:t1", round_number=1, review=review)
        assert await hw.has_approved_review("proj:t1") is True

    async def test_rejected_review_returns_false(
        self, session_factory,
    ) -> None:
        """Rejected verdict without human approval => False."""
        from datetime import UTC, datetime

        hw = HistoryWriter(session_factory)
        review = LLMReview(
            model="m", focus="f", verdict="reject",
            summary="no", suggestions=[], timestamp=datetime.now(UTC),
        )
        await hw.write_review("proj:t1", round_number=1, review=review)
        assert await hw.has_approved_review("proj:t1") is False

    async def test_human_approve_returns_true(
        self, session_factory,
    ) -> None:
        """Human decision 'approve' => True."""
        from datetime import UTC, datetime

        hw = HistoryWriter(session_factory)
        review = LLMReview(
            model="m", focus="f", verdict="needs_human",
            summary="needs review", suggestions=[], timestamp=datetime.now(UTC),
        )
        await hw.write_review(
            "proj:t1", round_number=1, review=review,
            human_decision="approve",
        )
        assert await hw.has_approved_review("proj:t1") is True
