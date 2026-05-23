#!/usr/bin/env python3
"""db-migrate workflow: mechanism-contract form of the /db-migrate skill (WSH-D4).

Replaces the prose backup/dry-run/rollback steps with a script that prints a
machine-checkable checklist and a non-negotiable oracle verdict. Builds on the
db-backup workflow's invariants: a migration is safe to apply iff the source is
a sound SQLite DB AND the migration applies cleanly to a *temporary copy* that
still passes integrity_check. The caller proceeds only on GREEN.

Subcommands
-----------
  plan <db> <sql>     Dry-run the migration on a throwaway copy of <db>. Verifies
                      the source is sound, the migration applies, and the migrated
                      copy passes integrity_check. The real DB is never touched.
  execute <db> <sql>  Re-run the dry-run oracle; if GREEN, snapshot the source
                      (via db_backup), checkpoint WAL, apply to the real DB, and
                      re-verify integrity. Any FAIL -> nothing is applied.

<sql> is a path to a .sql file OR an inline SQL string.

Exit codes: 0 = ORACLE GREEN; 2 = ORACLE RED (failing items printed); 1 = usage.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import Checklist, emit_and_gate, find_project_root  # noqa: E402
import db_backup as dbw  # noqa: E402  (reuse _is_sqlite/_integrity/_table_count/backup)

ROOT = find_project_root()


def _load_sql(sql_arg: str) -> str:
    p = Path(sql_arg)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return sql_arg


def _dry_run(db_path: Path, sql: str) -> Checklist:
    """Apply the migration to a temp copy and verify it. No mutation of source."""
    cl = Checklist("db-migrate")

    exists = db_path.exists() and db_path.is_file()
    cl.check("source-exists", "source database exists", exists,
             detail=str(db_path) if exists else f"not found: {db_path}")
    if not exists:
        return cl

    valid = dbw._is_sqlite(db_path)
    cl.check("source-is-sqlite", "source has a valid SQLite header", valid,
             detail="" if valid else "missing 'SQLite format 3' magic")
    if not valid:
        return cl

    src_integ = dbw._integrity(db_path)
    cl.check("source-integrity", "source passes integrity_check before migrating",
             src_integ == "ok", detail=src_integ)

    has_sql = bool(sql.strip())
    cl.check("migration-non-empty", "migration SQL is non-empty", has_sql,
             detail="" if has_sql else "no SQL to apply")
    if not has_sql:
        return cl

    # Apply to a throwaway copy.
    tmp = db_path.with_suffix(db_path.suffix + ".migrate_test")
    applied = False
    apply_err = ""
    try:
        shutil.copy2(db_path, tmp)
        conn = sqlite3.connect(str(tmp))
        try:
            conn.executescript(sql)
            conn.commit()
            applied = True
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as e:
        apply_err = str(e)

    cl.check("dry-run-applies", "migration applies cleanly to a temp copy",
             applied, detail="" if applied else f"failed: {apply_err}")

    if applied:
        copy_integ = dbw._integrity(tmp)
        cl.check("migrated-copy-integrity",
                 "migrated copy still passes integrity_check",
                 copy_integ == "ok", detail=copy_integ)

    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
    return cl


def cmd_plan(args: argparse.Namespace) -> int:
    cl = _dry_run(Path(args.db_path), _load_sql(args.sql))
    return emit_and_gate(cl, json_out=args.json, phase="plan", db_path=args.db_path)


def cmd_execute(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    sql = _load_sql(args.sql)
    cl = _dry_run(db_path, sql)
    if cl.verdict() == "RED":
        return emit_and_gate(cl, json_out=args.json, phase="execute:dryrun",
                             db_path=args.db_path)

    # GREEN dry-run -> snapshot the source first (db-backup invariant).
    dbw.ROOT = ROOT
    bdir = dbw._backup_dir(db_path)
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = bdir / f"{db_path.stem}_{ts}.premigrate.db"
    backup_ok = False
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(str(db_path)); dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        backup_ok = dest.exists() and dbw._integrity(dest) == "ok"
    except sqlite3.Error as e:
        backup_ok = False
    cl.check("premigrate-backup", "verified pre-migration snapshot created",
             backup_ok, detail=str(dest) if backup_ok else "snapshot failed/corrupt")
    if not backup_ok:
        return emit_and_gate(cl, json_out=args.json, phase="execute:backup",
                             db_path=args.db_path)

    # Apply to the real DB (checkpoint WAL first), then re-verify.
    applied = False
    err = ""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.executescript(sql)
            conn.commit()
            applied = True
        finally:
            conn.close()
    except sqlite3.Error as e:
        err = str(e)
    cl.check("applied-to-source", "migration applied to the real database",
             applied, detail="" if applied else f"failed: {err}")
    if applied:
        post = dbw._integrity(db_path)
        cl.check("post-migrate-integrity", "real database passes integrity_check",
                 post == "ok", detail=post)

    code = emit_and_gate(cl, json_out=args.json, phase="execute:done",
                         db_path=args.db_path)
    if cl.verdict() == "GREEN":
        print(f"\nMigration applied. Pre-migration snapshot: {dest.relative_to(ROOT)}")
        print(f"Rollback: restore that snapshot over {db_path} after a WAL checkpoint.")
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="db-migrate workflow (checklist + oracle)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan", help="dry-run the migration on a temp copy")
    sp.add_argument("db_path")
    sp.add_argument("sql", help="path to .sql file or inline SQL")
    sp.set_defaults(func=cmd_plan)
    se = sub.add_parser("execute", help="snapshot + apply + verify (green-only)")
    se.add_argument("db_path")
    se.add_argument("sql")
    se.set_defaults(func=cmd_execute)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
