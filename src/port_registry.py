"""Port registry for HelixOS orchestrator.

Manages per-project port assignments with auto-allocation from configured
ranges, atomic persistence, and orphan cleanup for stale processes.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from src.config import PortRange
from src.platform_utils import is_process_alive as _is_process_alive

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------


class PortAssignment(BaseModel):
    """A single port assignment for a project."""

    port: int
    project_id: str
    project_type: str
    pid: int | None = None
    assigned_at: str


# ------------------------------------------------------------------
# PortRegistry
# ------------------------------------------------------------------


class PortRegistry:
    """Manages port assignments per project.

    Assigns ports from configured ranges, persists to a JSON file via
    atomic write, and cleans up orphaned assignments at startup.
    """

    def __init__(
        self,
        port_ranges: dict[str, PortRange],
        persist_path: Path,
    ) -> None:
        """Initialise the registry.

        Args:
            port_ranges: Mapping of project_type -> PortRange from config.
            persist_path: Path to the JSON persistence file
                          (e.g. ``~/.helixos/ports.json``).
        """
        self._port_ranges = port_ranges
        self._persist_path = persist_path
        self._assignments: dict[str, PortAssignment] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assign_port(
        self,
        project_id: str,
        project_type: str,
        *,
        pid: int | None = None,
        preferred_port: int | None = None,
        exclude_ports: set[int] | None = None,
    ) -> int:
        """Assign a port to *project_id* from the range for *project_type*.

        If the project already has an assignment, returns the existing port.

        Args:
            project_id: Unique project identifier.
            project_type: One of the configured port range keys
                          (e.g. ``"frontend"``, ``"backend"``).
            pid: Optional PID of the process using the port.
            preferred_port: Try this port first if it is available.
            exclude_ports: Ports to skip (e.g. previously failed).

        Returns:
            The assigned port number.

        Raises:
            ValueError: If no port range is configured for *project_type*.
            RuntimeError: If all ports in the range are exhausted.
        """
        # Return existing assignment if present
        if project_id in self._assignments:
            return self._assignments[project_id].port

        port_range = self._port_ranges.get(project_type)
        if port_range is None:
            msg = f"No port range configured for project_type: {project_type!r}"
            raise ValueError(msg)

        used_ports = {a.port for a in self._assignments.values()}
        if exclude_ports:
            used_ports = used_ports | exclude_ports

        # Try preferred port first
        if (
            preferred_port is not None
            and port_range.min_port <= preferred_port <= port_range.max_port
            and preferred_port not in used_ports
        ):
            return self._create_assignment(
                project_id, project_type, preferred_port, pid
            )

        # Scan range for first available
        for port in range(port_range.min_port, port_range.max_port + 1):
            if port not in used_ports:
                return self._create_assignment(
                    project_id, project_type, port, pid
                )

        msg = (
            f"No available ports in range "
            f"{port_range.min_port}-{port_range.max_port} "
            f"for project_type {project_type!r}"
        )
        raise RuntimeError(msg)

    def release_port(self, project_id: str) -> None:
        """Free the port assignment for *project_id*.

        No-op if the project has no assignment.
        """
        if project_id in self._assignments:
            released = self._assignments.pop(project_id)
            self._save()
            logger.info(
                "Released port %d for project %s",
                released.port,
                project_id,
            )

    def get_assignment(self, project_id: str) -> PortAssignment | None:
        """Return the current assignment for *project_id*, or ``None``."""
        return self._assignments.get(project_id)

    def update_pid(self, project_id: str, pid: int) -> None:
        """Update the PID on an existing assignment.

        Raises ``KeyError`` if the project has no assignment.
        """
        if project_id not in self._assignments:
            raise KeyError(f"No port assignment for project: {project_id!r}")
        self._assignments[project_id].pid = pid
        self._save()

    def list_assignments(self) -> dict[str, PortAssignment]:
        """Return a copy of all current assignments."""
        return dict(self._assignments)

    def cleanup_orphans(self) -> list[str]:
        """Remove assignments whose PID is no longer running.

        Entries with ``pid=None`` are left untouched (not yet launched).

        Returns:
            List of project IDs that were cleaned up.
        """
        orphans: list[str] = []
        for project_id, assignment in list(self._assignments.items()):
            if assignment.pid is not None and not _is_process_alive(
                assignment.pid
            ):
                orphans.append(project_id)
                del self._assignments[project_id]
                logger.info(
                    "Cleaned up orphan: project=%s port=%d pid=%d",
                    project_id,
                    assignment.port,
                    assignment.pid,
                )
        if orphans:
            self._save()
        return orphans

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load assignments from the JSON persistence file."""
        if not self._persist_path.exists():
            return
        try:
            raw = self._persist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            for project_id, entry in data.items():
                self._assignments[project_id] = (
                    PortAssignment.model_validate(entry)
                )
            logger.info(
                "Loaded %d port assignment(s) from %s",
                len(self._assignments),
                self._persist_path,
            )
        except (json.JSONDecodeError, Exception):
            logger.warning(
                "Failed to load port assignments from %s; starting fresh",
                self._persist_path,
                exc_info=True,
            )
            self._assignments.clear()

    def _save(self) -> None:
        """Persist assignments via atomic write (tmp + os.replace)."""
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.model_dump() for k, v in self._assignments.items()}
        payload = json.dumps(data, indent=2)
        tmp_path = self._persist_path.with_suffix(".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(str(tmp_path), str(self._persist_path))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _create_assignment(
        self,
        project_id: str,
        project_type: str,
        port: int,
        pid: int | None,
    ) -> int:
        """Create an assignment entry, persist, and return the port."""
        self._assignments[project_id] = PortAssignment(
            port=port,
            project_id=project_id,
            project_type=project_type,
            pid=pid,
            assigned_at=datetime.now(UTC).isoformat(),
        )
        self._save()
        logger.info(
            "Assigned port %d to project %s (type=%s)",
            port,
            project_id,
            project_type,
        )
        return port
