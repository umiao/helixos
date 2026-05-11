#!/usr/bin/env python3
"""human_review-aware task completion CLI (T-P1-304 / INFRA-HITL B2).

Bridge between the legacy `status='completed'` flow and the new state machine
introduced by B1. Lives in scripts/ (not .claude/hooks/) because the hook
library files .claude/hooks/task_store.py and .claude/hooks/task_db.py are
sensitive in autonomous mode and could not be edited from inside this task.
A follow-up task with `human_review:true` will fold this logic into the
TaskStore class and add `task_db.py complete-task` natively.

Behavior (per T-P1-304 acceptance criteria):
    1. complete <task_id>
       - if human_review=1 -> state='review_pending', writes
         <root>/.claude/review-queue/<task_id>.yaml stub, leaves status='active'
         (so the producing-complete signal does NOT mark the task done in
         legacy SELECT status='active' picker either; we flip status to
         'in_progress' so the legacy picker stops re-picking, and B3 will
         pick up state-based filtering directly).
       - if human_review=0 -> state='done', status='completed',
         completed_at=today; no queue file.

    2. update <task_id> --status completed
       - on human_review=1 task: emit WARN to stderr, leave state='review_pending',
         do NOT set status='completed' (preserves the review gate that the legacy
         CLI cannot enforce on its own).
       - on human_review=0 task: passthrough -- write status='completed',
         state='done', completed_at=today.

    Both subcommands are idempotent: re-running on an already-completed task
    is a no-op (returns the existing state).

Usage:
    python scripts/task_complete.py [--project <name>] complete <task_id>
    python scripts/task_complete.py [--project <name>] update <task_id> --status completed
    python scripts/task_complete.py [--project <name>] inspect <task_id>

Reads/writes the SQLite DB directly; does not import task_store.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _today() -> str:
    return datetime.date.today().isoformat()


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _root_for(project: str | None) -> Path:
    """Resolve the project root for a given --project argument.

    Default (None or 'root'): workspace root. Otherwise: <REPO_ROOT>/<project>.
    A bridge --db-path override is also supported below for tests.
    """
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


def _fetch_task(con: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    con.row_factory = sqlite3.Row
    row = con.execute(
        """SELECT id, title, status, state, human_review, project_id,
                  description, completed_at, priority, complexity
           FROM tasks WHERE id=?""",
        (task_id,),
    ).fetchone()
    return dict(row) if row else None


def _review_queue_path(root: Path, task_id: str) -> Path:
    return root / ".claude" / "review-queue" / f"{task_id}.yaml"


def _write_review_stub(
    root: Path, task_id: str, task: dict[str, Any]
) -> Path:
    """Write a minimal review-queue YAML stub.

    The full B5 (T-P1-307) schema is deferred; this stub captures the minimum
    fields the C1 portal will need to surface 'awaiting review' tasks:
        task_id, project_id, title, state, human_review, queued_at,
        legacy_status, source ('producing-complete'), schema_version='b2-stub'.
    """
    out = _review_queue_path(root, task_id)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Hand-roll YAML to avoid PyYAML dependency. Quote title defensively.
    safe_title = task["title"].replace('"', '\\"').replace("\n", " ")
    body = (
        "# Auto-generated review-queue stub (T-P1-304 / INFRA-HITL B2).\n"
        "# Will be superseded by B5 (T-P1-307) full plan/approval schema.\n"
        f"schema_version: b2-stub\n"
        f"task_id: {task['id']}\n"
        f"project_id: {task['project_id']}\n"
        f'title: "{safe_title}"\n'
        f"priority: {task['priority']}\n"
        f"complexity: {task['complexity']}\n"
        f"state: review_pending\n"
        f"human_review: true\n"
        f"queued_at: {_now()}\n"
        f"legacy_status: {task['status']}\n"
        f"source: producing-complete\n"
    )
    out.write_text(body, encoding="utf-8")
    return out


def _do_complete(
    con: sqlite3.Connection,
    root: Path,
    task_id: str,
    *,
    via_legacy_update: bool = False,
) -> dict[str, Any]:
    """Core completion logic shared by `complete` and `update --status completed`.

    Args:
        con: sqlite connection (caller commits).
        root: project root (for queue file placement).
        task_id: task to complete.
        via_legacy_update: True if invoked via `update --status completed`;
            triggers a stderr WARN block when the task carries human_review=1.

    Returns:
        Result dict with ok/id/state/status/human_review/queue_file?/warning?.
        Caller must `con.commit()` after a successful return.
    """
    task = _fetch_task(con, task_id)
    if task is None:
        return {"ok": False, "error": f"Task {task_id} not found"}

    # Idempotency: already done -> no-op
    if task["state"] == "done":
        return {
            "ok": True, "id": task_id, "state": "done",
            "status": task["status"], "human_review": bool(task["human_review"]),
            "noop": "already done",
        }
    if task["state"] == "review_pending":
        # Already in review queue; refresh stub but do not flip state again.
        if task["human_review"]:
            queue = _write_review_stub(root, task_id, task)
            return {
                "ok": True, "id": task_id, "state": "review_pending",
                "status": task["status"], "human_review": True,
                "queue_file": str(queue), "noop": "already review_pending",
            }

    now = _now()
    if task["human_review"]:
        # producing-complete -> review_pending. Flip status off 'active' so the
        # legacy SELECT WHERE status='active' picker stops re-picking. Use
        # 'in_progress' (in CHECK constraint) -- semantically "work done,
        # awaiting human review". DO NOT set completed_at -- the work is not
        # truly done from a tasks-completed-this-month perspective.
        con.execute(
            "UPDATE tasks SET status='in_progress', state='review_pending', "
            "updated_at=? WHERE id=?",
            (now, task_id),
        )
        # Re-fetch so stub uses the just-updated row.
        task = _fetch_task(con, task_id)
        assert task is not None
        queue = _write_review_stub(root, task_id, task)
        result: dict[str, Any] = {
            "ok": True, "id": task_id, "state": "review_pending",
            "status": "in_progress", "human_review": True,
            "queue_file": str(queue),
        }
        if via_legacy_update:
            warn = (
                f"WARN: task {task_id} carries human_review=1; legacy "
                "`update --status completed` was intercepted. State set to "
                "'review_pending' (NOT 'completed'); review-queue stub "
                f"written to {queue}. To force-complete a human_review task, "
                "use the B5 approval flow (T-P1-307) once it lands."
            )
            sys.stderr.write(warn + "\n")
            result["warning"] = warn
        return result

    # human_review=0: vanilla producing-complete.
    con.execute(
        "UPDATE tasks SET status='completed', state='done', "
        "completed_at=?, updated_at=? WHERE id=?",
        (_today(), now, task_id),
    )
    return {
        "ok": True, "id": task_id, "state": "done",
        "status": "completed", "human_review": False,
    }


def cmd_complete(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        result = _do_complete(con, _root_for(args.project) if not args.db_path
                              else db.parent.parent, args.task_id,
                              via_legacy_update=False)
        if result.get("ok"):
            con.commit()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    finally:
        con.close()


def cmd_update(args: argparse.Namespace) -> int:
    """Wrapper for `task_db.py update` that intercepts --status completed."""
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        if args.status == "completed":
            root = (db.parent.parent if args.db_path
                    else _root_for(args.project))
            result = _do_complete(con, root, args.task_id,
                                  via_legacy_update=True)
            if result.get("ok"):
                con.commit()
            print(json.dumps(result, indent=2))
            return 0 if result.get("ok") else 1
        # Other --status values: passthrough plain UPDATE (no human_review check).
        con.execute(
            "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
            (args.status, _now(), args.task_id),
        )
        con.commit()
        print(json.dumps({"ok": True, "id": args.task_id,
                          "status": args.status}))
        return 0
    finally:
        con.close()


def cmd_inspect(args: argparse.Namespace) -> int:
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
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        queue = _review_queue_path(root, args.task_id)
        out = {
            "id": task["id"],
            "title": task["title"],
            "status": task["status"],
            "state": task["state"],
            "human_review": bool(task["human_review"]),
            "project_id": task["project_id"],
            "completed_at": task["completed_at"],
            "queue_file_present": queue.exists(),
            "queue_file_path": str(queue),
        }
        print(json.dumps(out, indent=2))
        return 0
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="human_review-aware task completion (B2 bridge)"
    )
    parser.add_argument("--project", default=None,
                        help="Sub-project name; default 'root'")
    parser.add_argument("--db-path", default=None,
                        help="Override DB path (for tests)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_complete = sub.add_parser(
        "complete", help="producing-complete: branch on human_review"
    )
    p_complete.add_argument("task_id")
    p_complete.set_defaults(func=cmd_complete)

    p_update = sub.add_parser(
        "update",
        help="task_db.py update wrapper that respects human_review",
    )
    p_update.add_argument("task_id")
    p_update.add_argument("--status", required=True,
                          choices=["active", "in_progress", "completed",
                                   "blocked"])
    p_update.set_defaults(func=cmd_update)

    p_inspect = sub.add_parser(
        "inspect", help="dump task state + queue-file presence"
    )
    p_inspect.add_argument("task_id")
    p_inspect.set_defaults(func=cmd_inspect)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
