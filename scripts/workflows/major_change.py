#!/usr/bin/env python3
"""major-change workflow: mechanism-contract form of the /major-change skill (WSH-D4).

This one is special: a "major change" is an inherently human-judgment decision,
so the script does NOT auto-approve. Instead it mechanizes the *structural*
contract the prose protocol demanded -- a proposal is well-formed enough to put
to a human iff it states the change, lists >= 2 alternatives INCLUDING a
"do nothing" option, and gives an impact analysis with a rollback difficulty.

The oracle therefore answers "is this proposal complete enough to review?", NOT
"is this change approved?". Human approval remains an out-of-band gate (the
WSH-D2 router treats this as a high-risk capability whose postcondition is the
recorded human decision). GREEN means "ready to present"; it never means "go".

Subcommands
-----------
  check <proposal>   <proposal> is a path to a markdown/text file OR inline text.
                     Prints the structural checklist + oracle.

Exit codes: 0 = ORACLE GREEN (well-formed, present to human); 2 = RED (incomplete
proposal -- fill the missing sections before asking for approval); 1 = usage.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import Checklist, emit_and_gate, find_project_root  # noqa: E402

ROOT = find_project_root()

_DO_NOTHING_RE = re.compile(r"\bdo\s*nothing\b", re.IGNORECASE)
_ROLLBACK_RE = re.compile(r"\brollback\b", re.IGNORECASE)
_IMPACT_RE = re.compile(r"\b(impact|what breaks|downstream|affected)\b", re.IGNORECASE)
# Alternatives: markdown table rows, bullet "Option X", or numbered list lines.
_ALT_RE = re.compile(r"(^\s*\|.*\|\s*$)|(\boption\s+[A-Z0-9]\b)|(^\s*[-*]\s+)|(^\s*\d+[.)]\s+)",
                     re.IGNORECASE | re.MULTILINE)


def _load(arg: str) -> str:
    p = Path(arg)
    if p.exists() and p.is_file():
        return p.read_text(encoding="utf-8")
    return arg


def _build_checklist(text: str) -> Checklist:
    cl = Checklist("major-change")

    stated = len(text.strip()) >= 20
    cl.check("change-stated", "the proposed change is stated", stated,
             detail="" if stated else "proposal is empty or too short")

    n_alts = len(_ALT_RE.findall(text))
    cl.check("alternatives-listed", "at least 2 alternatives are presented",
             n_alts >= 2, detail=f"{n_alts} alternative line(s) detected")

    has_do_nothing = bool(_DO_NOTHING_RE.search(text))
    cl.check("includes-do-nothing", "alternatives include a 'do nothing' option",
             has_do_nothing, detail="" if has_do_nothing else "no 'do nothing' baseline")

    has_impact = bool(_IMPACT_RE.search(text))
    cl.check("impact-analysis", "an impact analysis is present", has_impact,
             detail="" if has_impact else "no impact/what-breaks/downstream section")

    has_rollback = bool(_ROLLBACK_RE.search(text))
    cl.check("rollback-difficulty", "rollback difficulty is addressed", has_rollback,
             detail="" if has_rollback else "no rollback discussion")

    return cl


def cmd_check(args: argparse.Namespace) -> int:
    cl = _build_checklist(_load(args.proposal))
    code = emit_and_gate(cl, json_out=args.json, phase="check")
    if cl.verdict() == "GREEN" and not args.json:
        print("\nProposal is well-formed. Present it to the user and WAIT for "
              "explicit approval -- GREEN means 'ready to ask', never 'go'.")
    return code


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="major-change workflow (structural checklist + oracle)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub = p.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("check", help="check a proposal is complete enough to review")
    sc.add_argument("proposal", help="path to proposal file or inline text")
    sc.set_defaults(func=cmd_check)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
