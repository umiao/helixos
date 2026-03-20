#!/usr/bin/env python3
"""Plan mode state manager CLI.

Manages .claude/state.json to track whether the session is in plan-only mode.
This is a CLI utility, NOT a hook -- does not use run_hook().

Commands:
    activate     -- Enter plan mode (blocks mutating tools via hook)
    deactivate   -- Exit plan mode
    is-active    -- Exit 0 if active, exit 1 if not
    status       -- Print current state as human-readable text
    record-drift -- Increment drift counter (called by hook on each block)

Usage:
    python .claude/hooks/plan_mode.py activate
    python .claude/hooks/plan_mode.py deactivate
    python .claude/hooks/plan_mode.py is-active
    python .claude/hooks/plan_mode.py status
    python .claude/hooks/plan_mode.py record-drift
"""

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_FILE = PROJECT_ROOT / ".claude" / "state.json"

DEFAULT_TTL_MINUTES = 120


def _read_state() -> dict:
    """Read state.json, returning empty dict if missing or corrupt."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict) -> None:
    """Write state.json atomically via tmp + rename."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    # On Windows, rename fails if target exists -- remove first
    if STATE_FILE.exists():
        os.replace(str(tmp_path), str(STATE_FILE))
    else:
        tmp_path.rename(STATE_FILE)


def _generate_plan_id() -> str:
    """Generate a plan ID like PLAN-2026-03-19-001."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    # Find existing plans from today to increment counter
    state = _read_state()
    existing_id = state.get("plan_id", "")
    if existing_id.startswith(f"PLAN-{today}-"):
        try:
            counter = int(existing_id.split("-")[-1]) + 1
        except (ValueError, IndexError):
            counter = 1
    else:
        counter = 1
    return f"PLAN-{today}-{counter:03d}"


def is_active() -> bool:
    """Check if plan mode is active and not expired.

    Returns:
        True if plan mode is active and TTL has not expired.
    """
    state = _read_state()
    if state.get("mode") != "plan":
        return False

    # Check TTL expiry
    activated_at = state.get("activated_at", 0)
    ttl_minutes = state.get("ttl_minutes", DEFAULT_TTL_MINUTES)
    elapsed_minutes = (time.time() - activated_at) / 60

    if elapsed_minutes > ttl_minutes:
        # Auto-deactivate on expiry
        state["mode"] = "normal"
        state["expired"] = True
        _write_state(state)
        return False

    return True


def activate() -> None:
    """Activate plan mode."""
    plan_id = _generate_plan_id()
    state = {
        "mode": "plan",
        "plan_id": plan_id,
        "activated_at": time.time(),
        "ttl_minutes": DEFAULT_TTL_MINUTES,
        "drift_count": 0,
    }
    _write_state(state)
    print(f"[PLAN MODE] Activated. ID: {plan_id}, TTL: {DEFAULT_TTL_MINUTES}min")


def deactivate() -> None:
    """Deactivate plan mode, preserving drift count for summary."""
    state = _read_state()
    drift_count = state.get("drift_count", 0)
    state["mode"] = "normal"
    state["deactivated_at"] = time.time()
    _write_state(state)
    print(f"[PLAN MODE] Deactivated. Drift attempts blocked: {drift_count}")


def record_drift() -> None:
    """Increment drift counter (called by hook on each block)."""
    state = _read_state()
    state["drift_count"] = state.get("drift_count", 0) + 1
    _write_state(state)


def status() -> None:
    """Print current plan mode status."""
    state = _read_state()
    mode = state.get("mode", "normal")

    if mode != "plan":
        print("[PLAN MODE] Inactive")
        return

    plan_id = state.get("plan_id", "unknown")
    activated_at = state.get("activated_at", 0)
    ttl_minutes = state.get("ttl_minutes", DEFAULT_TTL_MINUTES)
    drift_count = state.get("drift_count", 0)
    elapsed = (time.time() - activated_at) / 60
    remaining = max(0, ttl_minutes - elapsed)

    print("[PLAN MODE] Active")
    print(f"  Plan ID:    {plan_id}")
    print(f"  Elapsed:    {elapsed:.1f} min")
    print(f"  Remaining:  {remaining:.1f} min")
    print(f"  Drift blocks: {drift_count}")


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: plan_mode.py <activate|deactivate|is-active|status|record-drift>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "activate":
        activate()
    elif command == "deactivate":
        deactivate()
    elif command == "is-active":
        sys.exit(0 if is_active() else 1)
    elif command == "status":
        status()
    elif command == "record-drift":
        record_drift()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
