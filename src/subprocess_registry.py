"""Unified subprocess registry for HelixOS orchestrator.

Tracks ALL subprocesses spawned by the system -- both Scheduler executor
processes and ProcessManager dev servers.  Enforces a shared global limit
on total subprocesses to prevent resource exhaustion.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel

from src.platform_utils import is_process_alive as _is_process_alive

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------


class SubprocessEntry(BaseModel):
    """A single tracked subprocess."""

    pid: int
    project_id: str
    subprocess_type: str  # "executor" or "dev_server"
    start_time: str  # ISO 8601


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------


class SubprocessRegistry:
    """Unified tracker for all subprocesses with a shared global limit.

    Both the Scheduler (executor subprocesses) and ProcessManager (dev
    server subprocesses) register here.  The shared limit prevents the
    system from spawning more subprocesses than ``max_total``.
    """

    def __init__(self, max_total: int) -> None:
        """Initialise the registry.

        Args:
            max_total: Maximum number of simultaneous subprocesses allowed.
        """
        self._max_total = max_total
        self._entries: dict[str, SubprocessEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        key: str,
        pid: int,
        project_id: str,
        subprocess_type: str,
    ) -> None:
        """Register a subprocess.

        Args:
            key: Unique key for this entry (e.g. task_id or
                 ``"dev_server:{project_id}"``).
            pid: OS process ID.
            project_id: The project this subprocess belongs to.
            subprocess_type: ``"executor"`` or ``"dev_server"``.

        Raises:
            RuntimeError: If the global subprocess limit is reached.
        """
        if key in self._entries:
            logger.warning("Key %s already registered; overwriting", key)
        elif len(self._entries) >= self._max_total:
            msg = (
                f"Global subprocess limit reached ({self._max_total}). "
                f"Cannot register {key} (pid={pid})."
            )
            raise RuntimeError(msg)

        self._entries[key] = SubprocessEntry(
            pid=pid,
            project_id=project_id,
            subprocess_type=subprocess_type,
            start_time=datetime.now(UTC).isoformat(),
        )
        logger.info(
            "Registered subprocess: key=%s pid=%d type=%s project=%s",
            key, pid, subprocess_type, project_id,
        )

    def deregister(self, key: str) -> None:
        """Remove a subprocess from tracking.

        No-op if the key does not exist.
        """
        entry = self._entries.pop(key, None)
        if entry is not None:
            logger.info(
                "Deregistered subprocess: key=%s pid=%d", key, entry.pid,
            )

    @property
    def count(self) -> int:
        """Total number of tracked subprocesses."""
        return len(self._entries)

    def has_capacity(self) -> bool:
        """Whether another subprocess can be launched."""
        return len(self._entries) < self._max_total

    def list_entries(self) -> dict[str, SubprocessEntry]:
        """Return a copy of all tracked entries."""
        return dict(self._entries)

    def get_by_project(self, project_id: str) -> list[SubprocessEntry]:
        """Return all entries belonging to *project_id*."""
        return [
            e for e in self._entries.values()
            if e.project_id == project_id
        ]

    def get_by_type(self, subprocess_type: str) -> dict[str, SubprocessEntry]:
        """Return all entries of a given type."""
        return {
            k: v for k, v in self._entries.items()
            if v.subprocess_type == subprocess_type
        }

    def cleanup_dead(self) -> list[str]:
        """Remove entries whose PIDs are no longer running.

        Returns:
            List of keys that were removed.
        """
        dead: list[str] = []
        for key, entry in list(self._entries.items()):
            if not _is_process_alive(entry.pid):
                dead.append(key)
                del self._entries[key]
                logger.info(
                    "Cleaned up dead subprocess: key=%s pid=%d",
                    key, entry.pid,
                )
        return dead
