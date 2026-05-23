#!/usr/bin/env python3
"""deploy-check workflow: mechanism-contract form of the /deploy-check skill (WSH-D4).

Replaces the prose pre-deployment checklist with a script that prints a
machine-checkable checklist and a non-negotiable oracle verdict. The caller
(skill body / WSH-D2 router) proceeds only on GREEN.

Subcommands
-----------
  check [dir]   Run the pre-deploy gate over a project dir (default: project
                root). Fast filesystem/git checks always run; tests + lint run
                unless skipped. Oracle = "is this safe to deploy?".

Exit codes: 0 = ORACLE GREEN; 2 = ORACLE RED (blockers printed); 1 = usage.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import Checklist, emit_and_gate, find_project_root  # noqa: E402

ROOT = find_project_root()

_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")


def _git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                             text=True, timeout=30)
        return out.returncode, out.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _changed_files(cwd: Path) -> list[str]:
    for base in ("main", "master"):
        rc, out = _git(["diff", "--name-only", f"{base}...HEAD"], cwd)
        if rc == 0 and out.strip():
            return [l for l in out.splitlines() if l.strip()]
    # Fall back to uncommitted+staged names.
    rc, out = _git(["status", "--porcelain"], cwd)
    return [l[3:] for l in out.splitlines() if l.strip()] if rc == 0 else []


def _run(cmd: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str]:
    try:
        out = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                             timeout=timeout)
        return out.returncode, (out.stdout + out.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def _detect_and_run_tests(cwd: Path) -> tuple[str, str]:
    """Return (status, detail). status in PASS/FAIL/SKIP."""
    if (cwd / "pyproject.toml").exists() or list(cwd.glob("test_*.py")) or (cwd / "tests").is_dir():
        rc, out = _run([sys.executable, "-m", "pytest", "-q"], cwd)
        if rc == 5:  # pytest: no tests collected
            return "SKIP", "pytest: no tests collected"
        return ("PASS" if rc == 0 else "FAIL"), f"pytest rc={rc}"
    if (cwd / "package.json").exists():
        rc, out = _run(["npm", "test"], cwd)
        return ("PASS" if rc == 0 else "FAIL"), f"npm test rc={rc}"
    return "SKIP", "no test framework detected"


def _detect_and_run_lint(cwd: Path) -> tuple[str, str]:
    if (cwd / "pyproject.toml").exists() or (cwd / "ruff.toml").exists():
        rc, out = _run(["ruff", "check", "."], cwd, timeout=120)
        if rc == 127:
            return "SKIP", "ruff not installed"
        return ("PASS" if rc == 0 else "FAIL"), f"ruff rc={rc}"
    return "SKIP", "no linter configured"


def _build_checklist(target: Path, skip_tests: bool, skip_lint: bool) -> Checklist:
    cl = Checklist("deploy-check")

    rc, out = _git(["status", "--porcelain"], target)
    clean = (rc == 0 and not out.strip())
    cl.check("clean-worktree", "working tree is clean (no uncommitted changes)",
             clean, detail="" if clean else f"{len(out.splitlines())} dirty path(s)")

    changed = _changed_files(target)
    todo_hits = []
    for rel in changed:
        fp = target / rel
        if fp.is_file():
            try:
                for n, line in enumerate(fp.read_text(encoding="utf-8",
                                                       errors="ignore").splitlines(), 1):
                    if _TODO_RE.search(line):
                        todo_hits.append(f"{rel}:{n}")
            except OSError:
                pass
    cl.check("no-todo-in-changed", "no TODO/FIXME in changed files",
             not todo_hits, detail=", ".join(todo_hits[:5]) if todo_hits else "")

    progress = target / "PROGRESS.md"
    recent = progress.exists() and date.today().isoformat() in \
        progress.read_text(encoding="utf-8", errors="ignore")
    cl.check("progress-updated", "PROGRESS.md has a recent (today) entry", recent,
             detail="no entry for today" if not recent else "", warn_only=True)

    if skip_tests:
        cl.add("tests", "test suite passes", "WARN", detail="skipped (--skip-tests)")
    else:
        status, detail = _detect_and_run_tests(target)
        cl.add("tests", "test suite passes", status, detail=detail)

    if skip_lint:
        cl.add("lint", "linter passes", "WARN", detail="skipped (--skip-lint)")
    else:
        status, detail = _detect_and_run_lint(target)
        cl.add("lint", "linter passes", status, detail=detail)

    return cl


def cmd_check(args: argparse.Namespace) -> int:
    target = Path(args.dir).resolve() if args.dir else ROOT
    cl = _build_checklist(target, args.skip_tests, args.skip_lint)
    return emit_and_gate(cl, json_out=args.json, phase="check", target=str(target))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="deploy-check workflow (checklist + oracle)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("check", help="run the pre-deploy gate")
    sc.add_argument("dir", nargs="?", default=None, help="project dir (default: root)")
    sc.add_argument("--skip-tests", action="store_true", help="do not run the test suite")
    sc.add_argument("--skip-lint", action="store_true", help="do not run the linter")
    sc.set_defaults(func=cmd_check)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
