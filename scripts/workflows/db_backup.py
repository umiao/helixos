#!/usr/bin/env python3
"""db-backup workflow: mechanism-contract form of the /db-backup skill (WSH-D1).

Replaces the prose backup/restore steps with a script that prints a
machine-checkable checklist and a non-negotiable oracle verdict. The caller
proceeds only on GREEN.

Subcommands
-----------
  plan <db_path>      Dry-run. Verify the source is a readable SQLite DB that
                      passes integrity_check and the backup dir is writable.
                      No backup is written. Oracle = "is this backup sensible?".
  execute <db_path>   Create a snapshot via the SQLite .backup() API, then run
                      the oracle: backup integrity_check == 'ok' AND table count
                      matches source. RED -> the (possibly partial) snapshot is
                      removed and nothing is reported as good.

Exit codes: 0 = ORACLE GREEN; 2 = ORACLE RED (failing items printed); 1 = usage.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import Checklist, emit_and_gate, find_project_root  # noqa: E402

ROOT = find_project_root()


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _integrity(path: Path) -> str:
    """Return integrity_check result string, or an 'ERROR: ...' string."""
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return row[0] if row else "ERROR: empty integrity_check"
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"ERROR: {e}"


def _table_count(path: Path) -> int | None:
    try:
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _backup_dir(db_path: Path) -> Path:
    """Backups live next to the project containing the DB, under .backup/db/."""
    return ROOT / ".backup" / "db"


def _preflight(db_path: Path) -> Checklist:
    cl = Checklist("db-backup")
    exists = db_path.exists() and db_path.is_file()
    cl.check("source-exists", "source database file exists", exists,
             detail=str(db_path) if exists else f"not found: {db_path}")
    if not exists:
        return cl

    valid = _is_sqlite(db_path)
    cl.check("is-sqlite", "source has a valid SQLite header", valid,
             detail="" if valid else "missing 'SQLite format 3' magic")

    integ = _integrity(db_path) if valid else "ERROR: not sqlite"
    cl.check("source-integrity", "source passes PRAGMA integrity_check",
             integ == "ok", detail=integ)

    bdir = _backup_dir(db_path)
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        writable = os.access(bdir, os.W_OK)
    except OSError as e:
        writable = False
        bdir = Path(str(bdir) + f" ({e})")
    cl.check("backup-dir-writable", "backup directory is writable", writable,
             detail=str(bdir))
    return cl


def cmd_plan(args: argparse.Namespace) -> int:
    cl = _preflight(Path(args.db_path))
    return emit_and_gate(cl, json_out=args.json, phase="plan", db_path=args.db_path)


def cmd_execute(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    cl = _preflight(db_path)
    if cl.verdict() == "RED":
        return emit_and_gate(cl, json_out=args.json, phase="execute:preflight",
                             db_path=args.db_path)

    bdir = _backup_dir(db_path)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = bdir / f"{db_path.stem}_{ts}.db"
    src_tables = _table_count(db_path)
    backup_err = ""
    try:
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        created = dest.exists()
    except sqlite3.Error as e:
        created, backup_err = False, str(e)

    cl.check("snapshot-created", "snapshot file written", created,
             detail=str(dest) if created else f"backup() failed: {backup_err}")

    if created:
        integ = _integrity(dest)
        cl.check("snapshot-integrity", "snapshot passes PRAGMA integrity_check",
                 integ == "ok", detail=integ)
        b_tables = _table_count(dest)
        match = (b_tables is not None and b_tables == src_tables)
        cl.check("table-count-parity",
                 "snapshot table count matches source", match,
                 detail=f"source={src_tables} snapshot={b_tables}")

    if cl.verdict() == "RED":
        # Refuse: remove the suspect snapshot so it can't masquerade as a good one.
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return emit_and_gate(cl, json_out=args.json, phase="execute:oracle",
                             db_path=args.db_path)

    code = emit_and_gate(cl, json_out=args.json, phase="execute:done",
                         db_path=args.db_path)
    rel = dest.relative_to(ROOT)
    print(f"\nBackup created: {rel}")
    print(f"Restore: copy this snapshot back over the source after a WAL checkpoint.")
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="db-backup workflow (checklist + oracle)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan", help="dry-run preflight check, no snapshot")
    sp.add_argument("db_path")
    sp.set_defaults(func=cmd_plan)
    se = sub.add_parser("execute", help="snapshot + verified oracle (green-only)")
    se.add_argument("db_path")
    se.set_defaults(func=cmd_execute)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
