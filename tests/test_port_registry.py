"""Tests for src/port_registry.py -- PortRegistry with auto-assign, persistence, orphan cleanup."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import PortRange
from src.port_registry import PortRegistry, _is_process_alive

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

DEFAULT_RANGES: dict[str, PortRange] = {
    "frontend": PortRange(min_port=3100, max_port=3199),
    "backend": PortRange(min_port=8100, max_port=8199),
}


@pytest.fixture()
def ports_path(tmp_path: Path) -> Path:
    """Return a temp path for ports.json."""
    return tmp_path / "ports.json"


@pytest.fixture()
def registry(ports_path: Path) -> PortRegistry:
    """Return a fresh PortRegistry backed by a temp file."""
    return PortRegistry(DEFAULT_RANGES, ports_path)


# ------------------------------------------------------------------
# assign_port
# ------------------------------------------------------------------


class TestAssignPort:
    """Tests for PortRegistry.assign_port."""

    def test_assign_first_port_from_range(self, registry: PortRegistry) -> None:
        """First assignment should get the lowest port in range."""
        port = registry.assign_port("proj-1", "frontend")
        assert port == 3100

    def test_assign_second_port_increments(self, registry: PortRegistry) -> None:
        """Second assignment in the same range gets the next port."""
        registry.assign_port("proj-1", "frontend")
        port = registry.assign_port("proj-2", "frontend")
        assert port == 3101

    def test_assign_backend_range(self, registry: PortRegistry) -> None:
        """Backend projects use the backend port range."""
        port = registry.assign_port("proj-1", "backend")
        assert port == 8100

    def test_assign_returns_existing(self, registry: PortRegistry) -> None:
        """Assigning a port to an already-assigned project returns the same port."""
        port1 = registry.assign_port("proj-1", "frontend")
        port2 = registry.assign_port("proj-1", "frontend")
        assert port1 == port2

    def test_assign_with_preferred_port(self, registry: PortRegistry) -> None:
        """Preferred port is used when it is available and in range."""
        port = registry.assign_port("proj-1", "frontend", preferred_port=3150)
        assert port == 3150

    def test_assign_preferred_port_taken(self, registry: PortRegistry) -> None:
        """Falls back to lowest available when preferred port is already assigned."""
        registry.assign_port("proj-1", "frontend", preferred_port=3150)
        port = registry.assign_port("proj-2", "frontend", preferred_port=3150)
        assert port == 3100  # lowest available, since 3150 is taken

    def test_assign_preferred_port_out_of_range(
        self, registry: PortRegistry
    ) -> None:
        """Preferred port outside the range is ignored; assigns from range."""
        port = registry.assign_port("proj-1", "frontend", preferred_port=9999)
        assert port == 3100

    def test_assign_with_exclude_ports(self, registry: PortRegistry) -> None:
        """Excluded ports are skipped during assignment."""
        port = registry.assign_port(
            "proj-1", "frontend", exclude_ports={3100, 3101}
        )
        assert port == 3102

    def test_assign_unknown_project_type_raises(
        self, registry: PortRegistry
    ) -> None:
        """ValueError raised for unconfigured project_type."""
        with pytest.raises(ValueError, match="No port range configured"):
            registry.assign_port("proj-1", "unknown_type")

    def test_assign_exhausted_range_raises(self, ports_path: Path) -> None:
        """RuntimeError when all ports in the range are used."""
        tiny_range = {"test": PortRange(min_port=5000, max_port=5001)}
        reg = PortRegistry(tiny_range, ports_path)
        reg.assign_port("p1", "test")
        reg.assign_port("p2", "test")
        with pytest.raises(RuntimeError, match="No available ports"):
            reg.assign_port("p3", "test")

    def test_assign_with_pid(self, registry: PortRegistry) -> None:
        """PID is stored in the assignment."""
        registry.assign_port("proj-1", "frontend", pid=12345)
        assignment = registry.get_assignment("proj-1")
        assert assignment is not None
        assert assignment.pid == 12345

    def test_no_duplicate_ports_across_types(
        self, ports_path: Path
    ) -> None:
        """Different project types with overlapping ranges don't get duplicate ports."""
        overlapping = {
            "a": PortRange(min_port=4000, max_port=4010),
            "b": PortRange(min_port=4000, max_port=4010),
        }
        reg = PortRegistry(overlapping, ports_path)
        port_a = reg.assign_port("proj-a", "a")
        port_b = reg.assign_port("proj-b", "b")
        # Both come from same numeric range, but used_ports prevents dupes
        assert port_a != port_b
        assert port_a == 4000
        assert port_b == 4001


# ------------------------------------------------------------------
# release_port
# ------------------------------------------------------------------


class TestReleasePort:
    """Tests for PortRegistry.release_port."""

    def test_release_frees_port(self, registry: PortRegistry) -> None:
        """After release, the port can be reassigned."""
        registry.assign_port("proj-1", "frontend")
        registry.release_port("proj-1")
        assert registry.get_assignment("proj-1") is None
        # Port 3100 is available again
        port = registry.assign_port("proj-2", "frontend")
        assert port == 3100

    def test_release_nonexistent_is_noop(self, registry: PortRegistry) -> None:
        """Releasing a project with no assignment does nothing."""
        registry.release_port("no-such-project")  # should not raise


# ------------------------------------------------------------------
# get_assignment
# ------------------------------------------------------------------


class TestGetAssignment:
    """Tests for PortRegistry.get_assignment."""

    def test_get_existing(self, registry: PortRegistry) -> None:
        """Returns the PortAssignment for an assigned project."""
        registry.assign_port("proj-1", "backend", pid=42)
        a = registry.get_assignment("proj-1")
        assert a is not None
        assert a.port == 8100
        assert a.project_type == "backend"
        assert a.pid == 42

    def test_get_nonexistent(self, registry: PortRegistry) -> None:
        """Returns None for a project with no assignment."""
        assert registry.get_assignment("no-such") is None


# ------------------------------------------------------------------
# update_pid
# ------------------------------------------------------------------


class TestUpdatePid:
    """Tests for PortRegistry.update_pid."""

    def test_update_pid_success(self, registry: PortRegistry) -> None:
        """PID is updated on an existing assignment."""
        registry.assign_port("proj-1", "frontend")
        registry.update_pid("proj-1", 9999)
        a = registry.get_assignment("proj-1")
        assert a is not None
        assert a.pid == 9999

    def test_update_pid_no_assignment_raises(
        self, registry: PortRegistry
    ) -> None:
        """KeyError raised when updating PID for unassigned project."""
        with pytest.raises(KeyError, match="No port assignment"):
            registry.update_pid("no-such", 1234)


# ------------------------------------------------------------------
# list_assignments
# ------------------------------------------------------------------


class TestListAssignments:
    """Tests for PortRegistry.list_assignments."""

    def test_empty(self, registry: PortRegistry) -> None:
        """Empty registry returns empty dict."""
        assert registry.list_assignments() == {}

    def test_multiple(self, registry: PortRegistry) -> None:
        """Returns all assignments."""
        registry.assign_port("p1", "frontend")
        registry.assign_port("p2", "backend")
        assignments = registry.list_assignments()
        assert len(assignments) == 2
        assert "p1" in assignments
        assert "p2" in assignments


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


class TestPersistence:
    """Tests for atomic persistence (save/load)."""

    def test_persists_to_file(
        self, registry: PortRegistry, ports_path: Path
    ) -> None:
        """Assignments are saved to disk as JSON."""
        registry.assign_port("proj-1", "frontend")
        assert ports_path.exists()
        data = json.loads(ports_path.read_text(encoding="utf-8"))
        assert "proj-1" in data
        assert data["proj-1"]["port"] == 3100

    def test_loads_on_init(self, ports_path: Path) -> None:
        """A new PortRegistry instance loads existing assignments from disk."""
        reg1 = PortRegistry(DEFAULT_RANGES, ports_path)
        reg1.assign_port("proj-1", "frontend", pid=100)
        # Create a new instance -- should load from file
        reg2 = PortRegistry(DEFAULT_RANGES, ports_path)
        a = reg2.get_assignment("proj-1")
        assert a is not None
        assert a.port == 3100
        assert a.pid == 100

    def test_load_new_instance_no_duplicate_ports(
        self, ports_path: Path
    ) -> None:
        """New instance assigns the next port after loaded assignments."""
        reg1 = PortRegistry(DEFAULT_RANGES, ports_path)
        reg1.assign_port("proj-1", "frontend")
        reg2 = PortRegistry(DEFAULT_RANGES, ports_path)
        port = reg2.assign_port("proj-2", "frontend")
        assert port == 3101  # 3100 is already taken

    def test_atomic_write_no_tmp_left(
        self, registry: PortRegistry, ports_path: Path
    ) -> None:
        """After save, no .tmp file should remain."""
        registry.assign_port("proj-1", "frontend")
        tmp = ports_path.with_suffix(".tmp")
        assert not tmp.exists()

    def test_corrupted_file_starts_fresh(self, ports_path: Path) -> None:
        """If the JSON file is corrupted, the registry starts empty."""
        ports_path.parent.mkdir(parents=True, exist_ok=True)
        ports_path.write_text("NOT VALID JSON {{{", encoding="utf-8")
        reg = PortRegistry(DEFAULT_RANGES, ports_path)
        assert reg.list_assignments() == {}

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Parent directories are created if they do not exist."""
        deep_path = tmp_path / "a" / "b" / "ports.json"
        reg = PortRegistry(DEFAULT_RANGES, deep_path)
        reg.assign_port("proj-1", "frontend")
        assert deep_path.exists()


# ------------------------------------------------------------------
# cleanup_orphans
# ------------------------------------------------------------------


class TestCleanupOrphans:
    """Tests for PortRegistry.cleanup_orphans."""

    def test_removes_dead_pid(self, registry: PortRegistry) -> None:
        """Entries with a dead PID are removed."""
        registry.assign_port("proj-1", "frontend", pid=99999)
        with patch(
            "src.port_registry._is_process_alive", return_value=False
        ):
            orphans = registry.cleanup_orphans()
        assert orphans == ["proj-1"]
        assert registry.get_assignment("proj-1") is None

    def test_keeps_alive_pid(self, registry: PortRegistry) -> None:
        """Entries with a live PID are kept."""
        registry.assign_port("proj-1", "frontend", pid=12345)
        with patch(
            "src.port_registry._is_process_alive", return_value=True
        ):
            orphans = registry.cleanup_orphans()
        assert orphans == []
        assert registry.get_assignment("proj-1") is not None

    def test_skips_none_pid(self, registry: PortRegistry) -> None:
        """Entries without a PID (not yet launched) are not cleaned up."""
        registry.assign_port("proj-1", "frontend")
        orphans = registry.cleanup_orphans()
        assert orphans == []
        assert registry.get_assignment("proj-1") is not None

    def test_cleanup_persists_changes(
        self, registry: PortRegistry, ports_path: Path
    ) -> None:
        """Orphan cleanup saves the updated state to disk."""
        registry.assign_port("proj-1", "frontend", pid=11111)
        with patch(
            "src.port_registry._is_process_alive", return_value=False
        ):
            registry.cleanup_orphans()
        # Reload from disk
        reg2 = PortRegistry(DEFAULT_RANGES, ports_path)
        assert reg2.get_assignment("proj-1") is None

    def test_cleanup_mixed(self, registry: PortRegistry) -> None:
        """Mix of alive, dead, and no-PID entries."""
        registry.assign_port("alive", "frontend", pid=1)
        registry.assign_port("dead", "frontend", pid=2)
        registry.assign_port("no-pid", "backend")

        def mock_alive(pid: int) -> bool:
            return pid == 1

        with patch(
            "src.port_registry._is_process_alive", side_effect=mock_alive
        ):
            orphans = registry.cleanup_orphans()

        assert orphans == ["dead"]
        assert registry.get_assignment("alive") is not None
        assert registry.get_assignment("dead") is None
        assert registry.get_assignment("no-pid") is not None


# ------------------------------------------------------------------
# _is_process_alive
# ------------------------------------------------------------------


class TestIsProcessAlive:
    """Tests for the _is_process_alive helper."""

    def test_alive_process(self) -> None:
        """Current process PID should be alive."""
        import os

        assert _is_process_alive(os.getpid()) is True

    def test_dead_process(self) -> None:
        """A very large PID should not be alive."""
        assert _is_process_alive(2**30) is False
