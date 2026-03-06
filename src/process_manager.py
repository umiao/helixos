"""Process manager for launching and stopping project dev servers.

Spawns user-defined launch commands as subprocesses, injects the assigned
PORT env var, tracks them in the SubprocessRegistry, and provides clean
shutdown with a grace period before forced kill.

Windows compatibility:
- Uses ``CREATE_NEW_PROCESS_GROUP`` creation flag so the child gets its
  own process group.
- Sends ``CTRL_BREAK_EVENT`` instead of ``SIGTERM`` for graceful shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from datetime import UTC, datetime

from pydantic import BaseModel

from src.config import OrchestratorConfig, ProjectRegistry
from src.events import EventBus
from src.platform_utils import is_process_alive as _is_process_alive
from src.port_registry import PortRegistry
from src.subprocess_registry import SubprocessRegistry

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Key prefix for SubprocessRegistry entries managed by ProcessManager
_DEV_SERVER_KEY_PREFIX = "dev_server:"


def _dev_server_key(project_id: str) -> str:
    """Return the SubprocessRegistry key for a project's dev server."""
    return f"{_DEV_SERVER_KEY_PREFIX}{project_id}"


# ------------------------------------------------------------------
# Response model
# ------------------------------------------------------------------


class ProcessStatus(BaseModel):
    """Status of a project's dev server process."""

    running: bool
    pid: int | None = None
    port: int | None = None
    uptime_seconds: float | None = None


# ------------------------------------------------------------------
# ProcessManager
# ------------------------------------------------------------------


class ProcessManager:
    """Manages project dev server subprocesses.

    Each project can have at most one running dev server.  Launch injects
    the assigned PORT as an environment variable and registers the process
    in the shared SubprocessRegistry.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        registry: ProjectRegistry,
        port_registry: PortRegistry,
        subprocess_registry: SubprocessRegistry,
        event_bus: EventBus,
    ) -> None:
        """Initialise the ProcessManager.

        Args:
            config: Top-level orchestrator configuration.
            registry: Project registry for looking up project details.
            port_registry: Port registry for port assignments.
            subprocess_registry: Shared subprocess tracker.
            event_bus: Event bus for emitting process events.
        """
        self._config = config
        self._registry = registry
        self._port_registry = port_registry
        self._subprocess_registry = subprocess_registry
        self._event_bus = event_bus
        # Active asyncio subprocess handles, keyed by project_id
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        # Launch times, keyed by project_id
        self._launch_times: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------

    async def launch(self, project_id: str) -> ProcessStatus:
        """Launch the dev server for *project_id*.

        Spawns ``launch_command`` from the project config in the project's
        ``repo_path`` directory, with PORT injected into the environment.

        Args:
            project_id: The project whose dev server to start.

        Returns:
            ProcessStatus with running=True on success.

        Raises:
            ValueError: If the project has no ``launch_command`` or
                ``repo_path`` configured.
            RuntimeError: If already running or global subprocess limit
                is reached.
        """
        # Check not already running
        if project_id in self._processes:
            proc = self._processes[project_id]
            if proc.returncode is None:
                raise RuntimeError(
                    f"Dev server already running for project {project_id}"
                )
            # Process exited -- clean up stale entry
            self._cleanup_stale(project_id)

        # Get project config
        project_config = self._registry.get_project_config(project_id)
        if project_config.launch_command is None:
            msg = f"Project {project_id} has no launch_command configured"
            raise ValueError(msg)
        if project_config.repo_path is None:
            msg = f"Project {project_id} has no repo_path configured"
            raise ValueError(msg)

        # Ensure capacity
        if not self._subprocess_registry.has_capacity():
            msg = "Global subprocess limit reached; cannot launch dev server"
            raise RuntimeError(msg)

        # Get or assign port
        assignment = self._port_registry.get_assignment(project_id)
        if assignment is None:
            port = self._port_registry.assign_port(
                project_id,
                project_config.project_type,
                preferred_port=project_config.preferred_port,
            )
        else:
            port = assignment.port

        # Build environment with PORT injected
        env = dict(os.environ)
        env["PORT"] = str(port)

        # Platform-specific subprocess creation
        kwargs: dict[str, object] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(project_config.repo_path),
            "env": env,
        }
        if _IS_WINDOWS:
            import subprocess as _subprocess
            kwargs["creationflags"] = (
                _subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            )
        else:
            kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_shell(
            project_config.launch_command,
            **kwargs,  # type: ignore[arg-type]
        )

        pid = proc.pid
        if pid is None:
            msg = "Failed to get PID from spawned process"
            raise RuntimeError(msg)

        # Track the process
        self._processes[project_id] = proc
        self._launch_times[project_id] = datetime.now(UTC)

        # Register in SubprocessRegistry
        self._subprocess_registry.register(
            key=_dev_server_key(project_id),
            pid=pid,
            project_id=project_id,
            subprocess_type="dev_server",
        )

        # Update PID in port registry
        self._port_registry.update_pid(project_id, pid)

        # Emit event
        self._event_bus.emit(
            "process_start",
            project_id,
            {"pid": pid, "port": port},
            origin="execution",
        )

        logger.info(
            "Launched dev server for project %s: pid=%d port=%d cmd=%s",
            project_id, pid, port, project_config.launch_command,
        )

        return ProcessStatus(
            running=True,
            pid=pid,
            port=port,
            uptime_seconds=0.0,
        )

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    async def stop(
        self,
        project_id: str,
        timeout: float = 10.0,
    ) -> bool:
        """Stop the dev server for *project_id*.

        Sends a graceful termination signal, waits up to *timeout*
        seconds, then force-kills if still alive.

        Args:
            project_id: The project whose dev server to stop.
            timeout: Seconds to wait before force-kill.

        Returns:
            True if the process was stopped, False if it was not running.
        """
        proc = self._processes.get(project_id)
        if proc is None or proc.returncode is not None:
            # Not running -- clean up any stale state
            self._cleanup_stale(project_id)
            return False

        pid = proc.pid
        logger.info(
            "Stopping dev server for project %s (pid=%d, timeout=%.1fs)",
            project_id, pid, timeout,
        )

        # Graceful termination
        try:
            _terminate_process(proc)
        except OSError:
            logger.warning(
                "Failed to send terminate signal to pid=%d", pid,
                exc_info=True,
            )

        # Wait for graceful exit
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            # Force kill
            logger.warning(
                "Dev server pid=%d did not exit within %.1fs; force killing",
                pid, timeout,
            )
            try:
                proc.kill()
                await proc.wait()
            except OSError:
                logger.warning(
                    "Failed to kill pid=%d", pid, exc_info=True,
                )

        self._cleanup_stale(project_id)

        # Emit event
        self._event_bus.emit(
            "process_stop",
            project_id,
            {"pid": pid},
            origin="execution",
        )

        logger.info("Stopped dev server for project %s (pid=%d)", project_id, pid)
        return True

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self, project_id: str) -> ProcessStatus:
        """Get the current status of a project's dev server.

        Args:
            project_id: The project to check.

        Returns:
            ProcessStatus with running state, PID, port, and uptime.
        """
        proc = self._processes.get(project_id)
        if proc is None or proc.returncode is not None:
            # Check port assignment even if not running
            assignment = self._port_registry.get_assignment(project_id)
            port = assignment.port if assignment is not None else None
            return ProcessStatus(running=False, port=port)

        assignment = self._port_registry.get_assignment(project_id)
        port = assignment.port if assignment is not None else None

        uptime: float | None = None
        launch_time = self._launch_times.get(project_id)
        if launch_time is not None:
            uptime = (datetime.now(UTC) - launch_time).total_seconds()

        return ProcessStatus(
            running=True,
            pid=proc.pid,
            port=port,
            uptime_seconds=uptime,
        )

    # ------------------------------------------------------------------
    # Stop all (shutdown hook)
    # ------------------------------------------------------------------

    async def stop_all(self, timeout: float = 10.0) -> None:
        """Stop all running dev servers.

        Called during application shutdown to ensure no orphan processes.

        Args:
            timeout: Per-process grace period in seconds.
        """
        project_ids = list(self._processes.keys())
        if not project_ids:
            return

        logger.info("Stopping all dev servers (%d running)", len(project_ids))
        for project_id in project_ids:
            try:
                await self.stop(project_id, timeout=timeout)
            except Exception:
                logger.warning(
                    "Error stopping dev server for %s", project_id,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Orphan cleanup (startup)
    # ------------------------------------------------------------------

    def cleanup_orphans(self) -> list[str]:
        """Clean up stale dev server entries from the SubprocessRegistry.

        Called at startup to remove entries for processes that are no
        longer running (e.g. after a crash).

        Returns:
            List of project_ids that were cleaned up.
        """
        cleaned: list[str] = []
        dev_servers = self._subprocess_registry.get_by_type("dev_server")
        for key, entry in dev_servers.items():
            if not _is_process_alive(entry.pid):
                self._subprocess_registry.deregister(key)
                cleaned.append(entry.project_id)
                logger.info(
                    "Cleaned up orphan dev server: project=%s pid=%d",
                    entry.project_id, entry.pid,
                )
        return cleaned

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cleanup_stale(self, project_id: str) -> None:
        """Remove tracking state for a project that is no longer running."""
        self._processes.pop(project_id, None)
        self._launch_times.pop(project_id, None)
        self._subprocess_registry.deregister(_dev_server_key(project_id))


# ------------------------------------------------------------------
# Platform-specific helpers
# ------------------------------------------------------------------


def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    """Send a graceful termination signal to the process.

    On Windows: sends CTRL_BREAK_EVENT to the process group.
    On Unix: sends SIGTERM to the process group.
    """
    pid = proc.pid
    if pid is None:
        return

    if _IS_WINDOWS:
        # CTRL_BREAK_EVENT is sent to the process group created by
        # CREATE_NEW_PROCESS_GROUP
        os.kill(pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    else:
        # Send SIGTERM to the whole process group (kills child procs too)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(pid), signal.SIGTERM)


