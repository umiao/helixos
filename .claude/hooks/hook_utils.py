"""Shared utilities for Claude Code hooks.

Provides:
- UTF-8 stream initialization for Windows
- Safe JSON parsing from stdin with diagnostics
- Top-level exception wrapper for hook main() functions
- Stop hook caching (skip re-run if no files changed)
"""
import contextlib
import hashlib
import io
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def init_utf8_streams() -> None:
    """Force UTF-8 encoding on stdin/stdout/stderr for Windows.

    On Windows, the default encoding is often cp1252, which cannot handle
    Chinese characters in paths or emoji. This wraps all three standard
    streams with UTF-8 TextIOWrapper using errors='replace'.

    Safe to call on non-Windows platforms (no-op).
    """
    if sys.platform == "win32":
        # Guard: only wrap if .buffer exists (won't on StringIO in tests)
        if hasattr(sys.stdin, "buffer"):
            sys.stdin = io.TextIOWrapper(
                sys.stdin.buffer, encoding="utf-8", errors="replace"
            )
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8", errors="replace"
            )
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8", errors="replace"
            )


def safe_read_stdin(hook_name: str) -> dict[str, Any] | None:
    """Read and parse JSON from stdin with full error handling.

    On success, returns the parsed dict.
    On failure (empty stdin, malformed JSON, encoding errors, etc.):
      - Prints detailed diagnostics to stderr
      - Returns None

    Args:
        hook_name: Name of the calling hook, used in diagnostic output.

    Returns:
        Parsed JSON dict, or None if parsing failed.
    """
    raw_input = ""
    try:
        raw_input = sys.stdin.read()
    except Exception as exc:
        print(
            f"[HOOK ERROR] {hook_name}: Failed to read stdin.\n"
            f"  Exception: {type(exc).__name__}: {exc}\n"
            f"  This is a hook infrastructure error, not a project issue.",
            file=sys.stderr,
        )
        return None

    if not raw_input or not raw_input.strip():
        print(
            f"[HOOK ERROR] {hook_name}: Received empty stdin.\n"
            f"  Input length: {len(raw_input)} bytes\n"
            f"  This is a hook infrastructure error, not a project issue.",
            file=sys.stderr,
        )
        return None

    try:
        parsed = json.loads(raw_input)
    except (json.JSONDecodeError, ValueError) as exc:
        preview = raw_input[:500]
        if len(raw_input) > 500:
            preview += f"... [{len(raw_input) - 500} more chars]"
        print(
            f"[HOOK ERROR] {hook_name}: Failed to parse JSON from stdin.\n"
            f"  Exception: {type(exc).__name__}: {exc}\n"
            f"  Input length: {len(raw_input)} chars\n"
            f"  Input preview: {preview!r}\n"
            f"  This is a hook infrastructure error, not a project issue.",
            file=sys.stderr,
        )
        return None

    if not isinstance(parsed, dict):
        print(
            f"[HOOK ERROR] {hook_name}: Expected JSON object (dict), "
            f"got {type(parsed).__name__}.\n"
            f"  This is a hook infrastructure error, not a project issue.",
            file=sys.stderr,
        )
        return None

    return parsed


def _get_repo_fingerprint() -> str:
    """Compute a fingerprint of the current repo state (staged + unstaged + untracked).

    Uses `git status --porcelain` output hashed to a short hex string.
    Returns empty string if git is unavailable or not in a repo.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        if result.returncode != 0:
            return ""
        return hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()[:16]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def check_stop_cache(cache_name: str) -> bool:
    """Check if a stop hook can skip re-running based on cached results.

    Args:
        cache_name: Identifier for the cache file (e.g., "lint", "test").

    Returns:
        True if cache is valid (no files changed since last pass) -- caller should skip.
        False if cache is stale or missing -- caller should run checks.
    """
    cache_dir = Path(__file__).resolve().parent.parent  # .claude/
    cache_file = cache_dir / f"last_{cache_name}_pass"

    if not cache_file.exists():
        return False

    try:
        stored = cache_file.read_text(encoding="utf-8").strip()
    except OSError:
        return False

    current = _get_repo_fingerprint()
    if not current:
        return False  # Can't determine state, run checks

    return stored == current


def write_stop_cache(cache_name: str) -> None:
    """Record that a stop hook passed successfully.

    Args:
        cache_name: Identifier for the cache file (e.g., "lint", "test").
    """
    cache_dir = Path(__file__).resolve().parent.parent  # .claude/
    cache_file = cache_dir / f"last_{cache_name}_pass"

    fingerprint = _get_repo_fingerprint()
    if not fingerprint:
        return  # Can't determine state, don't cache

    with contextlib.suppress(OSError):
        cache_file.write_text(fingerprint, encoding="utf-8")


def run_hook(hook_name: str, main_fn: Callable[[dict[str, Any]], None]) -> None:
    """Top-level entry point for a hook script.

    Handles the complete lifecycle:
    1. Initializes UTF-8 streams
    2. Reads and parses JSON from stdin
    3. On parse failure: warns to stderr, exits 0 (pass-through)
    4. Calls main_fn(hook_input) with the parsed dict
    5. Catches ANY unhandled exception from main_fn, prints diagnostics,
       and exits 0 (never blocks the user due to hook bugs)
    6. Emits JSON to stdout on ALL exit paths (Claude Code expects
       ``{"ok": true/false}`` from stop hooks)

    Args:
        hook_name: Identifier for the hook (used in error messages).
        main_fn: The hook's business logic function. Takes the parsed
                 hook_input dict. Should call sys.exit() with the
                 appropriate code when done.
    """
    init_utf8_streams()

    hook_input = safe_read_stdin(hook_name)
    if hook_input is None:
        # Stdin failure is infrastructure error, don't block user
        print(json.dumps({"ok": True, "reason": "invalid hook input"}))
        sys.exit(0)

    try:
        main_fn(hook_input)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        if code == 0:
            print(json.dumps({"ok": True}))
        else:
            print(json.dumps({"ok": False, "reason": f"{hook_name} failed (exit {code})"}))
        sys.exit(code)
    except Exception as exc:
        print(
            f"[HOOK ERROR] {hook_name}: Unhandled exception: {exc}",
            file=sys.stderr,
        )
        print(json.dumps({"ok": True}))  # don't block on hook bugs
        sys.exit(0)

    # main_fn returned without sys.exit()
    print(json.dumps({"ok": True}))
    sys.exit(0)
