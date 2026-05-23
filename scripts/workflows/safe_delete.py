#!/usr/bin/env python3
"""safe-delete workflow: mechanism-contract form of the /safe-delete skill (WSH-D1).

Replaces the prose "Step 1..6" checklist with a script that prints a
machine-checkable safety checklist and a non-negotiable oracle verdict. The
caller (skill body / WSH-D2 router) proceeds only on GREEN.

Subcommands
-----------
  plan <target>      Dry-run. Survey + permissibility checks only. No backup,
                     no deletion. Oracle = "is deleting this even permitted?".
  execute <target>   Re-check permissibility; if GREEN create+verify a backup
                     (the restorability oracle), then delete and print the
                     restore command. Any FAIL -> RED -> nothing is deleted.

Exit codes: 0 = ORACLE GREEN (proceeded / safe to proceed); 2 = ORACLE RED
(refused, failing items printed); 1 = usage error.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import Checklist, emit_and_gate, find_project_root  # noqa: E402

ROOT = find_project_root()


def _tree_stats(path: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) for a path (file or directory tree)."""
    if path.is_file() or path.is_symlink():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 1, 0
    n, total = 0, 0
    for p in path.rglob("*"):
        if p.is_file() and not p.is_symlink():
            n += 1
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return n, total


def _has_uncommitted(target: Path) -> bool | None:
    """True/False if git reports changes under target; None if git unavailable."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", str(target)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=20,
        )
        if out.returncode != 0:
            return None
        return bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _permissibility(target_arg: str) -> tuple[Checklist, Path]:
    """Build the permissibility checklist (no mutation)."""
    cl = Checklist("safe-delete")
    target = Path(target_arg)
    resolved = target.resolve()

    exists = target.exists() or target.is_symlink()
    cl.check("exists", "target path exists", exists,
             detail=str(resolved) if exists else f"not found: {target_arg}")

    is_root = resolved == ROOT.resolve()
    cl.check("not-project-root", "target is not the project root", not is_root,
             detail="refusing to delete the project root" if is_root else "")

    # .git itself or anything inside it.
    git_dir = (ROOT / ".git").resolve()
    in_git = resolved == git_dir or git_dir in resolved.parents
    cl.check("not-dot-git", "target is not the .git directory", not in_git,
             detail="refusing to delete git internals" if in_git else "")

    root_resolved = ROOT.resolve()
    within = resolved == root_resolved or root_resolved in resolved.parents
    cl.check("within-project-tree", "target is inside the project tree", within,
             detail="" if within else f"escapes project root {root_resolved}")

    if exists:
        n, total = _tree_stats(target)
        cl.check("survey", f"surveyed {n} file(s), {total} bytes", True,
                 detail=f"{n} files / {total} bytes")
        # >50 files is advisory (WARN): the prose skill required double-confirm,
        # which is the human's job; the oracle never silently blocks on size.
        cl.check("large-deletion-advisory",
                 "deletion size within auto-confirm threshold (<=50 files)",
                 n <= 50, detail=f"{n} files -- requires explicit confirmation"
                 if n > 50 else "", warn_only=True)
        dirty = _has_uncommitted(target)
        if dirty is not None:
            cl.check("clean-worktree", "no uncommitted changes under target",
                     not dirty, detail="uncommitted changes present -- confirm"
                     if dirty else "", warn_only=True)

    return cl, target


def cmd_plan(args: argparse.Namespace) -> int:
    cl, _ = _permissibility(args.target)
    return emit_and_gate(cl, json_out=args.json, phase="plan", target=args.target)


def cmd_execute(args: argparse.Namespace) -> int:
    cl, target = _permissibility(args.target)
    # Phase 1: permissibility. Refuse before touching anything.
    if cl.verdict() == "RED":
        return emit_and_gate(cl, json_out=args.json, phase="execute:permit",
                             target=args.target)

    # Phase 2: backup, then verify restorability (the oracle that gates deletion).
    from datetime import date
    backup_root = ROOT / ".backup" / date.today().isoformat()
    backup_root.mkdir(parents=True, exist_ok=True)
    dest = backup_root / target.name
    src_n, src_total = _tree_stats(target)
    try:
        if target.is_dir() and not target.is_symlink():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(target, dest, symlinks=True)
        else:
            shutil.copy2(target, dest)
        backup_ok = dest.exists()
        backup_err = ""
    except (OSError, shutil.Error) as e:
        backup_ok, backup_err = False, str(e)

    cl.check("backup-created", "backup copy created", backup_ok,
             detail=str(dest) if backup_ok else f"copy failed: {backup_err}")

    if backup_ok:
        b_n, b_total = _tree_stats(dest)
        match = (b_n == src_n and b_total == src_total)
        cl.check("backup-restorable",
                 "backup is byte/count-equivalent to source (restorable)", match,
                 detail=f"src={src_n}f/{src_total}b backup={b_n}f/{b_total}b"
                 + ("" if match else " -- MISMATCH, refusing to delete"))

    # Final oracle over permissibility + backup integrity.
    if cl.verdict() == "RED":
        return emit_and_gate(cl, json_out=args.json, phase="execute:backup",
                             target=args.target)

    # GREEN: safe to delete.
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink()
    cl.check("deleted", "target removed", True, detail=str(target))
    code = emit_and_gate(cl, json_out=args.json, phase="execute:done",
                         target=args.target)
    rel = dest.relative_to(ROOT)
    print(f"\nRestore: cp -r \"{rel}\" \"{args.target}\"")
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="safe-delete workflow (checklist + oracle)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan", help="dry-run permissibility check, no mutation")
    sp.add_argument("target")
    sp.set_defaults(func=cmd_plan)
    se = sub.add_parser("execute", help="backup + verified delete (green-only)")
    se.add_argument("target")
    se.set_defaults(func=cmd_execute)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
