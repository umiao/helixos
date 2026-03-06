"""Platform-aware utility functions.

Shared helpers that require ``sys.platform`` guards, extracted to avoid
duplication across modules.
"""

from __future__ import annotations

import os
import sys


def is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID is still running.

    On Windows, ``os.kill(pid, 0)`` sends ``CTRL_C_EVENT`` (signal 0 ==
    ``CTRL_C_EVENT``) instead of probing the process, so we use the
    Win32 ``OpenProcess`` API directly.
    """
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            0x1000, False, pid
        )  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
