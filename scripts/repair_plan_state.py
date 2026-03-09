"""Repair inconsistent plan state in the helixos database.

Finds and fixes tasks that violate plan state machine invariants
introduced in T-P0-134. Run after deploying the state machine to
clean up pre-existing data.

Usage:
    python scripts/repair_plan_state.py          # dry-run (report only)
    python scripts/repair_plan_state.py --fix     # apply fixes

Invariants enforced:
    - plan_status='none'  => plan_json IS NULL, description='',
                             has_proposed_tasks=False, plan_generation_id IS NULL
    - plan_status='ready' => plan_json IS NOT NULL
    - plan_status='generating' should not persist (orphaned)
"""

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".helixos" / "state.db"

# Queries that detect invariant violations
INCONSISTENCY_QUERIES = {
    "none_with_stale_data": {
        "description": "plan_status='none' but has leftover description/plan_json/flags",
        "detect": """
            SELECT id, title, plan_json IS NOT NULL AS has_plan_json,
                   LENGTH(description) AS desc_len, has_proposed_tasks,
                   plan_generation_id IS NOT NULL AS has_gen_id
            FROM tasks
            WHERE plan_status = 'none'
              AND (plan_json IS NOT NULL
                   OR description != ''
                   OR has_proposed_tasks = 1
                   OR plan_generation_id IS NOT NULL)
        """,
        "fix": """
            UPDATE tasks
            SET description = '',
                plan_json = NULL,
                has_proposed_tasks = 0,
                plan_generation_id = NULL
            WHERE plan_status = 'none'
              AND (plan_json IS NOT NULL
                   OR description != ''
                   OR has_proposed_tasks = 1
                   OR plan_generation_id IS NOT NULL)
        """,
    },
    "ready_no_plan_json": {
        "description": "plan_status='ready' but plan_json IS NULL (missing plan data)",
        "detect": """
            SELECT id, title, LENGTH(description) AS desc_len,
                   has_proposed_tasks
            FROM tasks
            WHERE plan_status = 'ready' AND plan_json IS NULL
        """,
        "fix": """
            UPDATE tasks
            SET plan_status = 'none',
                description = '',
                plan_json = NULL,
                has_proposed_tasks = 0,
                plan_generation_id = NULL
            WHERE plan_status = 'ready' AND plan_json IS NULL
        """,
    },
    "generating_orphaned": {
        "description": "plan_status='generating' (orphaned, no active generation)",
        "detect": """
            SELECT id, title, plan_generation_id
            FROM tasks
            WHERE plan_status = 'generating'
        """,
        "fix": """
            UPDATE tasks
            SET plan_status = 'none',
                description = '',
                plan_json = NULL,
                has_proposed_tasks = 0,
                plan_generation_id = NULL
            WHERE plan_status = 'generating'
        """,
    },
}


def main() -> None:
    """Run plan state integrity check and optional repair."""
    # Ensure UTF-8 output on Windows
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    parser = argparse.ArgumentParser(
        description="Check and repair plan state inconsistencies"
    )
    parser.add_argument(
        "--fix", action="store_true", help="Apply fixes (default is dry-run)"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Path to state.db (default: {DB_PATH})",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"[FAIL] Database not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row

    total_inconsistent = 0
    total_fixed = 0

    for name, spec in INCONSISTENCY_QUERIES.items():
        rows = conn.execute(spec["detect"]).fetchall()
        count = len(rows)
        total_inconsistent += count

        status = "[FOUND]" if count > 0 else "[OK]"
        print(f"{status} {name}: {count} rows -- {spec['description']}")

        if count > 0 and count <= 10:
            for r in rows:
                task_id = r["id"]
                title = (r["title"] or "")[:50]
                print(f"       {task_id}  {title}")

        if count > 0 and args.fix:
            cursor = conn.execute(spec["fix"])
            fixed = cursor.rowcount
            total_fixed += fixed
            print(f"  [FIXED] {fixed} rows repaired")

    if args.fix and total_fixed > 0:
        conn.commit()
        print(f"\n[DONE] Committed {total_fixed} fixes to {args.db}")
    elif args.fix:
        print("\n[DONE] No fixes needed")
    else:
        if total_inconsistent > 0:
            print(f"\n[DRY-RUN] {total_inconsistent} inconsistent rows found. "
                  "Run with --fix to repair.")
        else:
            print("\n[OK] All plan state invariants hold. No fixes needed.")

    # Verification pass
    print("\n--- Verification ---")
    remaining = 0
    for name, spec in INCONSISTENCY_QUERIES.items():
        count = len(conn.execute(spec["detect"]).fetchall())
        remaining += count
        status = "[OK]" if count == 0 else "[FAIL]"
        print(f"{status} {name}: {count} remaining")

    conn.close()

    if args.fix and remaining > 0:
        print(f"\n[FAIL] {remaining} inconsistencies remain after fix!")
        sys.exit(1)

    sys.exit(0 if remaining == 0 or not args.fix else 1)


if __name__ == "__main__":
    main()
