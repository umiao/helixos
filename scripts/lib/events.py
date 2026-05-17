"""Atomic events log + rotation (T-P1-309 / INFRA-HITL B7).

Every state transition in the HITL pipeline appends one JSON line to
``<root>/.claude/events.jsonl``. This module is the single shared writer:
all bridge scripts (task_complete.py, sweep_stuck_leases.py) and the
canonical task store import :func:`append` here, so the on-disk schema is
defined in exactly one place.

Schema (per task description, INV-MT-aware)::

    {
      "ts":         "<ISO 8601 UTC>",        # required, "%Y-%m-%dT%H:%M:%S"
      "project_id": "<str>",                  # required, default 'root'
      "task_id":    "<T-...>",               # required
      "from_state": "<str|null>",            # required (null on initial create)
      "to_state":   "<str>",                  # required
      "actor":      "<str>",                  # required (script/cli name)
      "plan_content_hash": "<sha256-hex>"    # OPTIONAL (B5/B6 approval flow)
    }

Additional fields (e.g. ``reason``, ``lease_age_s``, ``ttl_s``, ``pid``)
are allowed -- consumers must ignore unknown keys. Lines are written
sorted-keys so a byte-equal lookup is possible across writers.

Concurrency contract:
    - Each :func:`append` acquires an exclusive OS lock on a sentinel file
      (``.claude/events.jsonl.lock``) before writing. ``fcntl.flock`` on
      Linux/macOS, ``msvcrt.locking`` on Windows.
    - The append uses a single ``write()`` call on a file opened with
      ``O_APPEND`` semantics, so even without the OS lock individual
      writes would be POSIX-atomic up to PIPE_BUF (~4 KiB). The lock is
      belt-and-suspenders -- it also serialises the rotation check.
    - A ``fsync`` is best-effort: failures are swallowed (some MSYS
      mounts return EINVAL on regular files; that should not abort the
      caller's state transition).

Rotation:
    When the current ``events.jsonl`` plus the line about to be written
    would exceed :data:`MAX_BYTES` (100 MiB), the file is renamed to
    ``events.jsonl.<unix_epoch>`` (with ``.<n>`` suffix on collision)
    and a fresh ``events.jsonl`` is started. Rotation happens under the
    same lock as the write, so no event is lost during rollover.

Reader:
    :func:`iter_events` yields parsed events in chronological order. By
    default only the active ``events.jsonl`` is read; pass
    ``include_rotated=True`` to walk rotated archives first
    (epoch-ascending), then the current file. ``--since`` and
    ``--task_id`` filters are applied lazily.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

# Default rotation threshold. Override via CLAUDE_EVENTS_MAX_BYTES env
# (mostly for tests -- prod default is ~100 MiB).
MAX_BYTES = int(os.environ.get("CLAUDE_EVENTS_MAX_BYTES", str(100 * 1024 * 1024)))

# Required schema keys -- :func:`append` validates that callers supply them.
REQUIRED_FIELDS: tuple[str, ...] = (
    "ts", "project_id", "task_id", "from_state", "to_state", "actor",
)


# --- Cross-platform file locking ------------------------------------------

if sys.platform.startswith("win"):
    import msvcrt

    def _lock(fd: int) -> None:
        # msvcrt.locking locks a byte range from current file position.
        # We always seek to byte 0 of a 1-byte sentinel file before locking.
        # LK_LOCK blocks the calling process until the lock is acquired
        # (with internal retries every ~1s; we wrap our own retry loop
        # for additional resilience under heavy contention).
        os.lseek(fd, 0, os.SEEK_SET)
        deadline = time.monotonic() + 30.0
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            # If unlock races with FD close, swallow; the lock dies with the FD.
            pass
else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


# --- Path helpers ---------------------------------------------------------


def events_path(root: Path) -> Path:
    """Return the active events log path under <root>/.claude/."""
    return Path(root) / ".claude" / "events.jsonl"


def _lock_path(root: Path) -> Path:
    return Path(root) / ".claude" / "events.jsonl.lock"


def _ensure_lockfile(lockfile: Path) -> None:
    """Create the 1-byte sentinel lock file if absent.

    msvcrt.locking requires that the file have at least the byte being
    locked, so we initialise with a single null byte. fcntl.flock has no
    such requirement but harmless to do the same on POSIX.
    """
    if lockfile.exists() and lockfile.stat().st_size >= 1:
        return
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    # Open + write atomically; race on concurrent first-open is harmless
    # because both writers would write the same single null byte.
    try:
        with open(lockfile, "ab") as f:
            if f.tell() == 0:
                f.write(b"\0")
    except OSError as exc:
        sys.stderr.write(f"WARN: events lockfile init failed: {exc}\n")


# --- Rotation -------------------------------------------------------------


def _rotate(target: Path) -> Path | None:
    """Rename the active events log to events.jsonl.<epoch>.

    Returns the rotated path, or None if the active file did not exist.
    Callers must hold the lock when invoking this.
    """
    if not target.exists():
        return None
    epoch = int(time.time())
    rotated = target.with_name(f"{target.name}.{epoch}")
    n = 0
    while rotated.exists():
        n += 1
        rotated = target.with_name(f"{target.name}.{epoch}.{n}")
    target.rename(rotated)
    return rotated


# --- Public API: append ---------------------------------------------------


def append(root: Path | str, payload: dict[str, Any]) -> None:
    """Append one event line to <root>/.claude/events.jsonl.

    Acquires an exclusive lock, checks rotation, writes the JSON line,
    then releases. Best-effort fsync.

    Args:
        root: Project root (workspace root or sub-project root).
        payload: Event dict. Must contain all REQUIRED_FIELDS. Any extra
            fields are preserved as-is. Keys are sorted on disk.

    Raises:
        ValueError: If a required field is missing.

    Note:
        Failures to write (disk full, permission denied, etc.) are
        swallowed with a stderr WARN. State transitions in the bridge
        scripts MUST NOT abort because of an events log failure -- the
        forensic trail is best-effort by design.
    """
    root_p = Path(root)
    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        raise ValueError(
            f"events.append: missing required fields {missing}; "
            f"payload keys={sorted(payload)}"
        )

    target = events_path(root_p)
    lockfile = _lock_path(root_p)
    target.parent.mkdir(parents=True, exist_ok=True)
    _ensure_lockfile(lockfile)

    line = json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n"
    encoded = line.encode("utf-8")

    try:
        # Open lockfile in r+b (so msvcrt has a writable fd to lock against).
        with open(lockfile, "rb+") as lf:
            _lock(lf.fileno())
            try:
                # Rotate if this write would tip us over MAX_BYTES.
                try:
                    cur = target.stat().st_size if target.exists() else 0
                except OSError:
                    cur = 0
                if cur + len(encoded) > MAX_BYTES and cur > 0:
                    _rotate(target)
                # Append.
                with open(target, "ab") as ev:
                    ev.write(encoded)
                    ev.flush()
                    try:
                        os.fsync(ev.fileno())
                    except OSError:
                        # MSYS quirk on some mounts; durability still
                        # guaranteed at OS-level write boundary.
                        pass
            finally:
                _unlock(lf.fileno())
    except OSError as exc:
        sys.stderr.write(f"WARN: events.append failed ({exc}); event lost\n")


# --- Public API: read / replay -------------------------------------------


_ROTATION_RE = re.compile(r"^events\.jsonl\.(\d+)(?:\.(\d+))?$")


def _rotation_sort_key(p: Path) -> tuple[int, int]:
    """Sort rotated files in ascending creation order.

    Filenames look like ``events.jsonl.<epoch>`` or
    ``events.jsonl.<epoch>.<n>`` for collision suffixes. Sort by epoch
    first, then by collision counter.
    """
    m = _ROTATION_RE.match(p.name)
    if not m:
        return (0, 0)
    epoch = int(m.group(1))
    nseq = int(m.group(2)) if m.group(2) else 0
    return (epoch, nseq)


def _files_to_read(root: Path, *, include_rotated: bool) -> list[Path]:
    target = events_path(root)
    files: list[Path] = []
    if include_rotated:
        for p in sorted(target.parent.glob("events.jsonl.*"),
                        key=_rotation_sort_key):
            # Skip the lockfile (events.jsonl.lock would otherwise match
            # the glob; sort_key returns (0,0) which would put it first).
            if p.name.endswith(".lock"):
                continue
            files.append(p)
    if target.exists():
        files.append(target)
    return files


def iter_events(
    root: Path | str,
    *,
    since: str | None = None,
    task_id: str | None = None,
    project_id: str | None = None,
    include_rotated: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield events in chronological (file, line) order.

    Args:
        root: Project root.
        since: Optional ISO 8601 prefix filter (lexicographic compare on
            the ``ts`` field; ``"2026-05-10"`` matches everything from
            that date onward because timestamps are zero-padded).
        task_id: Optional exact-match filter on ``task_id``.
        project_id: Optional exact-match filter on ``project_id``.
        include_rotated: Walk rotated archives first.

    Yields:
        Parsed event dicts. Lines that fail JSON decode are silently
        skipped (the active writer never produces malformed lines, but
        a partially-written line at end-of-file during a crash could
        appear -- best to read past it than abort replay).
    """
    root_p = Path(root)
    for f in _files_to_read(root_p, include_rotated=include_rotated):
        try:
            with open(f, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if since is not None and str(evt.get("ts", "")) < since:
                        continue
                    if task_id is not None and evt.get("task_id") != task_id:
                        continue
                    if project_id is not None and evt.get("project_id") != project_id:
                        continue
                    yield evt
        except OSError:
            continue


# --- Convenience: ts builder ---------------------------------------------


def now_iso() -> str:
    """Return current UTC time as ``%Y-%m-%dT%H:%M:%S`` (matches B4).

    Kept here so callers don't need to import datetime just to build the
    ``ts`` field, and so the format is consistent across all writers.
    """
    import datetime
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S")
