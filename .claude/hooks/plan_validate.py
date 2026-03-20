#!/usr/bin/env python3
"""Plan validation CLI: checks that planning produced well-specified tasks.

Not a hook -- standalone script called at the end of a planning session.

Checks:
1. Tasks were created/updated during the session
2. Each new task has required spec sections
3. TASKS.md was regenerated recently

Usage:
    python .claude/hooks/plan_validate.py
    python .claude/hooks/plan_validate.py --since "2026-03-19T10:00:00"

Exit codes:
    0 = all checks pass (with optional warnings)
    1 = failures found
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_DB = PROJECT_ROOT / ".claude" / "tasks.db"
TASKS_MD = PROJECT_ROOT / "TASKS.md"
STATE_FILE = PROJECT_ROOT / ".claude" / "state.json"

# Required sections in task description (case-insensitive matching)
REQUIRED_SECTIONS = [
    "summary",
    "context",
    "acceptance criteria",
    "technical approach",
    "edge cases",
    "complexity",
    "dependencies",
]

# Minimum character length for a section to be considered non-empty
MIN_SECTION_LENGTH = 10


def _get_plan_start_time() -> float:
    """Get the plan activation timestamp from state.json.

    Returns:
        Unix timestamp of plan activation, or 0 if not found.
    """
    if not STATE_FILE.exists():
        return 0
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return float(state.get("activated_at", 0))
    except (json.JSONDecodeError, OSError, ValueError):
        return 0


def _get_tasks_since(since_ts: float) -> list[dict]:
    """Query tasks created or updated since the given timestamp.

    Args:
        since_ts: Unix timestamp to filter tasks.

    Returns:
        List of task dicts with id, title, description, created_at, updated_at.
    """
    if not TASKS_DB.exists():
        return []

    iso_since = datetime.fromtimestamp(since_ts, tz=UTC).isoformat()

    conn = sqlite3.connect(str(TASKS_DB))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT id, title, description, created_at, updated_at "
            "FROM tasks WHERE created_at >= ? OR updated_at >= ?",
            (iso_since, iso_since),
        )
        return [dict(row) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _check_spec_sections(description: str) -> list[str]:
    """Check if a task description contains all required spec sections.

    Args:
        description: The task description markdown text.

    Returns:
        List of missing or too-short section names.
    """
    if not description:
        return list(REQUIRED_SECTIONS)

    desc_lower = description.lower()
    missing = []

    for section in REQUIRED_SECTIONS:
        # Look for the section header (## Section or **Section** or Section:)
        patterns = [
            f"## {section}",
            f"**{section}**",
            f"{section}:",
        ]
        found = False
        for pattern in patterns:
            idx = desc_lower.find(pattern)
            if idx >= 0:
                # Check there's meaningful content after the header
                after = description[idx + len(pattern):]
                # Take content until next section header or end
                next_section = len(after)
                for other in REQUIRED_SECTIONS:
                    for p in [f"## {other}", f"**{other}**", f"{other}:"]:
                        pos = after.lower().find(p)
                        if pos > 0:
                            next_section = min(next_section, pos)
                content = after[:next_section].strip()
                if len(content) >= MIN_SECTION_LENGTH:
                    found = True
                    break
        if not found:
            missing.append(section)

    return missing


def _check_tasks_md_freshness(since_ts: float) -> bool:
    """Check if TASKS.md was modified after the plan start time.

    Args:
        since_ts: Unix timestamp to compare against.

    Returns:
        True if TASKS.md exists and was modified after since_ts.
    """
    if not TASKS_MD.exists():
        return False
    return os.path.getmtime(str(TASKS_MD)) > since_ts


def validate(since_iso: str | None = None) -> int:
    """Run all validation checks.

    Args:
        since_iso: Optional ISO timestamp string to use as start time.
            If None, reads from state.json.

    Returns:
        0 if all checks pass, 1 if any fail.
    """
    if since_iso:
        since_ts = datetime.fromisoformat(since_iso).timestamp()
    else:
        since_ts = _get_plan_start_time()

    if since_ts == 0:
        print("[WARN] Could not determine plan start time. Checking all tasks.")
        since_ts = 0

    failures: list[str] = []
    warnings: list[str] = []

    # Check 1: Tasks created/updated
    tasks = _get_tasks_since(since_ts)
    if not tasks:
        failures.append("No tasks were created or updated during this planning session.")
    else:
        print(f"[OK] {len(tasks)} task(s) created/updated during session.")

    # Check 2: Spec section completeness
    for task in tasks:
        missing = _check_spec_sections(task.get("description", ""))
        if missing:
            task_id = task["id"]
            title = task["title"]
            warnings.append(
                f"  {task_id} ({title}): missing sections: {', '.join(missing)}"
            )

    if warnings:
        print(f"[WARN] {len(warnings)} task(s) have incomplete specs:")
        for w in warnings:
            print(w)
    elif tasks:
        print("[OK] All tasks have complete spec sections.")

    # Check 3: TASKS.md freshness
    if _check_tasks_md_freshness(since_ts):
        print("[OK] TASKS.md was regenerated.")
    else:
        failures.append("TASKS.md was not regenerated after planning. Run task_db.py to refresh.")

    # Summary
    if failures:
        print(f"\n[FAIL] {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\n[PASS] Planning validation complete.")
    return 0


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Validate planning session output.")
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="ISO timestamp to filter tasks (default: reads from state.json)",
    )
    args = parser.parse_args()
    sys.exit(validate(args.since))


if __name__ == "__main__":
    main()
