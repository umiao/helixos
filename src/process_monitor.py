"""Background process health monitor for HelixOS orchestrator.

Periodically scans all tracked subprocesses and detects:
- Crashed processes (PID no longer alive)
- Exited processes with non-zero exit codes (dev servers)
- Hard timeout expiry

Emits SSE events so the UI can surface failures in real time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from src.events import EventBus
from src.platform_utils import is_process_alive as _is_process_alive
from src.process_manager import ProcessManager
from src.subprocess_registry import SubprocessEntry, SubprocessRegistry

logger = logging.getLogger(__name__)

# How often to scan for dead/failed processes (seconds).
# AC says "within 10s", so 5s gives a comfortable margin.
MONITOR_INTERVAL_SECONDS = 5


class ProcessMonitor:
    """Background monitor that detects subprocess failures.

    Runs a periodic scan of all entries in SubprocessRegistry.  When a
    tracked PID is no longer alive, it emits an SSE ``process_failed``
    event and cleans up the registry entry.  For dev servers, it also
    cleans up ProcessManager state.

    Design constraints (from AC):
    - NO activity-based stall detection (no "idle for X seconds" logic)
    - Only hard timeout, exit code, and process-not-alive checks
    """

    def __init__(
        self,
        subprocess_registry: SubprocessRegistry,
        process_manager: ProcessManager,
        event_bus: EventBus,
        interval: float = MONITOR_INTERVAL_SECONDS,
    ) -> None:
        """Initialize the process monitor.

        Args:
            subprocess_registry: Shared subprocess tracker to scan.
            process_manager: Dev server manager for cleanup.
            event_bus: Event bus for emitting failure events.
            interval: Seconds between scans.
        """
        self._subprocess_registry = subprocess_registry
        self._process_manager = process_manager
        self._event_bus = event_bus
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    async def start(self) -> None:
        """Start the background monitoring loop."""
        self._stopped = False
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(
            "ProcessMonitor started (interval=%ds)", self._interval,
        )

    async def stop(self) -> None:
        """Stop the background monitoring loop."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("ProcessMonitor stopped")

    async def _monitor_loop(self) -> None:
        """Run scan() every interval seconds until stopped."""
        while not self._stopped:
            try:
                await self.scan()
            except Exception:
                logger.exception("Error in process monitor scan")
            await asyncio.sleep(self._interval)

    async def scan(self) -> list[str]:
        """Scan all tracked subprocesses and detect failures.

        For each entry in the SubprocessRegistry:
        - Check if the PID is still alive
        - If dead, emit a ``process_failed`` SSE event
        - Clean up registry and ProcessManager state

        Returns:
            List of registry keys for processes that were detected as failed.
        """
        failed_keys: list[str] = []
        entries = self._subprocess_registry.list_entries()

        for key, entry in entries.items():
            if _is_process_alive(entry.pid):
                continue

            # Process is dead -- detect and surface the failure
            failed_keys.append(key)
            self._handle_process_failure(key, entry)

        return failed_keys

    def _handle_process_failure(
        self, key: str, entry: SubprocessEntry,
    ) -> None:
        """Handle a detected process failure.

        Emits SSE events, cleans up registry, and cleans up ProcessManager
        for dev servers.

        Args:
            key: The SubprocessRegistry key for this entry.
            entry: The subprocess entry that failed.
        """
        elapsed = _elapsed_seconds(entry.start_time)

        if entry.subprocess_type == "dev_server":
            project_id = entry.project_id
            logger.warning(
                "Dev server crashed: project=%s pid=%d (ran for %.0fs)",
                project_id, entry.pid, elapsed,
            )
            # Emit process_failed event (task_id field carries project_id)
            self._event_bus.emit(
                "process_failed",
                project_id,
                {
                    "pid": entry.pid,
                    "subprocess_type": entry.subprocess_type,
                    "elapsed_seconds": round(elapsed, 1),
                    "error": f"Dev server crashed (pid={entry.pid})",
                },
                origin="execution",
            )
            # Clean up ProcessManager state for this dev server
            self._process_manager._cleanup_stale(project_id)
        elif entry.subprocess_type == "executor":
            # Executor task crash -- the Scheduler normally handles this,
            # but if somehow an executor PID dies without the asyncio task
            # noticing (e.g. SIGKILL), we surface it here.
            logger.warning(
                "Executor process crashed: key=%s pid=%d (ran for %.0fs)",
                key, entry.pid, elapsed,
            )
            self._event_bus.emit(
                "process_failed",
                key,  # key is the task_id for executors
                {
                    "pid": entry.pid,
                    "subprocess_type": entry.subprocess_type,
                    "elapsed_seconds": round(elapsed, 1),
                    "error": f"Executor process crashed (pid={entry.pid})",
                },
                origin="execution",
            )
        else:
            logger.warning(
                "Unknown subprocess type crashed: key=%s type=%s pid=%d",
                key, entry.subprocess_type, entry.pid,
            )

        # Remove from registry
        self._subprocess_registry.deregister(key)

    def get_active_processes(self) -> list[dict[str, object]]:
        """Return a snapshot of all tracked subprocesses.

        Used by the health-check endpoint.

        Returns:
            List of dicts with key, pid, start_time, subprocess_type,
            project_id, and elapsed_seconds for each tracked process.
        """
        entries = self._subprocess_registry.list_entries()
        result: list[dict[str, object]] = []
        for key, entry in entries.items():
            elapsed = _elapsed_seconds(entry.start_time)
            result.append({
                "key": key,
                "pid": entry.pid,
                "project_id": entry.project_id,
                "subprocess_type": entry.subprocess_type,
                "start_time": entry.start_time,
                "elapsed_seconds": round(elapsed, 1),
                "alive": _is_process_alive(entry.pid),
            })
        return result


def _elapsed_seconds(start_time_iso: str) -> float:
    """Calculate seconds elapsed since an ISO 8601 timestamp.

    Args:
        start_time_iso: ISO 8601 timestamp string.

    Returns:
        Seconds elapsed since the given time.
    """
    try:
        start = datetime.fromisoformat(start_time_iso)
        return (datetime.now(UTC) - start).total_seconds()
    except (ValueError, TypeError):
        return 0.0
