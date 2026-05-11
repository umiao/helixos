#!/usr/bin/env python3
"""State-based picker + introspection CLI (T-P1-305 / INFRA-HITL B3).

Bridge between the legacy ``status='active'`` picker and the new
state-machine picker (``state='ready'``) introduced by B1. Lives in scripts/
because ``.claude/hooks/task_store.py`` and ``.claude/hooks/task_db.py`` are
sensitive in autonomous mode and could not be edited from this task. A
follow-up task (mirror of T-P1-319 for B2) will fold this logic into the
TaskStore class and expose ``task_db.py pick`` / ``task_db.py why-blocked``
natively.

Picker contract (post-B1 state machine):
    A task is *pickable* iff
        state == 'ready'
        AND all upstream dependencies are in state='done'
        AND no lease is held (state != 'leased' AND pid/started_at null)
    The ``status`` column is intentionally ignored -- B1 backfilled state
    from status, and from B3 onward state is the single source of truth.

Subcommands:
    pick                    -- print the highest-priority pickable task ID, or
                               "none" (exit 1 if none). Order: priority
                               ascending (P0 first), then sort_order, then id.
    why-blocked <task_id>   -- emit JSON explaining why a task isn't pickable:
                                {id, title, state, deps_missing[],
                                 approval_missing, lease_held_by,
                                 human_review_pending, pickable, reasons[]}
                               exit 0 if pickable, exit 1 if blocked.
    has-unblocked           -- backcompat: prints 'yes' (exit 0) if any task
                               is pickable, else 'no' (exit 1). Mirrors the
                               text-and-exit shape of
                               ``task_db.py has-unblocked`` so existing
                               orchestrator hooks can swap callsite with no
                               other change.

Reads/writes the SQLite DB directly; does not import task_store.

Usage:
    python scripts/task_pick.py [--project <name>] pick
    python scripts/task_pick.py [--project <name>] why-blocked T-P1-305
    python scripts/task_pick.py [--project <name>] has-unblocked
    python scripts/task_pick.py --db-path <path> ...        # tests
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# Priority order: lower index = higher priority. Matches task_store ordering.
_PRIORITY_ORDER = ("P0", "P1", "P2", "P3")

# States that mean "task is done from work-perspective but awaiting human gate".
# Surface these as human_review_pending=true so the portal can highlight them.
_REVIEW_STATES = ("review_pending", "rejected", "revision_requested")


def _root_for(project: str | None) -> Path:
    """Resolve the project root for a given ``--project`` argument."""
    if not project or project == "root":
        return REPO_ROOT
    return REPO_ROOT / project


def _db_path(project: str | None, override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    return _root_for(project) / ".claude" / "tasks.db"


def _ensure_migrated(con: sqlite3.Connection) -> None:
    cols = {c[1] for c in con.execute("PRAGMA table_info(tasks)")}
    missing = {"state", "human_review", "project_id"} - cols
    if missing:
        sys.stderr.write(
            f"ERROR: target DB is missing migrated columns {sorted(missing)}. "
            "Run `python scripts/migrate/02_core_migration.py --apply` first.\n"
        )
        sys.exit(2)


def _approval_path(root: Path, task_id: str) -> Path:
    return root / ".claude" / "approvals" / f"{task_id}.yaml"


def _fetch_task(con: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    con.row_factory = sqlite3.Row
    row = con.execute(
        """SELECT id, title, status, state, human_review, project_id,
                  priority, complexity, sort_order,
                  pid, pgid, started_at, ttl_seconds, last_heartbeat
           FROM tasks WHERE id=?""",
        (task_id,),
    ).fetchone()
    return dict(row) if row else None


def _fetch_deps(
    con: sqlite3.Connection, task_id: str
) -> list[dict[str, Any]]:
    """Return upstream dependencies with their current state."""
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT t.id, t.state, t.status
           FROM task_dependencies td
           JOIN tasks t ON td.upstream_id = t.id
           WHERE td.downstream_id = ?
           ORDER BY t.id""",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _classify(
    task: dict[str, Any],
    deps: list[dict[str, Any]],
    root: Path,
) -> dict[str, Any]:
    """Compute the why-blocked result dict (also reused by `pick`).

    Pure function over the row + deps + root. Does not touch the DB.
    """
    state = task["state"]
    task_id = task["id"]

    deps_missing = [d["id"] for d in deps if d["state"] != "done"]

    approval_missing: str | None = None
    if state in _REVIEW_STATES:
        approval_missing = str(_approval_path(root, task_id))

    lease_held_by: dict[str, Any] | None = None
    if state == "leased" and (task["pid"] is not None
                              or task["started_at"] is not None):
        lease_held_by = {
            "pid": task["pid"],
            "pgid": task["pgid"],
            "started_at": task["started_at"],
            "ttl_seconds": task["ttl_seconds"],
            "last_heartbeat": task["last_heartbeat"],
        }

    human_review_pending = state in _REVIEW_STATES

    # Pickable iff: state==ready, no missing deps, no lease.
    pickable = (
        state == "ready"
        and not deps_missing
        and lease_held_by is None
    )

    reasons: list[str] = []
    if state != "ready":
        if state in _REVIEW_STATES:
            reasons.append(
                f"{state}; awaiting {approval_missing}"
            )
        elif state == "leased":
            assert lease_held_by is not None
            ttl = lease_held_by.get("ttl_seconds")
            ttl_str = f"{ttl}s" if ttl is not None else "unset"
            reasons.append(
                f"lease_held_by: pid={lease_held_by['pid']} "
                f"pgid={lease_held_by['pgid']} "
                f"started_at={lease_held_by['started_at']} "
                f"ttl={ttl_str}"
            )
        else:
            reasons.append(f"state={state!r} (not 'ready')")
    if deps_missing:
        reasons.append(f"deps_missing: {deps_missing}")
    # Edge: state=ready but a stale lease row -- shouldn't happen by schema,
    # but surface if it does.
    if state == "ready" and lease_held_by is not None:
        reasons.append("stale lease on a ready task (data drift)")

    return {
        "id": task_id,
        "title": task["title"],
        "state": state,
        "deps_missing": deps_missing,
        "approval_missing": approval_missing,
        "lease_held_by": lease_held_by,
        "human_review_pending": human_review_pending,
        "pickable": pickable,
        "reasons": reasons,
    }


def _list_pickable(
    con: sqlite3.Connection, root: Path
) -> list[dict[str, Any]]:
    """Return all pickable tasks ordered by priority, sort_order, id."""
    con.row_factory = sqlite3.Row
    # state='ready' AND not leased is most of the filter; deps must be checked
    # per-row because SQL can't express ALL(deps.state='done') cleanly without
    # a correlated subquery, which is fine but readability wins here.
    candidates = con.execute(
        """SELECT id FROM tasks
           WHERE state='ready'
             AND (pid IS NULL AND started_at IS NULL)
           ORDER BY priority ASC, sort_order ASC, id ASC"""
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in candidates:
        task = _fetch_task(con, row["id"])
        assert task is not None
        deps = _fetch_deps(con, row["id"])
        result = _classify(task, deps, root)
        if result["pickable"]:
            out.append(result)
    return out


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_pick(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        candidates = _list_pickable(con, root)
        if not candidates:
            if args.json:
                print(json.dumps({"ok": False, "pickable": None}))
            else:
                print("none")
            return 1
        chosen = candidates[0]
        if args.json:
            print(json.dumps({"ok": True, "pickable": chosen["id"],
                              "candidate_count": len(candidates)}))
        else:
            print(chosen["id"])
        return 0
    finally:
        con.close()


def cmd_why_blocked(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        task = _fetch_task(con, args.task_id)
        if task is None:
            sys.stderr.write(f"ERROR: task {args.task_id} not found\n")
            return 1
        deps = _fetch_deps(con, args.task_id)
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        result = _classify(task, deps, root)
        print(json.dumps(result, indent=2))
        return 0 if result["pickable"] else 1
    finally:
        con.close()


def cmd_has_unblocked(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        any_pickable = bool(_list_pickable(con, root))
        if any_pickable:
            print("yes")
            return 0
        print("no")
        return 1
    finally:
        con.close()


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="State-based picker + introspection (B3 bridge)"
    )
    parser.add_argument("--project", default=None,
                        help="Sub-project name; default 'root'")
    parser.add_argument("--db-path", default=None,
                        help="Override DB path (for tests)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pick = sub.add_parser(
        "pick", help="print highest-priority pickable task ID, or 'none'"
    )
    p_pick.add_argument("--json", action="store_true",
                        help="emit JSON instead of plain task ID")
    p_pick.set_defaults(func=cmd_pick)

    p_why = sub.add_parser(
        "why-blocked",
        help="JSON dump explaining why a task isn't pickable",
    )
    p_why.add_argument("task_id")
    p_why.set_defaults(func=cmd_why_blocked)

    p_has = sub.add_parser(
        "has-unblocked",
        help=("backcompat: print 'yes' (exit 0) if any pickable task exists, "
              "else 'no' (exit 1)"),
    )
    p_has.set_defaults(func=cmd_has_unblocked)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
