"""Tests for ProcessMonitor -- background process failure detection.

Covers:
- Dead process detection and SSE event emission
- Dev server crash cleanup
- Executor crash detection
- Health-check snapshot (get_active_processes)
- Start/stop lifecycle
- No false positives for alive processes
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.events import EventBus
from src.process_monitor import ProcessMonitor, _elapsed_seconds
from src.subprocess_registry import SubprocessRegistry

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def subprocess_registry() -> SubprocessRegistry:
    """SubprocessRegistry with limit of 5."""
    return SubprocessRegistry(max_total=5)


@pytest.fixture
def event_bus() -> EventBus:
    """Fresh EventBus."""
    return EventBus()


@pytest.fixture
def process_manager() -> MagicMock:
    """Mocked ProcessManager for cleanup calls."""
    pm = MagicMock()
    pm._cleanup_stale = MagicMock()
    return pm


@pytest.fixture
def monitor(
    subprocess_registry: SubprocessRegistry,
    process_manager: MagicMock,
    event_bus: EventBus,
) -> ProcessMonitor:
    """ProcessMonitor with mocked dependencies."""
    return ProcessMonitor(
        subprocess_registry=subprocess_registry,
        process_manager=process_manager,
        event_bus=event_bus,
        interval=1,
    )


# ------------------------------------------------------------------
# scan() -- dead process detection
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_detects_dead_dev_server(
    monitor: ProcessMonitor,
    subprocess_registry: SubprocessRegistry,
    event_bus: EventBus,
    process_manager: MagicMock,
) -> None:
    """When a dev server PID is dead, scan() emits process_failed and cleans up."""
    subprocess_registry.register(
        key="dev_server:proj1",
        pid=99999,
        project_id="proj1",
        subprocess_type="dev_server",
    )

    with patch(
        "src.process_monitor._is_process_alive", return_value=False,
    ):
        failed = await monitor.scan()

    assert failed == ["dev_server:proj1"]
    # Registry entry removed
    assert subprocess_registry.count == 0
    # ProcessManager cleanup called
    process_manager._cleanup_stale.assert_called_once_with("proj1")


@pytest.mark.asyncio
async def test_scan_detects_dead_executor(
    monitor: ProcessMonitor,
    subprocess_registry: SubprocessRegistry,
    event_bus: EventBus,
) -> None:
    """When an executor PID is dead, scan() emits process_failed."""
    subprocess_registry.register(
        key="task-001",
        pid=88888,
        project_id="proj1",
        subprocess_type="executor",
    )

    with patch(
        "src.process_monitor._is_process_alive", return_value=False,
    ):
        failed = await monitor.scan()

    assert failed == ["task-001"]
    assert subprocess_registry.count == 0


@pytest.mark.asyncio
async def test_scan_emits_process_failed_event(
    monitor: ProcessMonitor,
    subprocess_registry: SubprocessRegistry,
    event_bus: EventBus,
) -> None:
    """scan() emits a process_failed SSE event with correct payload."""
    subprocess_registry.register(
        key="dev_server:proj1",
        pid=99999,
        project_id="proj1",
        subprocess_type="dev_server",
    )

    collected: list[dict] = []
    original_emit = event_bus.emit

    def capture_emit(event_type: str, task_id: str, data: object) -> None:
        collected.append({
            "type": event_type,
            "task_id": task_id,
            "data": data,
        })
        original_emit(event_type, task_id, data)

    event_bus.emit = capture_emit  # type: ignore[assignment]

    with patch(
        "src.process_monitor._is_process_alive", return_value=False,
    ):
        await monitor.scan()

    assert len(collected) == 1
    evt = collected[0]
    assert evt["type"] == "process_failed"
    assert evt["task_id"] == "proj1"
    assert evt["data"]["pid"] == 99999
    assert evt["data"]["subprocess_type"] == "dev_server"
    assert "error" in evt["data"]


@pytest.mark.asyncio
async def test_scan_ignores_alive_processes(
    monitor: ProcessMonitor,
    subprocess_registry: SubprocessRegistry,
) -> None:
    """scan() does not report alive processes as failed."""
    subprocess_registry.register(
        key="dev_server:proj1",
        pid=99999,
        project_id="proj1",
        subprocess_type="dev_server",
    )

    with patch(
        "src.process_monitor._is_process_alive", return_value=True,
    ):
        failed = await monitor.scan()

    assert failed == []
    assert subprocess_registry.count == 1  # Still tracked


@pytest.mark.asyncio
async def test_scan_handles_multiple_processes(
    monitor: ProcessMonitor,
    subprocess_registry: SubprocessRegistry,
) -> None:
    """scan() handles mix of alive and dead processes correctly."""
    subprocess_registry.register(
        key="dev_server:proj1", pid=100, project_id="proj1",
        subprocess_type="dev_server",
    )
    subprocess_registry.register(
        key="task-001", pid=200, project_id="proj1",
        subprocess_type="executor",
    )
    subprocess_registry.register(
        key="dev_server:proj2", pid=300, project_id="proj2",
        subprocess_type="dev_server",
    )

    def mock_alive(pid: int) -> bool:
        return pid == 200  # Only executor is alive

    with patch(
        "src.process_monitor._is_process_alive", side_effect=mock_alive,
    ):
        failed = await monitor.scan()

    assert len(failed) == 2
    assert "dev_server:proj1" in failed
    assert "dev_server:proj2" in failed
    assert subprocess_registry.count == 1  # Only executor remains


@pytest.mark.asyncio
async def test_scan_empty_registry(
    monitor: ProcessMonitor,
) -> None:
    """scan() on empty registry returns empty list."""
    failed = await monitor.scan()
    assert failed == []


# ------------------------------------------------------------------
# get_active_processes() -- health-check snapshot
# ------------------------------------------------------------------


def test_get_active_processes_returns_entries(
    monitor: ProcessMonitor,
    subprocess_registry: SubprocessRegistry,
) -> None:
    """get_active_processes() returns all tracked entries."""
    subprocess_registry.register(
        key="dev_server:proj1", pid=100, project_id="proj1",
        subprocess_type="dev_server",
    )
    subprocess_registry.register(
        key="task-001", pid=200, project_id="proj2",
        subprocess_type="executor",
    )

    with patch(
        "src.process_monitor._is_process_alive", return_value=True,
    ):
        result = monitor.get_active_processes()

    assert len(result) == 2
    keys = {entry["key"] for entry in result}
    assert keys == {"dev_server:proj1", "task-001"}

    for entry in result:
        assert "pid" in entry
        assert "project_id" in entry
        assert "subprocess_type" in entry
        assert "start_time" in entry
        assert "elapsed_seconds" in entry
        assert "alive" in entry


def test_get_active_processes_empty(
    monitor: ProcessMonitor,
) -> None:
    """get_active_processes() returns empty list when no processes tracked."""
    result = monitor.get_active_processes()
    assert result == []


# ------------------------------------------------------------------
# start() / stop() lifecycle
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_stop_lifecycle(
    monitor: ProcessMonitor,
) -> None:
    """ProcessMonitor can be started and stopped without error."""
    await monitor.start()
    assert monitor._task is not None
    assert not monitor._task.done()

    await monitor.stop()
    assert monitor._task is None


@pytest.mark.asyncio
async def test_stop_idempotent(
    monitor: ProcessMonitor,
) -> None:
    """Stopping a never-started monitor is a no-op."""
    await monitor.stop()  # Should not raise


# ------------------------------------------------------------------
# _elapsed_seconds helper
# ------------------------------------------------------------------


def test_elapsed_seconds_valid_timestamp() -> None:
    """_elapsed_seconds returns a positive value for a past timestamp."""
    from datetime import UTC, datetime
    past = datetime.now(UTC).isoformat()
    elapsed = _elapsed_seconds(past)
    assert elapsed >= 0.0


def test_elapsed_seconds_invalid_timestamp() -> None:
    """_elapsed_seconds returns 0.0 for invalid input."""
    assert _elapsed_seconds("not-a-timestamp") == 0.0
    assert _elapsed_seconds("") == 0.0


# ------------------------------------------------------------------
# Executor crash: event payload check
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_crash_event_payload(
    monitor: ProcessMonitor,
    subprocess_registry: SubprocessRegistry,
    event_bus: EventBus,
) -> None:
    """Executor crash emits process_failed with task_id as the key."""
    subprocess_registry.register(
        key="task-abc-123",
        pid=77777,
        project_id="proj1",
        subprocess_type="executor",
    )

    collected: list[dict] = []
    original_emit = event_bus.emit

    def capture_emit(event_type: str, task_id: str, data: object) -> None:
        collected.append({"type": event_type, "task_id": task_id, "data": data})
        original_emit(event_type, task_id, data)

    event_bus.emit = capture_emit  # type: ignore[assignment]

    with patch(
        "src.process_monitor._is_process_alive", return_value=False,
    ):
        await monitor.scan()

    assert len(collected) == 1
    evt = collected[0]
    assert evt["type"] == "process_failed"
    assert evt["task_id"] == "task-abc-123"
    assert evt["data"]["subprocess_type"] == "executor"
    assert evt["data"]["pid"] == 77777


# ------------------------------------------------------------------
# No activity-based stall detection (AC 4)
# ------------------------------------------------------------------


def test_no_activity_based_stall_detection() -> None:
    """ProcessMonitor has no idle/stall timeout attributes (AC 4)."""
    # Verify the class does not have any activity/idle/stall timeout fields
    monitor_attrs = dir(ProcessMonitor)
    for attr in monitor_attrs:
        attr_lower = attr.lower()
        assert "idle" not in attr_lower, f"Found idle-related attribute: {attr}"
        assert "stall" not in attr_lower, f"Found stall-related attribute: {attr}"
        assert "inactivity" not in attr_lower, f"Found inactivity attribute: {attr}"
