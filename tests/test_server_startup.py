"""Real server startup smoke test (subprocess-based).

Unlike the mock-based tests in test_windows_asyncio.py, this test actually
starts the server as a subprocess and verifies HTTP connectivity.

Marked @pytest.mark.slow so regular `pytest -x -q` skips it.
Run with: pytest tests/test_server_startup.py -v
   or:    pytest -m slow
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Timeout for server to emit "Application startup complete" (seconds)
STARTUP_TIMEOUT = 15

# Timeout for HTTP response (seconds)
HTTP_TIMEOUT = 5


def _find_free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_startup(proc: subprocess.Popen, timeout: float) -> None:
    """Block until 'Application startup complete' appears in output."""
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            # Process exited early
            rc = proc.poll()
            if rc is not None:
                raise RuntimeError(
                    f"Server process exited with code {rc} before startup completed"
                )
            continue
        if "Application startup complete" in line:
            return

    raise TimeoutError(
        f"Server did not emit 'Application startup complete' within {timeout}s"
    )


@pytest.mark.slow
def test_server_starts_and_responds() -> None:
    """Real smoke test: server starts, serves HTTP, shuts down cleanly.

    Starts run_server.py as a subprocess on a random free port, waits for
    the startup message, hits GET /api/projects, then shuts down.
    """
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "scripts/run_server.py",
            "--no-reload",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        cwd=str(PROJECT_ROOT),
    )
    try:
        _wait_for_startup(proc, timeout=STARTUP_TIMEOUT)

        # Hit an API endpoint to verify HTTP is working
        url = f"http://127.0.0.1:{port}/api/projects"
        resp = urllib.request.urlopen(url, timeout=HTTP_TIMEOUT)
        assert resp.status == 200, f"Expected 200, got {resp.status}"

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
