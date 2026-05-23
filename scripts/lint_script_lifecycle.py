#!/usr/bin/env python3
"""Script-lifecycle lint (T-P1-346 / WSH-F1, blind spot 6).

The workspace's ``scripts/`` tree is split into four lifecycle namespaces
(see ``scripts/README.md``):

    infra/    -- persistent infrastructure. EXEMPT from this lint.
    migrate/  -- one-shot schema migrations (lifecycle-bound; see note below).
    seed/     -- one-shot data-seeding scripts (lifecycle-linted).
    tools/    -- ad-hoc / dev utilities (lifecycle-linted).

This lint enforces that *ephemeral* scripts carry an explicit lifecycle
marker so they do not silently accumulate as dead weight. It scans the
lifecycle namespaces, classifies each script, and (under ``--strict``)
fails CI when stale/unmarked scripts are found.

Lifecycle markers (a line anywhere in the file, usually in the header):

    # SAFE_DELETE_AFTER: YYYY-MM-DD
        Explicit retention date. Unexpired (date in the future) -> PASS.
        Expired (date today-or-earlier) -> CLEANUP_CANDIDATE.

    # RUN_ONCE
        Declares the script is intentionally one-shot. Counts as an
        acknowledged marker (suppresses the stale-unmarked WARN), but the
        lint still nudges (in --verbose) to add a SAFE_DELETE_AFTER so the
        script eventually self-retires.

Classification per script (AC3 branches):

    PASS              -- in infra/ (exempt), OR has an unexpired
                         SAFE_DELETE_AFTER, OR is younger than the age
                         threshold, OR carries RUN_ONCE.
    CLEANUP_CANDIDATE -- SAFE_DELETE_AFTER present and expired.
    WARN              -- no marker AND age > --max-age-days (default 30).

Age is the script's *first-commit* date from ``git log`` when available
(filesystem mtime is unreliable across clones/copies -- a fresh clone
rewrites every mtime to checkout time); it falls back to mtime only when
the file is untracked or git is unavailable. (Edge clause of the task.)

Follows the workspace ``audit_*.py`` calling convention (AC5):
``--json`` / ``--strict`` / ``--verbose`` flags, ``[OK]/[WARN]/[GAP]``
human lines, and a non-zero exit under ``--strict`` when findings exist.

Usage:
    python scripts/lint_script_lifecycle.py            # human report, exit 0 (CI-soft)
    python scripts/lint_script_lifecycle.py --strict   # exit 1 if any WARN/CLEANUP found
    python scripts/lint_script_lifecycle.py --json      # machine-readable
    python scripts/lint_script_lifecycle.py --verbose   # per-script notes
    python scripts/lint_script_lifecycle.py --root DIR  # lint a different scripts/ root (tests)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# Namespaces under scripts/ that this lint manages.
EXEMPT_NAMESPACES = ("infra",)          # persistent -- never warned (AC1/AC2)
LIFECYCLE_NAMESPACES = ("seed", "tools")  # ephemeral -- linted (AC2)
# migrate/ is also lifecycle-bound but predates this convention and ships
# already-committed run-once migrations; it is documented in scripts/README.md
# and can be opted into with --include-migrate to avoid retroactive churn.

DEFAULT_MAX_AGE_DAYS = 30

_SCRIPT_GLOBS = ("*.py", "*.sh")
# Files that are namespace scaffolding, not lifecycle-managed scripts.
_SKIP_NAMES = {"__init__.py", "README.md", ".gitkeep"}

_SAFE_DELETE_RE = re.compile(
    r"SAFE_DELETE_AFTER\s*[:=]\s*(\d{4}-\d{2}-\d{2})"
)
_RUN_ONCE_RE = re.compile(r"\bRUN_ONCE\b")

# Outcome constants
PASS = "PASS"
CLEANUP_CANDIDATE = "CLEANUP_CANDIDATE"
WARN = "WARN"
SKIPPED = "SKIPPED"


@dataclass
class Finding:
    path: str                 # repo-relative (or root-relative under --root)
    namespace: str            # infra | migrate | seed | tools
    outcome: str              # PASS | CLEANUP_CANDIDATE | WARN | SKIPPED
    age_days: int | None      # None when undeterminable
    age_source: str           # git | mtime | unknown
    marker: str               # safe_delete_after:<date> | run_once | none
    detail: str = ""

    @property
    def is_finding(self) -> bool:
        """A finding the --strict gate trips on."""
        return self.outcome in (WARN, CLEANUP_CANDIDATE)


# --- git / age helpers ------------------------------------------------------

def _git_first_commit_epoch(path: Path, repo: Path) -> int | None:
    """Unix epoch of the file's FIRST commit, or None if untracked/no-git."""
    try:
        cp = subprocess.run(
            ["git", "log", "--diff-filter=A", "--follow",
             "--format=%at", "--", str(path)],
            cwd=str(repo), capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if cp.returncode != 0:
        return None
    lines = [ln for ln in cp.stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    # last line = oldest (add) commit
    try:
        return int(lines[-1].strip())
    except ValueError:
        return None


def _age_days(path: Path, repo: Path, now: _dt.datetime) -> tuple[int | None, str]:
    """(age_in_days, source). Prefer git first-commit; fall back to mtime."""
    epoch = _git_first_commit_epoch(path, repo)
    if epoch is not None:
        created = _dt.datetime.fromtimestamp(epoch, tz=now.tzinfo)
        return max(0, (now - created).days), "git"
    try:
        mtime = _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
        return max(0, (now - mtime).days), "mtime"
    except OSError:
        return None, "unknown"


def _read_markers(path: Path) -> tuple[_dt.date | None, bool]:
    """Return (safe_delete_after_date, has_run_once)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, False
    sd = None
    m = _SAFE_DELETE_RE.search(text)
    if m:
        try:
            sd = _dt.date.fromisoformat(m.group(1))
        except ValueError:
            sd = None
    return sd, bool(_RUN_ONCE_RE.search(text))


# --- core classification ----------------------------------------------------

def _iter_scripts(ns_dir: Path):
    for pat in _SCRIPT_GLOBS:
        for p in sorted(ns_dir.glob(pat)):
            if p.name in _SKIP_NAMES or not p.is_file():
                continue
            yield p


def classify(
    scripts_root: Path,
    *,
    repo: Path | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    include_migrate: bool = False,
    today: _dt.date | None = None,
) -> list[Finding]:
    """Walk the namespaces under ``scripts_root`` and classify each script."""
    repo = repo or scripts_root.parent
    today = today or _dt.date.today()
    now = _dt.datetime.combine(today, _dt.time(), tzinfo=None)

    linted = list(LIFECYCLE_NAMESPACES)
    if include_migrate:
        linted.append("migrate")

    findings: list[Finding] = []

    # Exempt namespaces: record as SKIPPED so the report is explicit (AC1/AC2).
    for ns in EXEMPT_NAMESPACES:
        ns_dir = scripts_root / ns
        if not ns_dir.is_dir():
            continue
        for p in _iter_scripts(ns_dir):
            findings.append(Finding(
                path=str(p.relative_to(repo)).replace("\\", "/"),
                namespace=ns, outcome=SKIPPED, age_days=None,
                age_source="n/a", marker="exempt",
                detail="infra/ is persistent -- exempt from lifecycle lint",
            ))

    for ns in linted:
        ns_dir = scripts_root / ns
        if not ns_dir.is_dir():
            continue
        for p in _iter_scripts(ns_dir):
            rel = str(p.relative_to(repo)).replace("\\", "/")
            sd_date, run_once = _read_markers(p)
            age, src = _age_days(p, repo, now)

            if sd_date is not None:
                marker = f"safe_delete_after:{sd_date.isoformat()}"
                if sd_date <= today:
                    findings.append(Finding(
                        rel, ns, CLEANUP_CANDIDATE, age, src, marker,
                        detail=f"SAFE_DELETE_AFTER {sd_date.isoformat()} expired "
                               f"(today {today.isoformat()}) -- safe to delete",
                    ))
                else:
                    findings.append(Finding(
                        rel, ns, PASS, age, src, marker,
                        detail=f"retained until {sd_date.isoformat()}",
                    ))
                continue

            if run_once:
                findings.append(Finding(
                    rel, ns, PASS, age, src, "run_once",
                    detail="RUN_ONCE acknowledged; consider adding SAFE_DELETE_AFTER",
                ))
                continue

            # No marker.
            if age is not None and age > max_age_days:
                findings.append(Finding(
                    rel, ns, WARN, age, src, "none",
                    detail=f"unmarked & {age}d old (> {max_age_days}d) -- add "
                           f"SAFE_DELETE_AFTER or RUN_ONCE",
                ))
            else:
                age_txt = f"{age}d" if age is not None else "age?"
                findings.append(Finding(
                    rel, ns, PASS, age, src, "none",
                    detail=f"unmarked but young ({age_txt} <= {max_age_days}d)",
                ))

    return findings


# --- reporting --------------------------------------------------------------

_ICON = {PASS: "[OK]", CLEANUP_CANDIDATE: "[GAP]", WARN: "[WARN]", SKIPPED: "[skip]"}


def print_human(findings: list[Finding], verbose: bool) -> None:
    print("## SCRIPT-LIFECYCLE LINT (T-P1-346 / WSH-F1)\n")
    if not findings:
        print("  (no scripts in lifecycle namespaces yet -- nothing to lint)")
    shown = findings if verbose else [f for f in findings if f.outcome != SKIPPED]
    if not shown and not verbose:
        print("  all lifecycle-namespace scripts pass (no findings)")
    for f in shown:
        line = f"  {_ICON[f.outcome]} {f.path}  [{f.namespace}]"
        if verbose or f.is_finding:
            line += f"  -- {f.detail}"
        print(line)
    warns = [f for f in findings if f.outcome == WARN]
    cleanups = [f for f in findings if f.outcome == CLEANUP_CANDIDATE]
    skipped = [f for f in findings if f.outcome == SKIPPED]
    print("\n" + "=" * 72)
    print(f"SUMMARY: {len(findings)} scripts | {len(warns)} stale-unmarked | "
          f"{len(cleanups)} cleanup-candidates | {len(skipped)} exempt(infra)")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Script-lifecycle lint (T-P1-346 / WSH-F1): flag stale/unmarked "
                    "seed+tools scripts; infra/ exempt."
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any stale-unmarked or cleanup-candidate found "
                         "(CI-blockable, AC4)")
    ap.add_argument("--verbose", action="store_true",
                    help="show every script incl. PASS and exempt infra/")
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                    help=f"age threshold for unmarked scripts (default {DEFAULT_MAX_AGE_DAYS})")
    ap.add_argument("--include-migrate", action="store_true",
                    help="also lint migrate/ (default: skipped, predates convention)")
    ap.add_argument("--root", type=str, default=None,
                    help="scripts/ root to lint (default: this repo's scripts/)")
    args = ap.parse_args(argv)

    scripts_root = Path(args.root).resolve() if args.root else (WORKSPACE_ROOT / "scripts")
    findings = classify(
        scripts_root,
        max_age_days=args.max_age_days,
        include_migrate=args.include_migrate,
    )

    if args.json:
        warns = [f for f in findings if f.outcome == WARN]
        cleanups = [f for f in findings if f.outcome == CLEANUP_CANDIDATE]
        print(json.dumps({
            "findings": [asdict(f) for f in findings],
            "counts": {
                "total": len(findings),
                "stale_unmarked": len(warns),
                "cleanup_candidates": len(cleanups),
                "exempt_infra": len([f for f in findings if f.outcome == SKIPPED]),
            },
        }, indent=2, ensure_ascii=False))
    else:
        print_human(findings, args.verbose)

    findings_count = sum(1 for f in findings if f.is_finding)
    if args.strict and findings_count:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
