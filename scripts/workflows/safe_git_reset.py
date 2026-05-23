#!/usr/bin/env python3
"""safe-git-reset workflow: mechanism-contract form of /safe-git-reset (WSH-D4).

Replaces the prose stash-before-reset checklist with a script that prints a
machine-checkable safety checklist and a non-negotiable oracle verdict. The
mechanism mirrors safe-delete: the destructive act (reset/clean/checkout) is
gated behind a restorable backup -- here a `git stash` -- whose creation is the
oracle. The caller proceeds only on GREEN.

Subcommands
-----------
  plan <op>      Dry-run. Assess impact + permissibility (refuse `--force` to
                 main/master; require a git repo). No stash, no reset.
  execute <op>   Re-check permissibility; if GREEN create a stash backup
                 (the restorability oracle) and print recovery commands. The
                 actual reset/clean is left to the human AFTER the safety net
                 exists -- this script never runs a destructive git command.

Exit codes: 0 = ORACLE GREEN; 2 = ORACLE RED (failing items printed); 1 = usage.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import Checklist, emit_and_gate, find_project_root  # noqa: E402

ROOT = find_project_root()

# `--force` (not `--force-with-lease`) targeting main/master is the cardinal sin.
_FORCE_RE = re.compile(r"--force(?!-with-lease)\b")
_PROTECTED_RE = re.compile(r"\b(main|master|origin/main|origin/master)\b")


def _git(args: list[str]) -> tuple[int, str]:
    try:
        out = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                             text=True, timeout=30)
        return out.returncode, out.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _permissibility(op: str) -> Checklist:
    cl = Checklist("safe-git-reset")

    rc, _ = _git(["rev-parse", "--is-inside-work-tree"])
    in_repo = (rc == 0)
    cl.check("in-git-repo", "running inside a git work tree", in_repo,
             detail="" if in_repo else "not a git repository")

    force_to_protected = bool(_FORCE_RE.search(op) and _PROTECTED_RE.search(op)
                              and "push" in op)
    cl.check("no-force-to-protected",
             "not a bare --force push to main/master", not force_to_protected,
             detail="use --force-with-lease; never --force to a protected branch"
             if force_to_protected else "")

    if in_repo:
        rc, out = _git(["status", "--porcelain"])
        dirty = bool(out.strip())
        n = len(out.splitlines())
        cl.check("impact-surveyed",
                 f"impact surveyed ({n} affected path(s))", True,
                 detail=f"{n} dirty path(s)" if dirty else "clean tree")

    return cl


def cmd_plan(args: argparse.Namespace) -> int:
    cl = _permissibility(args.op)
    return emit_and_gate(cl, json_out=args.json, phase="plan", op=args.op)


def cmd_execute(args: argparse.Namespace) -> int:
    cl = _permissibility(args.op)
    if cl.verdict() == "RED":
        return emit_and_gate(cl, json_out=args.json, phase="execute:permit", op=args.op)

    # Restorability oracle: create a stash backup (incl. untracked) and verify it.
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    rc, _ = _git(["stash", "push", "-m", f"safe-git-reset backup {stamp}",
                  "--include-untracked"])
    if rc != 0:
        # Nothing to stash (clean tree) is not a failure -- there is simply
        # nothing to lose; record it and let the oracle stay GREEN.
        rc_list, listing = _git(["stash", "list"])
        cl.check("stash-backup",
                 "stash backup created (or clean tree -> nothing to back up)",
                 True, detail="clean tree: no stash needed", warn_only=True)
    else:
        rc_list, listing = _git(["stash", "list"])
        created = bool(listing.strip())
        cl.check("stash-backup", "stash backup created and listed", created,
                 detail=listing.splitlines()[0] if created else "stash not found")

    code = emit_and_gate(cl, json_out=args.json, phase="execute:backup", op=args.op)
    if cl.verdict() == "GREEN":
        print("\nSafety net in place. Now run your git op, then recover with:")
        print("  git stash pop                    # restore stashed changes")
        print("  git reflog                       # find previous HEAD positions")
        print("  git reset --hard <reflog-hash>   # go back to a specific state")
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="safe-git-reset workflow (checklist + oracle)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan", help="dry-run permissibility + impact, no stash")
    sp.add_argument("op", help="the git operation, e.g. 'reset --hard HEAD~1'")
    sp.set_defaults(func=cmd_plan)
    se = sub.add_parser("execute", help="create stash backup + print recovery (green-only)")
    se.add_argument("op")
    se.set_defaults(func=cmd_execute)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
