#!/usr/bin/env python3
"""Stuck-lease sweeper + claim/heartbeat helpers (T-P1-306 / INFRA-HITL B4).

Bridge between the new state-machine schema introduced by B1 and the
``state='leased'`` column-based lease lifecycle this task introduces. Lives in
scripts/ (not .claude/hooks/) for the same harness reason as the B2/B3
bridges: ``.claude/hooks/task_store.py`` and ``.claude/hooks/task_db.py`` are
sensitive in autonomous mode and could not be edited from inside this task.
A future fold-in (sibling of T-P1-319/T-P1-320) absorbs ``claim_task``,
``heartbeat`` and the sweeper into TaskStore + the canonical CLI.

Subcommands:
    sweep                   -- scan for stuck leases; reset them to state='ready'.
                               JSON output of {scanned, reset[], skipped[]} on
                               --json. Always exit 0 unless --strict and reset
                               count > 0 (ops-friendly default).
    claim <task_id>         -- set state='leased' + write pid/pgid/started_at/
                               last_heartbeat. Refuses if task is not currently
                               state='ready' (or if a non-stale lease is held).
    heartbeat <task_id>     -- bump tasks.last_heartbeat to now() AND touch
                               .claude/heartbeats/<task_id> file mtime. No-op
                               if state != 'leased' (so a heartbeat after the
                               sweep reaped the lease silently exits 0 without
                               undoing the sweep).
    release <task_id>       -- explicit complement to claim. Resets the lease
                               columns + state -> 'ready'. Used by inner agent
                               on graceful exit (B7 follow-up will wire this).
    inspect <task_id>       -- JSON dump of {state, pid, pgid, started_at,
                               last_heartbeat, ttl_seconds, effective_ttl,
                               heartbeat_age_s, pid_alive, would_sweep}.

Stuck-lease contract:
    A leased task is "stuck" iff
        state == 'leased'
        AND (
            now() - max(last_heartbeat_db, heartbeat_file_mtime) > ttl
            OR pid is set AND pid is NOT alive
            OR pid is unset AND no heartbeat file (orphan claim)
        )
    Effective ttl = tasks.ttl_seconds if not NULL, else CLAUDE_LEASE_DEFAULT_TTL
    env (default 600s).

Reset action (atomic, single transaction):
    UPDATE tasks SET state='ready', pid=NULL, pgid=NULL, started_at=NULL,
                     last_heartbeat=NULL WHERE id=?
    Append one JSON line to .claude/events.jsonl per reset task (B7 schema).
    Remove .claude/heartbeats/<task_id> if present.

Events.jsonl entry shape (forward-compatible with T-P1-309 / B7):
    {"ts": "...", "project_id": "...", "task_id": "T-X",
     "from_state": "leased", "to_state": "ready",
     "actor": "sweep_stuck_leases.py",
     "reason": "<heartbeat_stale|pid_dead|orphan_lease>",
     "lease_age_s": <int>, "ttl_s": <int>, "pid": <int|null>}

Cross-platform PID liveness:
    Linux/macOS: kill(pid, 0).
    Windows MSYS: psutil.pid_exists(pid) if available, else
                  ``tasklist //FI "PID eq <pid>" //NH`` parse.
    A pid that is unreadable (no perms) is treated as ALIVE for safety
    (false-negative is preferable to false-positive sweep that resets a
    healthy worker).

Usage:
    python scripts/sweep_stuck_leases.py [--project <name>] sweep
    python scripts/sweep_stuck_leases.py [--project <name>] sweep --json --dry-run
    python scripts/sweep_stuck_leases.py [--project <name>] claim T-P1-306
    python scripts/sweep_stuck_leases.py [--project <name>] heartbeat T-P1-306
    python scripts/sweep_stuck_leases.py [--project <name>] release T-P1-306
    python scripts/sweep_stuck_leases.py [--project <name>] inspect T-P1-306
    python scripts/sweep_stuck_leases.py --db-path <path> ...        # tests

Reads/writes the SQLite DB directly; does not import task_store.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from lib import events as events_log  # noqa: E402

DEFAULT_TTL = int(os.environ.get("CLAUDE_LEASE_DEFAULT_TTL", "600"))


def _now_dt() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0, tzinfo=None)


def _now() -> str:
    return _now_dt().strftime("%Y-%m-%dT%H:%M:%S")


def _parse_iso(ts: str | None) -> datetime.datetime | None:
    if not ts:
        return None
    try:
        # Stored shape is "%Y-%m-%dT%H:%M:%S" (naive UTC); also accept fractional/Z.
        cleaned = ts.rstrip("Z").split(".", 1)[0]
        return datetime.datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None


def _root_for(project: str | None) -> Path:
    if not project or project == "root":
        return REPO_ROOT
    return REPO_ROOT / project


def _db_path(project: str | None, override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    return _root_for(project) / ".claude" / "tasks.db"


def _ensure_migrated(con: sqlite3.Connection) -> None:
    cols = {c[1] for c in con.execute("PRAGMA table_info(tasks)")}
    missing = {"state", "pid", "pgid", "started_at",
               "last_heartbeat", "ttl_seconds", "project_id"} - cols
    if missing:
        sys.stderr.write(
            f"ERROR: target DB is missing migrated columns {sorted(missing)}. "
            "Run `python scripts/migrate/02_core_migration.py --apply` first.\n"
        )
        sys.exit(2)


def _heartbeat_file(root: Path, task_id: str) -> Path:
    return root / ".claude" / "heartbeats" / task_id


def _events_log(root: Path) -> Path:
    return root / ".claude" / "events.jsonl"


def _project_id_for_root(root: Path) -> str:
    """Best-effort project_id derivation from a filesystem path.

    Prefer reading the actual project_id off a task row (B1 already backfilled
    it correctly per DB); fall back to this only when the DB has no rows or
    the caller can't pass a row in.
    """
    try:
        rel = root.resolve().relative_to(REPO_ROOT)
        if rel == Path("."):
            return "root"
        parts = rel.parts
        return parts[0] if parts else "root"
    except ValueError:
        return "root"


def _project_id_from_db(con: sqlite3.Connection, fallback: str) -> str:
    """Read project_id from any task row; fall back if the table is empty
    or the column is null/missing.
    """
    try:
        row = con.execute(
            "SELECT project_id FROM tasks WHERE project_id IS NOT NULL LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return fallback
    if not row:
        return fallback
    val = row[0] if not isinstance(row, sqlite3.Row) else row["project_id"]
    return val or fallback


def _fetch_task(con: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    con.row_factory = sqlite3.Row
    row = con.execute(
        """SELECT id, title, status, state, project_id,
                  pid, pgid, started_at, ttl_seconds, last_heartbeat
           FROM tasks WHERE id=?""",
        (task_id,),
    ).fetchone()
    return dict(row) if row else None


def _list_leased(con: sqlite3.Connection) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT id, title, status, state, project_id,
                  pid, pgid, started_at, ttl_seconds, last_heartbeat
           FROM tasks WHERE state='leased'
           ORDER BY id"""
    ).fetchall()
    return [dict(r) for r in rows]


# --- PID liveness probes (cross-platform) ----------------------------------


def _pid_alive_unix(pid: int) -> bool | None:
    """Return True/False, or None if unknown."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it; treat as alive.
        return True
    except (OSError, AttributeError):
        return None


def _pid_alive_windows(pid: int) -> bool | None:
    # Try psutil first if available.
    try:
        import psutil  # type: ignore
        return bool(psutil.pid_exists(pid))
    except ImportError:
        pass
    # Fall back to tasklist (available on every Windows install).
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        # tasklist exits 0 even with no match; the "INFO: No tasks..." line is
        # printed instead. Detect by presence of the PID in the output.
        return str(pid) in out.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _pid_alive(pid: int | None) -> bool | None:
    if pid is None:
        return None
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        return _pid_alive_windows(pid)
    return _pid_alive_unix(pid)


# --- Heartbeat / lease state evaluation ------------------------------------


def _effective_heartbeat(
    task: dict[str, Any], root: Path
) -> tuple[datetime.datetime | None, str]:
    """Return (newest_known_heartbeat, source). Source is 'db', 'file',
    'started_at', or 'none'.
    """
    db_hb = _parse_iso(task.get("last_heartbeat"))
    file_hb: datetime.datetime | None = None
    fpath = _heartbeat_file(root, task["id"])
    if fpath.exists():
        try:
            file_hb = datetime.datetime.utcfromtimestamp(fpath.stat().st_mtime)
        except OSError:
            file_hb = None
    candidates = [(db_hb, "db"), (file_hb, "file")]
    real = [(t, s) for t, s in candidates if t is not None]
    if real:
        # Newest wins -- treat both DB and file as valid signals.
        real.sort(key=lambda p: p[0], reverse=True)
        return real[0]
    started = _parse_iso(task.get("started_at"))
    if started is not None:
        return started, "started_at"
    return None, "none"


def _classify_lease(
    task: dict[str, Any], root: Path, now: datetime.datetime,
) -> dict[str, Any]:
    """Decide whether a leased task is stuck. Returns a dict suitable for
    JSON output AND for the sweep decision branch.
    """
    ttl = task.get("ttl_seconds")
    effective_ttl = int(ttl) if ttl else DEFAULT_TTL

    hb, hb_source = _effective_heartbeat(task, root)
    age_s: int | None
    age_s = int((now - hb).total_seconds()) if hb else None

    pid = task.get("pid")
    pid_alive = _pid_alive(pid) if pid else None

    reason: str | None = None
    if task.get("state") != "leased":
        return {
            "state": task.get("state"),
            "would_sweep": False,
            "reason": None,
            "effective_ttl": effective_ttl,
            "heartbeat_age_s": age_s,
            "heartbeat_source": hb_source,
            "pid": pid,
            "pid_alive": pid_alive,
        }

    # Orphan claim: pid not set AND no heartbeat file/DB row -> can't tell who
    # owns this lease. Consider stuck.
    if pid is None and hb is None:
        reason = "orphan_lease"
    elif pid_alive is False:
        reason = "pid_dead"
    elif age_s is not None and age_s > effective_ttl:
        reason = "heartbeat_stale"

    return {
        "state": task["state"],
        "would_sweep": reason is not None,
        "reason": reason,
        "effective_ttl": effective_ttl,
        "heartbeat_age_s": age_s,
        "heartbeat_source": hb_source,
        "pid": pid,
        "pid_alive": pid_alive,
    }


# --- Events log ------------------------------------------------------------


def _append_event(root: Path, payload: dict[str, Any]) -> None:
    """Append a JSON line to .claude/events.jsonl via the shared lib.

    The B7 ``scripts/lib/events.append`` provides cross-process locking
    (fcntl/msvcrt), atomic writes under O_APPEND, and 100 MiB rotation.
    This wrapper exists for backwards-compatibility with the B4
    call sites; both paths emit the same on-disk format.
    """
    try:
        events_log.append(root, payload)
    except (ValueError, OSError) as exc:
        sys.stderr.write(
            f"WARN: could not append to events.jsonl: {exc}\n"
        )


# --- Subcommand: sweep -----------------------------------------------------


def cmd_sweep(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        project_id = _project_id_from_db(con, _project_id_for_root(root))
        leased = _list_leased(con)
        now = _now_dt()
        scanned = len(leased)
        reset: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for task in leased:
            classification = _classify_lease(task, root, now)
            entry = {
                "task_id": task["id"],
                "title": task["title"],
                **classification,
            }
            if classification["would_sweep"]:
                if not args.dry_run:
                    con.execute(
                        """UPDATE tasks SET state='ready', pid=NULL, pgid=NULL,
                                            started_at=NULL, last_heartbeat=NULL,
                                            updated_at=?
                           WHERE id=? AND state='leased'""",
                        (_now(), task["id"]),
                    )
                    con.commit()
                    # Best-effort: clean stale heartbeat file too.
                    fpath = _heartbeat_file(root, task["id"])
                    if fpath.exists():
                        try:
                            fpath.unlink()
                        except OSError:
                            pass
                    _append_event(root, {
                        "ts": _now(),
                        "project_id": project_id,
                        "task_id": task["id"],
                        "from_state": "leased",
                        "to_state": "ready",
                        "actor": "sweep_stuck_leases.py",
                        "reason": classification["reason"],
                        "lease_age_s": classification["heartbeat_age_s"],
                        "ttl_s": classification["effective_ttl"],
                        "pid": classification["pid"],
                    })
                reset.append(entry)
            else:
                skipped.append(entry)

        result = {
            "ok": True,
            "scanned": scanned,
            "reset_count": len(reset),
            "skipped_count": len(skipped),
            "reset": reset,
            "skipped": skipped,
            "dry_run": args.dry_run,
            "project_id": project_id,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"[sweep] project_id={project_id} scanned={scanned} "
                f"reset={len(reset)} skipped={len(skipped)} "
                f"dry_run={args.dry_run}"
            )
            for e in reset:
                print(f"  RESET {e['task_id']} reason={e['reason']} "
                      f"age={e['heartbeat_age_s']}s ttl={e['effective_ttl']}s "
                      f"pid={e['pid']} pid_alive={e['pid_alive']}")
            for e in skipped:
                print(f"  KEEP  {e['task_id']} age={e['heartbeat_age_s']}s "
                      f"ttl={e['effective_ttl']}s pid_alive={e['pid_alive']}")
        if args.strict and len(reset) > 0:
            return 1
        return 0
    finally:
        con.close()


# --- Subcommand: claim -----------------------------------------------------


def cmd_claim(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        task = _fetch_task(con, args.task_id)
        if task is None:
            sys.stderr.write(f"ERROR: task {args.task_id} not found\n")
            return 1
        if task["state"] not in ("ready", "leased"):
            sys.stderr.write(
                f"ERROR: cannot claim {args.task_id}: state={task['state']!r} "
                "(must be 'ready')\n"
            )
            return 1
        if task["state"] == "leased":
            # Check if existing lease is stuck; if not, refuse.
            now = _now_dt()
            classification = _classify_lease(task, root, now)
            if not classification["would_sweep"]:
                sys.stderr.write(
                    f"ERROR: cannot claim {args.task_id}: lease held by "
                    f"pid={task['pid']} (not stuck)\n"
                )
                return 1
            # Otherwise allow steal-claim (caller must have run sweep first
            # in normal operation; this is the recovery path).

        pid = args.pid if args.pid is not None else os.getpid()
        pgid = args.pgid
        if pgid is None:
            try:
                pgid = os.getpgrp()
            except (AttributeError, OSError):
                pgid = pid
        ttl = args.ttl_seconds  # may be None -> NULL -> default-on-read
        now = _now()

        # Atomic claim: only succeed if state is still 'ready' OR 'leased' (steal).
        cur = con.execute(
            """UPDATE tasks
                   SET state='leased', pid=?, pgid=?, started_at=?,
                       last_heartbeat=?, ttl_seconds=COALESCE(?, ttl_seconds),
                       updated_at=?
                 WHERE id=? AND state IN ('ready','leased')""",
            (pid, pgid, now, now, ttl, now, args.task_id),
        )
        con.commit()
        if cur.rowcount != 1:
            sys.stderr.write(
                f"ERROR: claim race lost on {args.task_id} "
                "(state changed mid-claim)\n"
            )
            return 1

        # Touch the heartbeat file too.
        fpath = _heartbeat_file(root, args.task_id)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.touch()

        # B7 events log entry: ready -> leased (or leased -> leased on
        # steal-claim). prev_state captured before the UPDATE above.
        project_id = _project_id_from_db(con, _project_id_for_root(root))
        _append_event(root, {
            "ts": now,
            "project_id": project_id,
            "task_id": args.task_id,
            "from_state": task["state"],
            "to_state": "leased",
            "actor": "sweep_stuck_leases.py:claim",
            "pid": pid,
            "pgid": pgid,
            "ttl_s": ttl,
        })

        result = {
            "ok": True, "task_id": args.task_id, "pid": pid, "pgid": pgid,
            "started_at": now, "ttl_seconds": ttl,
        }
        if args.json:
            print(json.dumps(result))
        else:
            print(
                f"[claim] {args.task_id} pid={pid} pgid={pgid} "
                f"started_at={now} ttl={ttl or 'default'}"
            )
        return 0
    finally:
        con.close()


# --- Subcommand: heartbeat -------------------------------------------------


def cmd_heartbeat(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        task = _fetch_task(con, args.task_id)
        if task is None:
            sys.stderr.write(f"ERROR: task {args.task_id} not found\n")
            return 1
        # If state changed under us (e.g. sweep reaped this lease, or task
        # finished), no-op silently. Returning 0 keeps the heartbeat bg loop
        # from spamming errors after a clean exit.
        if task["state"] != "leased":
            if args.json:
                print(json.dumps({
                    "ok": True, "noop": True, "state": task["state"]
                }))
            return 0
        now = _now()
        con.execute(
            "UPDATE tasks SET last_heartbeat=?, updated_at=? "
            "WHERE id=? AND state='leased'",
            (now, now, args.task_id),
        )
        con.commit()
        fpath = _heartbeat_file(root, args.task_id)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.touch()
        if args.json:
            print(json.dumps({"ok": True, "task_id": args.task_id, "ts": now}))
        return 0
    finally:
        con.close()


# --- Subcommand: release ---------------------------------------------------


def cmd_release(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        task = _fetch_task(con, args.task_id)
        if task is None:
            sys.stderr.write(f"ERROR: task {args.task_id} not found\n")
            return 1
        if task["state"] != "leased":
            # Already released or progressed to producing/done.
            if args.json:
                print(json.dumps({
                    "ok": True, "noop": True, "state": task["state"]
                }))
            return 0
        now = _now()
        prior_pid = task.get("pid")
        con.execute(
            """UPDATE tasks SET state='ready', pid=NULL, pgid=NULL,
                                started_at=NULL, last_heartbeat=NULL,
                                updated_at=?
               WHERE id=? AND state='leased'""",
            (now, args.task_id),
        )
        con.commit()
        fpath = _heartbeat_file(root, args.task_id)
        if fpath.exists():
            try:
                fpath.unlink()
            except OSError:
                pass

        # B7 events log entry: leased -> ready (graceful release).
        project_id = _project_id_from_db(con, _project_id_for_root(root))
        _append_event(root, {
            "ts": now,
            "project_id": project_id,
            "task_id": args.task_id,
            "from_state": "leased",
            "to_state": "ready",
            "actor": "sweep_stuck_leases.py:release",
            "reason": "graceful_release",
            "pid": prior_pid,
        })

        if args.json:
            print(json.dumps({"ok": True, "task_id": args.task_id}))
        else:
            print(f"[release] {args.task_id} -> ready")
        return 0
    finally:
        con.close()


# --- Subcommand: inspect ---------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    db = _db_path(args.project, args.db_path)
    if not db.exists():
        sys.stderr.write(f"ERROR: DB not found: {db}\n")
        return 2
    con = sqlite3.connect(str(db))
    _ensure_migrated(con)
    try:
        root = (db.parent.parent if args.db_path
                else _root_for(args.project))
        task = _fetch_task(con, args.task_id)
        if task is None:
            sys.stderr.write(f"ERROR: task {args.task_id} not found\n")
            return 1
        now = _now_dt()
        classification = _classify_lease(task, root, now)
        out = {
            "task_id": task["id"],
            "title": task["title"],
            "state": task["state"],
            "pid": task["pid"],
            "pgid": task["pgid"],
            "started_at": task["started_at"],
            "last_heartbeat": task["last_heartbeat"],
            "ttl_seconds": task["ttl_seconds"],
            **{k: v for k, v in classification.items() if k != "state"},
        }
        print(json.dumps(out, indent=2))
        return 0
    finally:
        con.close()


# --- CLI plumbing ----------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stuck-lease sweeper + claim/heartbeat helpers (B4 bridge)"
    )
    parser.add_argument("--project", default=None,
                        help="Sub-project name; default 'root'")
    parser.add_argument("--db-path", default=None,
                        help="Override DB path (for tests)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sweep = sub.add_parser("sweep", help="reset stuck leases to state='ready'")
    p_sweep.add_argument("--json", action="store_true")
    p_sweep.add_argument("--dry-run", action="store_true",
                         help="report would-sweep without modifying DB")
    p_sweep.add_argument("--strict", action="store_true",
                         help="exit 1 if any task was reset")
    p_sweep.set_defaults(func=cmd_sweep)

    p_claim = sub.add_parser("claim", help="acquire a lease on a ready task")
    p_claim.add_argument("task_id")
    p_claim.add_argument("--pid", type=int, default=None,
                         help="lease owner pid (default: own pid)")
    p_claim.add_argument("--pgid", type=int, default=None,
                         help="lease owner pgid (default: own pgid)")
    p_claim.add_argument("--ttl-seconds", type=int, default=None,
                         help="override task ttl_seconds in same call")
    p_claim.add_argument("--json", action="store_true")
    p_claim.set_defaults(func=cmd_claim)

    p_hb = sub.add_parser("heartbeat", help="update last_heartbeat for a leased task")
    p_hb.add_argument("task_id")
    p_hb.add_argument("--json", action="store_true")
    p_hb.set_defaults(func=cmd_heartbeat)

    p_rel = sub.add_parser("release",
                           help="release a lease (state='leased' -> 'ready')")
    p_rel.add_argument("task_id")
    p_rel.add_argument("--json", action="store_true")
    p_rel.set_defaults(func=cmd_release)

    p_ins = sub.add_parser("inspect",
                           help="JSON dump of a task's lease state")
    p_ins.add_argument("task_id")
    p_ins.set_defaults(func=cmd_inspect)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
