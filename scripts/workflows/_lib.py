#!/usr/bin/env python3
"""Shared primitives for high-risk workflow scripts (WSH-D1).

A *workflow* is the mechanism-contract form of a previously prose-only skill.
Instead of the model reading numbered steps and "agreeing" to follow them, it
runs a workflow script that:

  1. evaluates a set of machine-checkable safety preconditions (the checklist),
  2. renders them as deterministic ``[PASS]/[FAIL]/[WARN]`` lines, and
  3. emits a single non-negotiable oracle verdict (``GREEN``/``RED``).

The contract for every caller (skill body, meta-skill router in WSH-D2):
**proceed only on GREEN.** A RED verdict prints the failing items and exits
non-zero so nothing can silently proceed. This module is the single source of
that gate so each workflow only declares *what* to check, never *how* to gate.

Stdlib only. No third-party deps.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"


def find_project_root(start: Optional[Path] = None) -> Path:
    """Walk up from ``start`` (default: this file) to the dir containing ``.claude``.

    Mirrors the proven ``_find_project_root`` walk-up used elsewhere in the
    workspace (see CLAUDE.md, verified-mechanical-apply class) rather than a
    brittle ``parent.parent`` count.
    """
    here = (start or Path(__file__)).resolve()
    for cand in (here, *here.parents):
        if (cand / ".claude").is_dir():
            return cand
    # Fallback: two levels up (scripts/workflows/ -> repo root) — best effort.
    return Path(__file__).resolve().parent.parent.parent


@dataclass
class CheckItem:
    """One machine-checkable precondition.

    ``status`` is one of PASS / FAIL / WARN. WARN never blocks the oracle (it is
    advisory, e.g. "uncommitted changes present"); FAIL always turns the verdict
    RED. ``detail`` is a short human-readable reason rendered alongside the id.
    """

    id: str
    description: str
    status: str
    detail: str = ""


@dataclass
class Checklist:
    """An ordered collection of CheckItems plus the oracle gate over them."""

    workflow: str
    items: list[CheckItem] = field(default_factory=list)

    def add(self, item_id: str, description: str, status: str, detail: str = "") -> CheckItem:
        item = CheckItem(item_id, description, status, detail)
        self.items.append(item)
        return item

    def check(self, item_id: str, description: str, ok: bool, detail: str = "",
              warn_only: bool = False) -> CheckItem:
        """Add an item from a boolean predicate.

        ``ok=True`` -> PASS. ``ok=False`` -> FAIL, unless ``warn_only`` (then WARN).
        """
        if ok:
            status = PASS
        else:
            status = WARN if warn_only else FAIL
        return self.add(item_id, description, status, detail)

    @property
    def failing(self) -> list[CheckItem]:
        return [i for i in self.items if i.status == FAIL]

    @property
    def warnings(self) -> list[CheckItem]:
        return [i for i in self.items if i.status == WARN]

    def verdict(self) -> str:
        """Non-negotiable oracle: GREEN iff zero FAIL items (WARN allowed)."""
        return "RED" if self.failing else "GREEN"

    def render(self) -> str:
        """Machine-checkable text block. One item per line + a final ORACLE line."""
        lines = [f"=== WORKFLOW: {self.workflow} ===",
                 "--- CHECKLIST ---"]
        for i in self.items:
            tail = f" -- {i.detail}" if i.detail else ""
            lines.append(f"[{i.status}] {i.id}: {i.description}{tail}")
        verdict = self.verdict()
        lines.append("--- ORACLE ---")
        if verdict == "GREEN":
            lines.append("ORACLE: GREEN -- all checks passed; safe to proceed.")
        else:
            lines.append(
                f"ORACLE: RED -- {len(self.failing)} failing item(s); MUST NOT proceed:")
            for i in self.failing:
                lines.append(f"  FAIL {i.id}: {i.detail or i.description}")
        return "\n".join(lines)

    def to_event(self, **extra) -> dict:
        return {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "actor": f"workflow.{self.workflow}",
            "workflow": self.workflow,
            "verdict": self.verdict(),
            "checks": [{"id": i.id, "status": i.status, "detail": i.detail}
                       for i in self.items],
            "n_fail": len(self.failing),
            "n_warn": len(self.warnings),
            **extra,
        }


def log_event(checklist: Checklist, *, root: Optional[Path] = None, **extra) -> Path:
    """Append one JSON line recording this oracle evaluation to events.jsonl.

    The file is gitignored runtime state (see .gitignore B7 block) and created
    on demand. Returns the path written.
    """
    root = root or find_project_root()
    events_path = root / ".claude" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(checklist.to_event(**extra), ensure_ascii=True, sort_keys=True)
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return events_path


def emit_and_gate(checklist: Checklist, *, json_out: bool = False,
                  log: bool = True, root: Optional[Path] = None, **extra) -> int:
    """Render the checklist, log the oracle evaluation, and return an exit code.

    Exit code is the gate the caller obeys: ``0`` on GREEN, ``2`` on RED. The
    RED path always prints the failing items first (AC3: never silently proceed).
    """
    if log:
        log_event(checklist, root=root, **extra)
    if json_out:
        print(json.dumps(checklist.to_event(**extra), ensure_ascii=True, indent=2))
    else:
        print(checklist.render())
    return 0 if checklist.verdict() == "GREEN" else 2
