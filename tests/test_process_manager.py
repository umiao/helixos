"""Tests for SubprocessRegistry and ProcessManager.

Covers subprocess tracking, global limit enforcement, process launch/stop,
status queries, orphan cleanup, and shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import (
    OrchestratorConfig,
    OrchestratorSettings,
    PortRange,
    ProjectConfig,
    ProjectRegistry,
)
from src.events import EventBus
from src.port_registry import PortRegistry
from src.process_manager import ProcessManager, ProcessStatus, _dev_server_key
from src.subprocess_registry import SubprocessEntry, SubprocessRegistry

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def subprocess_registry() -> SubprocessRegistry:
    """SubprocessRegistry with limit of 3."""
    return SubprocessRegistry(max_total=3)


@pytest.fixture
def event_bus() -> EventBus:
    """Fresh EventBus."""
    return EventBus()


@pytest.fixture
def config_with_project(tmp_path: Path) -> OrchestratorConfig:
    """Config with one project that has a launch_command."""
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    return OrchestratorConfig(
        orchestrator=OrchestratorSettings(
            max_total_subprocesses=5,
            port_ranges={
                "frontend": PortRange(min_port=3100, max_port=3999),
                "backend": PortRange(min_port=8100, max_port=8999),
            },
        ),
        projects={
            "proj1": ProjectConfig(
                name="Test Project",
                repo_path=project_dir,
                launch_command="echo hello",
                project_type="frontend",
                preferred_port=3100,
            ),
            "proj_no_cmd": ProjectConfig(
                name="No Command Project",
                repo_path=project_dir,
            ),
            "proj_no_path": ProjectConfig(
                name="No Path Project",
                launch_command="echo hello",
            ),
        },
    )


@pytest.fixture
def port_registry(tmp_path: Path, config_with_project: OrchestratorConfig) -> PortRegistry:
    """PortRegistry with test persistence path."""
    persist = tmp_path / "ports.json"
    return PortRegistry(
        config_with_project.orchestrator.port_ranges,
        persist,
    )


@pytest.fixture
def process_manager(
    config_with_project: OrchestratorConfig,
    port_registry: PortRegistry,
    subprocess_registry: SubprocessRegistry,
    event_bus: EventBus,
) -> ProcessManager:
    """ProcessManager wired to test fixtures."""
    registry = ProjectRegistry(config_with_project)
    return ProcessManager(
        config=config_with_project,
        registry=registry,
        port_registry=port_registry,
        subprocess_registry=subprocess_registry,
        event_bus=event_bus,
    )


# ================================================================
# SubprocessRegistry tests
# ================================================================


class TestSubprocessRegistry:
    """Tests for SubprocessRegistry."""

    def test_register_and_count(self, subprocess_registry: SubprocessRegistry) -> None:
        """Register an entry and verify count."""
        subprocess_registry.register("key1", 1234, "proj1", "executor")
        assert subprocess_registry.count == 1

    def test_register_multiple(self, subprocess_registry: SubprocessRegistry) -> None:
        """Register multiple entries."""
        subprocess_registry.register("key1", 1234, "proj1", "executor")
        subprocess_registry.register("key2", 1235, "proj2", "dev_server")
        assert subprocess_registry.count == 2

    def test_deregister(self, subprocess_registry: SubprocessRegistry) -> None:
        """Deregister removes the entry."""
        subprocess_registry.register("key1", 1234, "proj1", "executor")
        subprocess_registry.deregister("key1")
        assert subprocess_registry.count == 0

    def test_deregister_missing_noop(self, subprocess_registry: SubprocessRegistry) -> None:
        """Deregistering a non-existent key is a no-op."""
        subprocess_registry.deregister("nonexistent")
        assert subprocess_registry.count == 0

    def test_global_limit_enforced(self, subprocess_registry: SubprocessRegistry) -> None:
        """Registering beyond max_total raises RuntimeError."""
        subprocess_registry.register("k1", 1, "p1", "executor")
        subprocess_registry.register("k2", 2, "p2", "executor")
        subprocess_registry.register("k3", 3, "p3", "executor")
        with pytest.raises(RuntimeError, match="Global subprocess limit"):
            subprocess_registry.register("k4", 4, "p4", "executor")

    def test_overwrite_existing_key(self, subprocess_registry: SubprocessRegistry) -> None:
        """Re-registering an existing key overwrites without hitting the limit."""
        subprocess_registry.register("k1", 1, "p1", "executor")
        subprocess_registry.register("k2", 2, "p2", "executor")
        subprocess_registry.register("k3", 3, "p3", "executor")
        # Overwrite k1 -- should not raise even though at limit
        subprocess_registry.register("k1", 10, "p1", "executor")
        assert subprocess_registry.count == 3
        entries = subprocess_registry.list_entries()
        assert entries["k1"].pid == 10

    def test_has_capacity(self, subprocess_registry: SubprocessRegistry) -> None:
        """has_capacity returns False when at limit."""
        assert subprocess_registry.has_capacity() is True
        subprocess_registry.register("k1", 1, "p1", "executor")
        subprocess_registry.register("k2", 2, "p2", "executor")
        assert subprocess_registry.has_capacity() is True
        subprocess_registry.register("k3", 3, "p3", "executor")
        assert subprocess_registry.has_capacity() is False

    def test_get_by_project(self, subprocess_registry: SubprocessRegistry) -> None:
        """get_by_project filters by project_id."""
        subprocess_registry.register("k1", 1, "p1", "executor")
        subprocess_registry.register("k2", 2, "p1", "dev_server")
        subprocess_registry.register("k3", 3, "p2", "executor")
        p1_entries = subprocess_registry.get_by_project("p1")
        assert len(p1_entries) == 2
        assert all(e.project_id == "p1" for e in p1_entries)

    def test_get_by_type(self, subprocess_registry: SubprocessRegistry) -> None:
        """get_by_type filters by subprocess_type."""
        subprocess_registry.register("k1", 1, "p1", "executor")
        subprocess_registry.register("k2", 2, "p2", "dev_server")
        subprocess_registry.register("k3", 3, "p3", "dev_server")
        dev_servers = subprocess_registry.get_by_type("dev_server")
        assert len(dev_servers) == 2

    def test_list_entries(self, subprocess_registry: SubprocessRegistry) -> None:
        """list_entries returns a copy of all entries."""
        subprocess_registry.register("k1", 1, "p1", "executor")
        entries = subprocess_registry.list_entries()
        assert "k1" in entries
        assert isinstance(entries["k1"], SubprocessEntry)

    @patch("src.subprocess_registry._is_process_alive")
    def test_cleanup_dead(
        self,
        mock_alive: MagicMock,
        subprocess_registry: SubprocessRegistry,
    ) -> None:
        """cleanup_dead removes entries for dead processes."""
        subprocess_registry.register("k1", 1, "p1", "executor")
        subprocess_registry.register("k2", 2, "p2", "dev_server")
        # k1 dead, k2 alive
        mock_alive.side_effect = lambda pid: pid != 1
        dead = subprocess_registry.cleanup_dead()
        assert dead == ["k1"]
        assert subprocess_registry.count == 1

    @patch("src.subprocess_registry._is_process_alive", return_value=True)
    def test_cleanup_dead_all_alive(
        self,
        mock_alive: MagicMock,
        subprocess_registry: SubprocessRegistry,
    ) -> None:
        """cleanup_dead with all alive returns empty list."""
        subprocess_registry.register("k1", 1, "p1", "executor")
        dead = subprocess_registry.cleanup_dead()
        assert dead == []
        assert subprocess_registry.count == 1


# ================================================================
# ProcessManager tests
# ================================================================


class TestProcessManager:
    """Tests for ProcessManager."""

    @pytest.mark.asyncio
    async def test_launch_success(self, process_manager: ProcessManager) -> None:
        """Launch spawns a subprocess and returns running status."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            status = await process_manager.launch("proj1")

        assert status.running is True
        assert status.pid == 12345
        assert status.port == 3100  # preferred port

    @pytest.mark.asyncio
    async def test_launch_no_command_raises(self, process_manager: ProcessManager) -> None:
        """Launch raises ValueError when project has no launch_command."""
        with pytest.raises(ValueError, match="no launch_command"):
            await process_manager.launch("proj_no_cmd")

    @pytest.mark.asyncio
    async def test_launch_no_repo_path_raises(self, process_manager: ProcessManager) -> None:
        """Launch raises ValueError when project has no repo_path."""
        with pytest.raises(ValueError, match="no repo_path"):
            await process_manager.launch("proj_no_path")

    @pytest.mark.asyncio
    async def test_launch_already_running_raises(self, process_manager: ProcessManager) -> None:
        """Launch raises RuntimeError when dev server is already running."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            await process_manager.launch("proj1")
            with pytest.raises(RuntimeError, match="already running"):
                await process_manager.launch("proj1")

    @pytest.mark.asyncio
    async def test_launch_at_capacity_raises(
        self,
        process_manager: ProcessManager,
        subprocess_registry: SubprocessRegistry,
    ) -> None:
        """Launch raises RuntimeError when subprocess limit is reached."""
        # Fill up the registry (limit=3)
        subprocess_registry.register("ext1", 100, "x1", "executor")
        subprocess_registry.register("ext2", 101, "x2", "executor")
        subprocess_registry.register("ext3", 102, "x3", "executor")

        with pytest.raises(RuntimeError, match="subprocess limit"):
            await process_manager.launch("proj1")

    @pytest.mark.asyncio
    async def test_stop_success(self, process_manager: ProcessManager) -> None:
        """Stop terminates a running process and returns True."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(return_value=0)
        mock_proc.kill = AsyncMock()

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            await process_manager.launch("proj1")

        # Make proc look like it exited after terminate
        mock_proc.returncode = None  # Still "running" when stop is called

        with patch("src.process_manager._terminate_process"):
            # wait completes immediately (process exits gracefully)
            mock_proc.wait = AsyncMock(return_value=0)
            stopped = await process_manager.stop("proj1")

        assert stopped is True

    @pytest.mark.asyncio
    async def test_stop_not_running_returns_false(self, process_manager: ProcessManager) -> None:
        """Stop returns False when no process is running."""
        stopped = await process_manager.stop("proj1")
        assert stopped is False

    @pytest.mark.asyncio
    async def test_status_not_running(self, process_manager: ProcessManager) -> None:
        """Status returns running=False when no process is active."""
        status = process_manager.status("proj1")
        assert status.running is False

    @pytest.mark.asyncio
    async def test_status_running(self, process_manager: ProcessManager) -> None:
        """Status returns running=True with PID and port when process is active."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            await process_manager.launch("proj1")

        status = process_manager.status("proj1")
        assert status.running is True
        assert status.pid == 12345
        assert status.port == 3100

    @pytest.mark.asyncio
    async def test_stop_all(self, process_manager: ProcessManager) -> None:
        """stop_all stops all running processes."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            await process_manager.launch("proj1")

        with patch("src.process_manager._terminate_process"):
            mock_proc.wait = AsyncMock(return_value=0)
            await process_manager.stop_all()

        status = process_manager.status("proj1")
        assert status.running is False

    @pytest.mark.asyncio
    async def test_launch_emits_event(
        self,
        process_manager: ProcessManager,
        event_bus: EventBus,
    ) -> None:
        """Launch emits a process_start event."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        events: list = []

        async def collect_events() -> None:
            async for event in event_bus.subscribe():
                events.append(event)
                break  # Just collect the first event

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            task = asyncio.create_task(collect_events())
            await asyncio.sleep(0.01)
            await process_manager.launch("proj1")
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert len(events) >= 1
        assert events[0].type == "process_start"
        assert events[0].data["port"] == 3100

    @pytest.mark.asyncio
    async def test_stop_emits_event(
        self,
        process_manager: ProcessManager,
        event_bus: EventBus,
    ) -> None:
        """Stop emits a process_stop event."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            await process_manager.launch("proj1")

        events: list = []

        async def collect_events() -> None:
            async for event in event_bus.subscribe():
                events.append(event)
                if event.type == "process_stop":
                    break

        with patch("src.process_manager._terminate_process"):
            mock_proc.wait = AsyncMock(return_value=0)
            task = asyncio.create_task(collect_events())
            await asyncio.sleep(0.01)
            await process_manager.stop("proj1")
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        stop_events = [e for e in events if e.type == "process_stop"]
        assert len(stop_events) >= 1
        assert stop_events[0].data["pid"] == 12345

    @patch("src.process_manager._is_process_alive", return_value=False)
    def test_cleanup_orphans(
        self,
        mock_alive: MagicMock,
        process_manager: ProcessManager,
        subprocess_registry: SubprocessRegistry,
    ) -> None:
        """cleanup_orphans removes dead dev server entries."""
        # Manually add a stale dev_server entry
        subprocess_registry.register(
            _dev_server_key("proj1"), 99999, "proj1", "dev_server",
        )
        cleaned = process_manager.cleanup_orphans()
        assert "proj1" in cleaned
        assert subprocess_registry.count == 0

    @patch("src.process_manager._is_process_alive", return_value=True)
    def test_cleanup_orphans_alive(
        self,
        mock_alive: MagicMock,
        process_manager: ProcessManager,
        subprocess_registry: SubprocessRegistry,
    ) -> None:
        """cleanup_orphans keeps alive dev server entries."""
        subprocess_registry.register(
            _dev_server_key("proj1"), 99999, "proj1", "dev_server",
        )
        cleaned = process_manager.cleanup_orphans()
        assert cleaned == []
        assert subprocess_registry.count == 1

    @pytest.mark.asyncio
    async def test_launch_registers_in_subprocess_registry(
        self,
        process_manager: ProcessManager,
        subprocess_registry: SubprocessRegistry,
    ) -> None:
        """Launch registers the process in SubprocessRegistry."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            await process_manager.launch("proj1")

        entries = subprocess_registry.list_entries()
        key = _dev_server_key("proj1")
        assert key in entries
        assert entries[key].pid == 12345
        assert entries[key].subprocess_type == "dev_server"

    @pytest.mark.asyncio
    async def test_stop_deregisters_from_subprocess_registry(
        self,
        process_manager: ProcessManager,
        subprocess_registry: SubprocessRegistry,
    ) -> None:
        """Stop deregisters the process from SubprocessRegistry."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock(return_value=0)

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            await process_manager.launch("proj1")

        key = _dev_server_key("proj1")
        assert key in subprocess_registry.list_entries()

        with patch("src.process_manager._terminate_process"):
            await process_manager.stop("proj1")

        assert key not in subprocess_registry.list_entries()

    @pytest.mark.asyncio
    async def test_stop_force_kills_after_timeout(self, process_manager: ProcessManager) -> None:
        """Stop force kills when process does not exit within timeout."""
        mock_proc = AsyncMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        # wait raises TimeoutError first, then completes on kill
        async def slow_wait() -> int:
            await asyncio.sleep(100)
            return 0

        mock_proc.wait = slow_wait
        mock_proc.kill = AsyncMock()

        with patch("src.process_manager.asyncio.create_subprocess_shell", return_value=mock_proc):
            await process_manager.launch("proj1")

        # After kill, wait should return immediately
        kill_called = False

        def kill_and_reset() -> None:
            nonlocal kill_called
            kill_called = True
            # Simulate that kill makes the process exit
            mock_proc.wait = AsyncMock(return_value=-9)

        mock_proc.kill = kill_and_reset

        with patch("src.process_manager._terminate_process"):
            await process_manager.stop("proj1", timeout=0.1)

        assert kill_called


class TestProcessStatusModel:
    """Tests for the ProcessStatus data model."""

    def test_defaults(self) -> None:
        """ProcessStatus defaults are sensible."""
        status = ProcessStatus(running=False)
        assert status.running is False
        assert status.pid is None
        assert status.port is None
        assert status.uptime_seconds is None

    def test_full(self) -> None:
        """ProcessStatus with all fields."""
        status = ProcessStatus(
            running=True,
            pid=1234,
            port=3100,
            uptime_seconds=42.5,
        )
        assert status.running is True
        assert status.pid == 1234
