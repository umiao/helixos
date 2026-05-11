"""SQLite-backed task store for Claude Code task management.

Provides:
- Schema init with WAL mode and foreign keys
- CRUD operations for tasks and dependencies
- Auto-generated task IDs (T-P{priority}-{N})
- Deterministic TASKS.md projection
- Archival of completed tasks
- Batch operations with atomic transactions
- Lossless import from existing TASKS.md
"""

import datetime
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"

# --- Data classes ---


@dataclass
class Task:
    """A task in the task store."""

    id: str
    title: str
    status: str = "active"
    priority: str = "P2"
    complexity: str = "S"
    description: str = ""
    completed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    sort_order: int = 0
    depends_on: list[str] = field(default_factory=list)


@dataclass
class ArchivedTask:
    """An archived (completed and removed) task."""

    id: str
    title: str
    priority: str | None = None
    complexity: str | None = None
    completed_at: str | None = None
    archived_at: str = ""
    summary: str = ""
    depends_on_snapshot: list[str] = field(default_factory=list)


@dataclass
class ParsedTask:
    """A task parsed from TASKS.md markdown (used for import/verify)."""

    id: str
    title: str
    status: str
    priority: str
    complexity: str
    description: str
    depends_on: list[str] = field(default_factory=list)
    completed_at: str | None = None
    sort_order: int = 0


# --- SQL Schema ---

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','in_progress','completed','blocked')),
    priority    TEXT NOT NULL DEFAULT 'P2'
                CHECK(priority IN ('P0','P1','P2','P3')),
    complexity  TEXT DEFAULT 'S'
                CHECK(complexity IN ('S','M','L')),
    description TEXT DEFAULT '',
    completed_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    upstream_id   TEXT NOT NULL REFERENCES tasks(id),
    downstream_id TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (upstream_id, downstream_id),
    CHECK(upstream_id != downstream_id)
);

CREATE TABLE IF NOT EXISTS archived_tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    priority    TEXT,
    complexity  TEXT,
    completed_at TEXT,
    archived_at TEXT NOT NULL,
    summary     TEXT DEFAULT '',
    depends_on_snapshot TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# --- Helpers ---


def _now() -> str:
    """Return current UTC time as ISO 8601 without timezone suffix."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _today() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.date.today().isoformat()


def _try_events_append(project_root: Path, payload: dict[str, Any]) -> None:
    """Best-effort events.jsonl append. Silently no-ops if lib unavailable.

    Imports `scripts/lib/events` lazily; sub-projects may not have the
    B7 events lib propagated yet (tracked by T-P2-322). Failure to import
    or write is logged to stderr but never raises — the audit trail is
    advisory, not load-bearing.
    """
    import sys as _sys
    try:
        _sys.path.insert(0, str(project_root / "scripts"))
        from lib import events as _events  # noqa: PLC0415
        _events.append(project_root, payload)
    except (ImportError, ValueError, OSError) as exc:
        _sys.stderr.write(f"WARN: events.append failed: {exc}\n")
    finally:
        try:
            _sys.path.remove(str(project_root / "scripts"))
        except ValueError:
            pass


# --- TaskStore ---


class TaskStore:
    """SQLite-backed task store.

    Args:
        db_path: Path to the SQLite database file. Use ":memory:" for testing.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create the SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Initialize the database schema."""
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        # Set schema version if not present
        existing = conn.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO metadata (key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
        # Initialize next_id_counters if not present
        existing_counters = conn.execute(
            "SELECT value FROM metadata WHERE key='next_id_counters'"
        ).fetchone()
        if not existing_counters:
            conn.execute(
                "INSERT INTO metadata (key, value) VALUES ('next_id_counters', ?)",
                (json.dumps({"P0": 1, "P1": 1, "P2": 1, "P3": 1}),),
            )
        conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- ID Generation ---

    def _next_id(self, priority: str) -> str:
        """Generate the next task ID for the given priority.

        Uses a global counter (shared across all priorities) to ensure unique
        numbers. Scans both tasks and archived_tasks tables to find the max
        existing number, then picks max(that, stored_counter) + 1.
        """
        conn = self._get_conn()

        # Find max existing number across ALL priorities (global counter)
        max_num = 0

        for table in ["tasks", "archived_tasks"]:
            rows = conn.execute(f"SELECT id FROM {table}").fetchall()
            for r in rows:
                match = re.match(r"T-P\d+-(\d+)", r[0])
                if match:
                    max_num = max(max_num, int(match.group(1)))

        # Also check stored global counter
        counters_row = conn.execute(
            "SELECT value FROM metadata WHERE key='next_id_counters'"
        ).fetchone()
        counters = json.loads(counters_row[0]) if counters_row else {}
        # Use max across all stored counters as the global floor
        stored_max = max(counters.values()) if counters else 1

        next_num = max(max_num + 1, stored_max)
        new_id = f"T-{priority}-{next_num}"

        # Update all counters to at least next_num + 1
        for p in counters:
            counters[p] = max(counters[p], next_num + 1)
        conn.execute(
            "UPDATE metadata SET value=? WHERE key='next_id_counters'",
            (json.dumps(counters),),
        )

        return new_id

    # --- CRUD ---

    def add(
        self,
        title: str,
        priority: str = "P2",
        complexity: str = "S",
        description: str = "",
        depends_on: list[str] | None = None,
        task_id: str | None = None,
    ) -> Task:
        """Add a new task.

        Args:
            title: Task title.
            priority: P0-P3.
            complexity: S, M, or L.
            description: Full description/AC block (markdown).
            depends_on: List of upstream task IDs.
            task_id: Optional explicit ID (for import). Auto-generated if None.

        Returns:
            The created Task.
        """
        conn = self._get_conn()
        now = _now()

        if task_id is None:
            task_id = self._next_id(priority)

        # Get max sort_order for this priority
        row = conn.execute(
            "SELECT MAX(sort_order) FROM tasks WHERE priority=?", (priority,)
        ).fetchone()
        max_sort = row[0] if row[0] is not None else 0
        sort_order = max_sort + 100

        conn.execute(
            """INSERT INTO tasks (id, title, status, priority, complexity,
               description, created_at, updated_at, sort_order)
               VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
            (task_id, title, priority, complexity, description, now, now, sort_order),
        )

        if depends_on:
            for dep_id in depends_on:
                conn.execute(
                    "INSERT OR IGNORE INTO task_dependencies (upstream_id, downstream_id) VALUES (?, ?)",
                    (dep_id, task_id),
                )

        conn.commit()

        return Task(
            id=task_id,
            title=title,
            status="active",
            priority=priority,
            complexity=complexity,
            description=description,
            created_at=now,
            updated_at=now,
            sort_order=sort_order,
            depends_on=depends_on or [],
        )

    def get(self, task_id: str) -> Task | None:
        """Get a task by ID.

        Returns None if not found.
        """
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None

        deps = [
            r[0]
            for r in conn.execute(
                "SELECT upstream_id FROM task_dependencies WHERE downstream_id=?",
                (task_id,),
            ).fetchall()
        ]

        return Task(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            priority=row["priority"],
            complexity=row["complexity"],
            description=row["description"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            sort_order=row["sort_order"],
            depends_on=deps,
        )

    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        complexity: str | None = None,
        description: str | None = None,
    ) -> Task | None:
        """Update a task's fields. Only provided fields are changed.

        Returns the updated Task, or None if not found.

        Raises ValueError when `status='completed'` is requested on a task with
        `human_review=1` — those tasks must go through `complete_task()` with
        a `reviewer` argument (T-P1-319 B2 fold-in).
        """
        conn = self._get_conn()
        existing = self.get(task_id)
        if not existing:
            return None

        if status == "completed":
            # B2 fold-in gate -- only meaningful when the B1 schema has
            # added the human_review column. Un-migrated DBs (e.g. test
            # fixtures that build a fresh schema via _SCHEMA_SQL) skip
            # the check entirely; the legacy path is unaffected.
            cols = {c[1] for c in conn.execute("PRAGMA table_info(tasks)")}
            if "human_review" in cols:
                hr_row = conn.execute(
                    "SELECT human_review FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                if hr_row and (hr_row[0] or 0) == 1:
                    raise ValueError(
                        f"task {task_id} has human_review=1; "
                        f"use 'task_db.py complete {task_id} --reviewer NAME' "
                        f"to approve and complete"
                    )

        now = _now()
        updates: list[str] = []
        params: list[Any] = []

        if title is not None:
            updates.append("title=?")
            params.append(title)
        if status is not None:
            updates.append("status=?")
            params.append(status)
            if status == "completed" and existing.status != "completed":
                updates.append("completed_at=?")
                params.append(_today())
            elif status != "completed" and existing.status == "completed":
                updates.append("completed_at=?")
                params.append(None)
        if priority is not None:
            updates.append("priority=?")
            params.append(priority)
        if complexity is not None:
            updates.append("complexity=?")
            params.append(complexity)
        if description is not None:
            updates.append("description=?")
            params.append(description)

        if not updates:
            return existing

        updates.append("updated_at=?")
        params.append(now)
        params.append(task_id)

        conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE id=?",
            params,
        )
        conn.commit()

        return self.get(task_id)

    def complete_task(
        self,
        task_id: str,
        reviewer: str | None = None,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        """Complete a task respecting the human_review gate.

        Behavior (T-P1-319 B2 fold-in, folded from scripts/task_complete.py):
            * human_review=0 -> state='done', status='completed', completed_at=today
            * human_review=1, reviewer is None -> raises ValueError (gate not approved)
            * human_review=1, reviewer set -> bypasses gate, state='done',
              status='completed', writes audit event with reviewer name
            * Idempotent: re-running on an already-done task returns the
              current state with no_op=True

        Args:
            task_id: Task to complete.
            reviewer: When set, bypasses the human_review gate. The name is
                written to events.jsonl as an audit trail (grep-friendly).
            project_root: Workspace root for events.append. Defaults to the
                db_path's parent's parent (.claude/tasks.db -> root/).

        Returns:
            dict with ok, task_id, state, status, completed_at, reviewer, no_op.

        Raises:
            ValueError: if task not found, or if hr=1 and reviewer is None.
        """
        conn = self._get_conn()
        cols = {c[1] for c in conn.execute("PRAGMA table_info(tasks)")}
        select_cols = "id, status, state, human_review, completed_at"
        if "project_id" in cols:
            select_cols += ", project_id"
        row = conn.execute(
            f"SELECT {select_cols} FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"task {task_id} not found")

        if row["state"] == "done":
            return {
                "ok": True,
                "task_id": task_id,
                "state": "done",
                "status": row["status"],
                "completed_at": row["completed_at"],
                "reviewer": reviewer,
                "no_op": True,
            }

        hr = row["human_review"] or 0
        if hr == 1 and reviewer is None:
            raise ValueError(
                f"task {task_id} has human_review=1; "
                f"use complete_task(..., reviewer='<name>') to approve and complete"
            )

        today = _today()
        now = _now()
        prior_state = row["state"] or row["status"]
        proj_id = (row["project_id"] if "project_id" in cols else None) or "root"
        conn.execute(
            "UPDATE tasks SET status=?, state=?, completed_at=?, updated_at=? "
            "WHERE id=?",
            ("completed", "done", today, now, task_id),
        )
        conn.commit()

        if project_root is None:
            project_root = Path(self.db_path).resolve().parent.parent
        _try_events_append(
            project_root,
            {
                "ts": now,
                "project_id": proj_id,
                "task_id": task_id,
                "from_state": prior_state,
                "to_state": "done",
                "actor": "task_store.complete_task",
                "reason": "approved" if reviewer else "auto",
                "reviewer": reviewer,
                "human_review": hr,
            },
        )

        return {
            "ok": True,
            "task_id": task_id,
            "state": "done",
            "status": "completed",
            "completed_at": today,
            "reviewer": reviewer,
            "no_op": False,
        }

    def inspect_task(
        self,
        task_id: str,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        """Return current state + queue-file presence for a task.

        Mirrors the legacy `scripts/task_complete.py inspect` shape.
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, title, status, state, human_review, completed_at "
            "FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"task {task_id} not found"}

        if project_root is None:
            project_root = Path(self.db_path).resolve().parent.parent
        queue_file = (
            project_root / ".claude" / "review-queue" / f"{task_id}.yaml"
        )

        return {
            "ok": True,
            "task_id": task_id,
            "title": row["title"],
            "status": row["status"],
            "state": row["state"],
            "human_review": row["human_review"] or 0,
            "completed_at": row["completed_at"],
            "queue_file_present": queue_file.exists(),
            "queue_file_path": str(queue_file),
        }

    def delete(self, task_id: str) -> bool:
        """Delete a task and its dependency references.

        Returns True if the task existed and was deleted.
        """
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not existing:
            return False

        conn.execute(
            "DELETE FROM task_dependencies WHERE upstream_id=? OR downstream_id=?",
            (task_id, task_id),
        )
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
        return True

    def list_tasks(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
    ) -> list[Task]:
        """List tasks, optionally filtered by status and/or priority.

        Results are ordered by priority, sort_order, then id.
        """
        conn = self._get_conn()
        query = "SELECT * FROM tasks"
        conditions: list[str] = []
        params: list[str] = []

        if status:
            conditions.append("status=?")
            params.append(status)
        if priority:
            conditions.append("priority=?")
            params.append(priority)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY priority, sort_order ASC, id ASC"

        rows = conn.execute(query, params).fetchall()
        tasks = []
        for row in rows:
            deps = [
                r[0]
                for r in conn.execute(
                    "SELECT upstream_id FROM task_dependencies WHERE downstream_id=?",
                    (row["id"],),
                ).fetchall()
            ]
            tasks.append(
                Task(
                    id=row["id"],
                    title=row["title"],
                    status=row["status"],
                    priority=row["priority"],
                    complexity=row["complexity"],
                    description=row["description"],
                    completed_at=row["completed_at"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    sort_order=row["sort_order"],
                    depends_on=deps,
                )
            )
        return tasks

    def has_unblocked_tasks(self) -> bool:
        """Return True if any active task has all dependencies completed.

        Legacy `status='active'`-based picker. Kept for backcompat with
        existing Python callers (orchestrator.py, test_audit_dep_graph.py).
        New code SHOULD use :meth:`has_unblocked_state` (state-machine aware).
        """
        active = self.list_tasks(status="active")
        for task in active:
            if not task.depends_on:
                return True  # no deps = runnable
            if all(
                (dep := self.get(dep_id)) is not None
                and dep.status == "completed"
                for dep_id in task.depends_on
            ):
                return True
        return False

    # --- State-aware picker (T-P1-320 B3 fold-in) ---
    #
    # `_REVIEW_STATES` and the pickability contract mirror
    # scripts/task_pick.py exactly (the bridge this folds in). A task is
    # pickable iff state='ready' AND all upstream deps are state='done'
    # AND no lease is held. The legacy `status` column is intentionally
    # ignored at this layer; post-B1 the canonical source of truth is
    # `state`. CLI `has-unblocked` is wired through here so autorun
    # scripts see the new semantics without code changes (plain-text
    # yes/no contract preserved).

    _REVIEW_STATES_BX: tuple[str, ...] = (
        "review_pending", "rejected", "revision_requested",
    )

    def _b3_columns_present(self) -> bool:
        """Detect whether the B1 schema migration has run on this DB."""
        cols = {c[1] for c in self._get_conn().execute(
            "PRAGMA table_info(tasks)"
        )}
        return {"state", "human_review", "pid", "started_at"}.issubset(cols)

    def _b3_classify(
        self,
        task: dict[str, Any],
        deps: list[dict[str, Any]],
        project_root: Path,
    ) -> dict[str, Any]:
        """Pure function: row + deps + root -> pickability diagnostic."""
        state = task["state"]
        task_id = task["id"]
        deps_missing = [d["id"] for d in deps if d["state"] != "done"]
        approval_missing: str | None = None
        if state in self._REVIEW_STATES_BX:
            approval_missing = str(
                project_root / ".claude" / "approvals" / f"{task_id}.yaml"
            )
        lease_held_by: dict[str, Any] | None = None
        if state == "leased" and (task["pid"] is not None
                                  or task["started_at"] is not None):
            lease_held_by = {
                "pid": task["pid"],
                "pgid": task["pgid"],
                "started_at": task["started_at"],
                "ttl_seconds": task["ttl_seconds"],
                "last_heartbeat": task["last_heartbeat"],
            }
        pickable = (
            state == "ready"
            and not deps_missing
            and lease_held_by is None
        )
        reasons: list[str] = []
        if state != "ready":
            if state in self._REVIEW_STATES_BX:
                reasons.append(f"{state}; awaiting {approval_missing}")
            elif state == "leased":
                assert lease_held_by is not None
                ttl = lease_held_by.get("ttl_seconds")
                ttl_str = f"{ttl}s" if ttl is not None else "unset"
                reasons.append(
                    f"lease_held_by: pid={lease_held_by['pid']} "
                    f"pgid={lease_held_by['pgid']} "
                    f"started_at={lease_held_by['started_at']} "
                    f"ttl={ttl_str}"
                )
            else:
                reasons.append(f"state={state!r} (not 'ready')")
        if deps_missing:
            reasons.append(f"deps_missing: {deps_missing}")
        if state == "ready" and lease_held_by is not None:
            reasons.append("stale lease on a ready task (data drift)")
        return {
            "id": task_id,
            "title": task["title"],
            "state": state,
            "deps_missing": deps_missing,
            "approval_missing": approval_missing,
            "lease_held_by": lease_held_by,
            "human_review_pending": state in self._REVIEW_STATES_BX,
            "pickable": pickable,
            "reasons": reasons,
        }

    def _b3_fetch_task_row(
        self, task_id: str
    ) -> dict[str, Any] | None:
        conn = self._get_conn()
        row = conn.execute(
            """SELECT id, title, status, state, human_review,
                      priority, complexity, sort_order,
                      pid, pgid, started_at, ttl_seconds, last_heartbeat
               FROM tasks WHERE id=?""",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None

    def _b3_fetch_deps(
        self, task_id: str
    ) -> list[dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT t.id, t.state, t.status
               FROM task_dependencies td
               JOIN tasks t ON td.upstream_id = t.id
               WHERE td.downstream_id = ?
               ORDER BY t.id""",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def pick_one(
        self, project_root: Path | None = None
    ) -> dict[str, Any] | None:
        """Pick the highest-priority pickable task.

        State-aware: filters `state='ready'` AND deps all `state='done'`
        AND no lease held. Order: priority ASC (P0 first), sort_order ASC,
        id ASC.

        Returns:
            The classification dict for the chosen task, or None if no
            pickable task exists. Caller may read `result['id']`.
        """
        if not self._b3_columns_present():
            return None
        if project_root is None:
            project_root = Path(self.db_path).resolve().parent.parent
        conn = self._get_conn()
        candidates = conn.execute(
            """SELECT id FROM tasks
               WHERE state='ready'
                 AND (pid IS NULL AND started_at IS NULL)
               ORDER BY priority ASC, sort_order ASC, id ASC"""
        ).fetchall()
        for row in candidates:
            task = self._b3_fetch_task_row(row["id"])
            if task is None:
                continue
            deps = self._b3_fetch_deps(row["id"])
            result = self._b3_classify(task, deps, project_root)
            if result["pickable"]:
                return result
        return None

    def why_blocked(
        self, task_id: str, project_root: Path | None = None
    ) -> dict[str, Any] | None:
        """Return diagnostic JSON explaining why `task_id` isn't pickable.

        Returns None if task doesn't exist or DB hasn't been B1-migrated.
        """
        if not self._b3_columns_present():
            return None
        task = self._b3_fetch_task_row(task_id)
        if task is None:
            return None
        if project_root is None:
            project_root = Path(self.db_path).resolve().parent.parent
        deps = self._b3_fetch_deps(task_id)
        return self._b3_classify(task, deps, project_root)

    def has_unblocked_state(
        self, project_root: Path | None = None
    ) -> bool:
        """State-aware version of :meth:`has_unblocked_tasks`.

        Returns True iff at least one task is `state='ready'` AND has all
        deps `state='done'` AND no lease held. This is the canonical
        post-B1 picker; legacy `has_unblocked_tasks` (status='active')
        is retained for backcompat.
        """
        if not self._b3_columns_present():
            # Fallback to legacy semantics on un-migrated DBs.
            return self.has_unblocked_tasks()
        result = self.pick_one(project_root=project_root)
        return result is not None

    # --- Dependencies ---

    def add_dependency(self, task_id: str, depends_on: str) -> bool:
        """Add a dependency: task_id depends on depends_on.

        Returns True if added, False if either task doesn't exist.
        Raises ValueError for self-dependency.
        """
        if task_id == depends_on:
            raise ValueError(f"Self-dependency not allowed: {task_id}")

        conn = self._get_conn()
        # Verify both tasks exist
        for tid in [task_id, depends_on]:
            if not conn.execute("SELECT id FROM tasks WHERE id=?", (tid,)).fetchone():
                return False

        conn.execute(
            "INSERT OR IGNORE INTO task_dependencies (upstream_id, downstream_id) VALUES (?, ?)",
            (depends_on, task_id),
        )
        conn.commit()
        return True

    def remove_dependency(self, task_id: str, depends_on: str) -> bool:
        """Remove a dependency.

        Returns True if the dependency existed and was removed.
        """
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM task_dependencies WHERE upstream_id=? AND downstream_id=?",
            (depends_on, task_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    # --- Sort Order ---

    def reorder(self, task_id: str, *, after: str | None = None) -> bool:
        """Reorder a task within its priority group.

        Args:
            task_id: Task to move.
            after: Place task_id after this task. If None, move to beginning.

        Returns:
            True if reorder succeeded.
        """
        conn = self._get_conn()
        task = self.get(task_id)
        if not task:
            return False

        priority = task.priority

        if after is None:
            # Move to beginning: set sort_order to min - 100
            row = conn.execute(
                "SELECT MIN(sort_order) FROM tasks WHERE priority=?", (priority,)
            ).fetchone()
            min_sort = row[0] if row[0] is not None else 100
            new_order = min_sort - 100
        else:
            after_task = self.get(after)
            if not after_task or after_task.priority != priority:
                return False

            # Find the task immediately after the target
            next_row = conn.execute(
                """SELECT sort_order FROM tasks
                   WHERE priority=? AND sort_order > ? AND id != ?
                   ORDER BY sort_order ASC LIMIT 1""",
                (priority, after_task.sort_order, task_id),
            ).fetchone()

            if next_row:
                gap = next_row[0] - after_task.sort_order
                if gap > 1:
                    new_order = after_task.sort_order + gap // 2
                else:
                    # No gap -- renumber all tasks in this priority
                    self._renumber_priority(priority)
                    # Re-fetch after renumber
                    after_task = self.get(after)
                    if not after_task:
                        return False
                    next_row2 = conn.execute(
                        """SELECT sort_order FROM tasks
                           WHERE priority=? AND sort_order > ? AND id != ?
                           ORDER BY sort_order ASC LIMIT 1""",
                        (priority, after_task.sort_order, task_id),
                    ).fetchone()
                    new_order = (
                        after_task.sort_order + (next_row2[0] - after_task.sort_order) // 2
                        if next_row2
                        else after_task.sort_order + 100
                    )
            else:
                new_order = after_task.sort_order + 100

        conn.execute(
            "UPDATE tasks SET sort_order=?, updated_at=? WHERE id=?",
            (new_order, _now(), task_id),
        )
        conn.commit()
        return True

    def _renumber_priority(self, priority: str) -> None:
        """Renumber all tasks in a priority group with gaps of 100."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id FROM tasks WHERE priority=? ORDER BY sort_order ASC, id ASC",
            (priority,),
        ).fetchall()
        for i, row in enumerate(rows):
            conn.execute(
                "UPDATE tasks SET sort_order=? WHERE id=?",
                ((i + 1) * 100, row[0]),
            )
        conn.commit()

    # --- Archival ---

    def archive(self, *, max_completed: int = 20, keep_completed: int = 5) -> int:
        """Archive old completed tasks.

        Moves completed tasks beyond keep_completed count to archived_tasks.
        Dependencies are snapshotted as JSON before removal.

        Args:
            max_completed: Only archive if completed count exceeds this.
            keep_completed: Number of recent completed tasks to keep.

        Returns:
            Number of tasks archived.
        """
        conn = self._get_conn()

        completed = conn.execute(
            """SELECT id FROM tasks WHERE status='completed'
               ORDER BY completed_at DESC, id DESC"""
        ).fetchall()

        if len(completed) <= max_completed:
            return 0

        to_keep_ids = {r[0] for r in completed[:keep_completed]}
        to_archive = [r[0] for r in completed if r[0] not in to_keep_ids]

        archived_count = 0
        now = _now()

        for task_id in to_archive:
            task = self.get(task_id)
            if not task:
                continue

            # Snapshot dependencies
            deps = [
                r[0]
                for r in conn.execute(
                    "SELECT upstream_id FROM task_dependencies WHERE downstream_id=?",
                    (task_id,),
                ).fetchall()
            ]

            conn.execute(
                """INSERT OR REPLACE INTO archived_tasks
                   (id, title, priority, complexity, completed_at, archived_at, summary, depends_on_snapshot)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id,
                    task.title,
                    task.priority,
                    task.complexity,
                    task.completed_at,
                    now,
                    task.description,
                    json.dumps(deps),
                ),
            )

            # Remove dependency references
            conn.execute(
                "DELETE FROM task_dependencies WHERE upstream_id=? OR downstream_id=?",
                (task_id, task_id),
            )
            conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            archived_count += 1

        conn.commit()
        return archived_count

    def list_archived(self) -> list[ArchivedTask]:
        """List all archived tasks, ordered by archived_at DESC."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM archived_tasks ORDER BY archived_at DESC, id DESC"
        ).fetchall()
        return [
            ArchivedTask(
                id=r["id"],
                title=r["title"],
                priority=r["priority"],
                complexity=r["complexity"],
                completed_at=r["completed_at"],
                archived_at=r["archived_at"],
                summary=r["summary"],
                depends_on_snapshot=json.loads(r["depends_on_snapshot"] or "[]"),
            )
            for r in rows
        ]

    # --- Projection ---

    def project(self) -> str:
        """Generate deterministic TASKS.md content from the database.

        Returns:
            Complete TASKS.md markdown content.
        """
        conn = self._get_conn()
        lines: list[str] = []

        lines.append("# Task Backlog")
        lines.append("")
        lines.append("<!-- Auto-generated from .claude/tasks.db. Do not edit directly. -->")
        lines.append("<!-- Use: python .claude/hooks/task_db.py --help -->")
        lines.append("")

        # --- In Progress ---
        lines.append("## In Progress")
        lines.append("")
        in_progress = self.list_tasks(status="in_progress")
        for task in in_progress:
            lines.extend(self._format_task_block(task))
            lines.append("")

        # --- Active Tasks ---
        lines.append("## Active Tasks")
        lines.append("")

        priority_labels = {
            "P0": "P0 -- Must Have (core functionality)",
            "P1": "P1 -- Should Have (agentic intelligence)",
            "P2": "P2 -- Nice to Have",
            "P3": "P3 -- Stretch Goals",
        }

        for priority in ["P0", "P1", "P2", "P3"]:
            lines.append(f"### {priority_labels[priority]}")
            lines.append("")
            active = conn.execute(
                """SELECT * FROM tasks
                   WHERE status='active' AND priority=?
                   ORDER BY sort_order ASC, id ASC""",
                (priority,),
            ).fetchall()
            for row in active:
                task = self._row_to_task(row, conn)
                lines.extend(self._format_task_block(task))
                lines.append("")

        # --- Blocked ---
        lines.append("## Blocked")
        lines.append("")
        blocked = self.list_tasks(status="blocked")
        for task in blocked:
            lines.extend(self._format_task_block(task))
            lines.append("")

        # --- Completed Tasks ---
        lines.append("## Completed Tasks")
        lines.append("")

        # Show archive count
        archive_count = conn.execute(
            "SELECT COUNT(*) FROM archived_tasks"
        ).fetchone()[0]
        if archive_count > 0:
            lines.append(
                f"> {archive_count} completed tasks archived to "
                "[archive/completed_tasks.md](archive/completed_tasks.md)."
            )
            lines.append("")

        completed = conn.execute(
            """SELECT * FROM tasks WHERE status='completed'
               ORDER BY completed_at DESC, id DESC"""
        ).fetchall()
        for row in completed:
            task = self._row_to_task(row, conn)
            lines.append(self._format_completed_oneliner(task))
        if completed:
            lines.append("")

        # Ensure trailing newline
        result = "\n".join(lines)
        if not result.endswith("\n"):
            result += "\n"
        return result

    def _row_to_task(self, row: sqlite3.Row, conn: sqlite3.Connection) -> Task:
        """Convert a sqlite3.Row to a Task with dependencies."""
        deps = [
            r[0]
            for r in conn.execute(
                "SELECT upstream_id FROM task_dependencies WHERE downstream_id=?",
                (row["id"],),
            ).fetchall()
        ]
        return Task(
            id=row["id"],
            title=row["title"],
            status=row["status"],
            priority=row["priority"],
            complexity=row["complexity"],
            description=row["description"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            sort_order=row["sort_order"],
            depends_on=deps,
        )

    def _format_task_block(self, task: Task) -> list[str]:
        """Format a task as a full #### block for projection."""
        lines = [f"#### {task.id}: {task.title}"]
        lines.append(f"- **Priority**: {task.priority}")
        lines.append(f"- **Complexity**: {task.complexity}")
        deps_str = ", ".join(task.depends_on) if task.depends_on else "None"
        lines.append(f"- **Depends on**: {deps_str}")
        if task.description:
            # Description is stored without the `- **Description**: ` prefix
            desc_lines = task.description.splitlines()
            lines.append(f"- **Description**: {desc_lines[0]}")
            for desc_line in desc_lines[1:]:
                lines.append(desc_line)
        return lines

    def _format_completed_oneliner(self, task: Task) -> str:
        """Format a completed task as a oneliner."""
        date = task.completed_at or _today()
        summary = ""
        if task.description:
            first_line = task.description.splitlines()[0].strip()
            # Strip redundant prefix if present
            if first_line.startswith("- **Description**: "):
                first_line = first_line[len("- **Description**: "):]
            summary = first_line[:120]
        if summary:
            return f"- [x] **{date}** -- {task.id}: {task.title}. {summary}"
        return f"- [x] **{date}** -- {task.id}: {task.title}"

    # --- Projection Hash ---

    def get_projection_hash(self) -> str | None:
        """Get the stored hash of the last projected TASKS.md."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='last_projection_hash'"
        ).fetchone()
        return row[0] if row else None

    def set_projection_hash(self, hash_value: str) -> None:
        """Store the hash of the projected TASKS.md."""
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO metadata (key, value)
               VALUES ('last_projection_hash', ?)""",
            (hash_value,),
        )
        conn.commit()

    # --- Import ---

    def import_from_markdown(self, content: str) -> list[ParsedTask]:
        """Parse TASKS.md and import all tasks into the database.

        This is the ONE FINAL regex parse -- after import, all operations
        use SQL. Existing data is cleared and replaced.

        Args:
            content: Full TASKS.md file content.

        Returns:
            List of ParsedTask objects that were imported.
        """
        parsed = self._parse_tasks_md(content)
        conn = self._get_conn()

        # Clear existing data
        conn.execute("DELETE FROM task_dependencies")
        conn.execute("DELETE FROM tasks")

        # Import each parsed task
        for pt in parsed:
            conn.execute(
                """INSERT INTO tasks (id, title, status, priority, complexity,
                   description, completed_at, created_at, updated_at, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pt.id,
                    pt.title,
                    pt.status,
                    pt.priority,
                    pt.complexity,
                    pt.description,
                    pt.completed_at,
                    _now(),
                    _now(),
                    pt.sort_order,
                ),
            )

        # Import dependencies (second pass -- all tasks exist now)
        for pt in parsed:
            for dep_id in pt.depends_on:
                # Only add if upstream exists (some deps may be archived)
                if conn.execute(
                    "SELECT id FROM tasks WHERE id=?", (dep_id,)
                ).fetchone():
                    conn.execute(
                        "INSERT OR IGNORE INTO task_dependencies (upstream_id, downstream_id) VALUES (?, ?)",
                        (dep_id, pt.id),
                    )

        # Update ID counters based on imported data
        self._sync_id_counters()
        conn.commit()

        return parsed

    def _sync_id_counters(self) -> None:
        """Update next_id_counters based on max existing IDs."""
        conn = self._get_conn()
        counters: dict[str, int] = {"P0": 1, "P1": 1, "P2": 1, "P3": 1}

        for table in ["tasks", "archived_tasks"]:
            rows = conn.execute(f"SELECT id FROM {table}").fetchall()
            for row in rows:
                match = re.match(r"T-P(\d+)-(\d+)", row[0])
                if match:
                    p = f"P{match.group(1)}"
                    num = int(match.group(2))
                    if p in counters:
                        counters[p] = max(counters[p], num + 1)

        conn.execute(
            "UPDATE metadata SET value=? WHERE key='next_id_counters'",
            (json.dumps(counters),),
        )

    def _parse_tasks_md(self, content: str) -> list[ParsedTask]:
        """Parse TASKS.md into structured ParsedTask objects.

        Handles the following sections:
        - ## In Progress -> status='in_progress'
        - ## Active Tasks -> status='active'
        - ## Blocked -> status='blocked'
        - ## Completed Tasks -> status='completed'
        """
        parsed: list[ParsedTask] = []
        sort_counter = 0

        # Parse section by section
        sections = self._split_sections(content)

        for section_name, section_body in sections:
            if section_name.lower() in ("in progress",):
                status = "in_progress"
            elif section_name.lower() in ("active tasks",):
                status = "active"
            elif section_name.lower() in ("blocked",):
                status = "blocked"
            elif section_name.lower() in ("completed tasks",):
                status = "completed"
            else:
                continue

            if status == "completed":
                parsed.extend(self._parse_completed_section(section_body))
            else:
                tasks = self._parse_active_section(section_body, status)
                for t in tasks:
                    sort_counter += 100
                    t.sort_order = sort_counter
                parsed.extend(tasks)

        return parsed

    def _split_sections(self, content: str) -> list[tuple[str, str]]:
        """Split TASKS.md content into (section_name, section_body) tuples."""
        sections: list[tuple[str, str]] = []
        section_re = re.compile(r"^## (.+)$", re.MULTILINE)
        matches = list(section_re.finditer(content))

        for i, match in enumerate(matches):
            name = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            body = content[start:end].strip()
            sections.append((name, body))

        return sections

    def _parse_active_section(self, body: str, status: str) -> list[ParsedTask]:
        """Parse active/in_progress/blocked task blocks from section body."""
        tasks: list[ParsedTask] = []

        # Split on #### headers
        blocks = re.split(r"(?=^#### )", body, flags=re.MULTILINE)

        for block in blocks:
            block = block.strip()
            if not block.startswith("#### "):
                continue

            # Parse header
            header_match = re.match(
                r"####\s+(T-P\d+-\d+[a-z]?):\s*(.+)", block
            )
            if not header_match:
                continue

            task_id = header_match.group(1)
            title = header_match.group(2).strip()

            # Parse metadata fields
            priority = self._extract_field(block, "Priority") or "P2"
            complexity = self._extract_field(block, "Complexity") or "S"
            depends_raw = self._extract_field(block, "Depends on") or ""
            description_text = self._extract_description(block)

            # Parse complexity -- take first word (e.g., "S (< 1 session)" -> "S")
            complexity = complexity.split()[0] if complexity else "S"
            # Validate
            if complexity not in ("S", "M", "L"):
                complexity = "S"

            # Parse depends_on
            depends_on: list[str] = []
            if depends_raw and depends_raw.lower() != "none":
                depends_on = re.findall(r"(T-P\d+-\d+[a-z]?)", depends_raw)

            tasks.append(
                ParsedTask(
                    id=task_id,
                    title=title,
                    status=status,
                    priority=priority,
                    complexity=complexity,
                    description=description_text,
                    depends_on=depends_on,
                )
            )

        return tasks

    def _parse_completed_section(self, body: str) -> list[ParsedTask]:
        """Parse completed tasks from the Completed Tasks section."""
        tasks: list[ParsedTask] = []
        sort_counter = 0

        # Match #### [x] T-P...: blocks
        block_re = re.compile(
            r"^####\s+\[x\]\s+(T-P\d+-\d+[a-z]?):\s*(.+?)(?:\s+--\s+(\d{4}-\d{2}-\d{2}))?$",
            re.MULTILINE,
        )

        # Match oneliner: - [x] **date** -- T-P...: Title. Summary
        oneliner_re = re.compile(
            r"^- \[x\]\s+\*\*(\d{4}-\d{2}-\d{2})\*\*\s+--\s+(T-P\d+-\d+[a-z]?):\s*(.+)$",
            re.MULTILINE,
        )

        # Process blocks
        blocks = re.split(r"(?=^#### \[x\])", body, flags=re.MULTILINE)
        for block in blocks:
            block = block.strip()
            m = block_re.match(block)
            if not m:
                continue

            task_id = m.group(1)
            title = m.group(2).strip()
            completed_at = m.group(3) or _today()

            # Get description from remaining lines
            remaining = block[m.end():].strip()
            description = remaining if remaining else ""

            sort_counter += 100
            tasks.append(
                ParsedTask(
                    id=task_id,
                    title=title,
                    status="completed",
                    priority=self._priority_from_id(task_id),
                    complexity="S",
                    description=description,
                    completed_at=completed_at,
                    sort_order=sort_counter,
                )
            )

        # Process oneliners
        for m in oneliner_re.finditer(body):
            completed_at = m.group(1)
            task_id = m.group(2)
            rest = m.group(3).strip()

            # Split title from summary at first period
            if ". " in rest:
                title, summary = rest.split(". ", 1)
            else:
                title = rest
                summary = ""

            # Skip if already found as block
            if any(t.id == task_id for t in tasks):
                continue

            sort_counter += 100
            tasks.append(
                ParsedTask(
                    id=task_id,
                    title=title.strip(),
                    status="completed",
                    priority=self._priority_from_id(task_id),
                    complexity="S",
                    description=summary.strip(),
                    completed_at=completed_at,
                    sort_order=sort_counter,
                )
            )

        return tasks

    def _extract_field(self, block: str, field_name: str) -> str | None:
        """Extract a `- **FieldName**: value` field from a task block."""
        match = re.search(
            rf"^- \*\*{re.escape(field_name)}\*\*:\s*(.+)$",
            block,
            re.MULTILINE,
        )
        return match.group(1).strip() if match else None

    def _extract_description(self, block: str) -> str:
        """Extract full description + AC from a task block.

        Returns the description value (without the `- **Description**: ` prefix)
        plus any AC lines that follow. Stops at section headers (### or ##).
        """
        # Find description start
        desc_match = re.search(
            r"^- \*\*Description\*\*:\s*(.+)$",
            block,
            re.MULTILINE,
        )
        if not desc_match:
            return ""

        # Get everything from description VALUE through end of block
        first_line = desc_match.group(1).strip()
        after_desc = block[desc_match.end():]

        # Collect remaining lines, stopping at section headers (###, ##)
        remaining_lines = [first_line]
        for line in after_desc.splitlines():
            stripped = line.strip()
            # Stop at section headers (### P0 -- Must Have, etc.)
            if stripped.startswith("### ") or stripped.startswith("## "):
                break
            remaining_lines.append(line)

        result = "\n".join(remaining_lines).strip()
        return result

    def _priority_from_id(self, task_id: str) -> str:
        """Extract priority from task ID (T-P2-42 -> P2)."""
        match = re.match(r"T-(P\d+)-", task_id)
        return match.group(1) if match else "P2"

    # --- Batch Operations ---

    def batch(self, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Execute multiple commands atomically in a single transaction.

        Each command is a dict with 'cmd' key and command-specific args.
        $LAST in id/on fields is replaced with the ID of the last added task.

        Supported commands:
        - {"cmd": "add", "title": "...", "priority": "P0", ...}
        - {"cmd": "update", "id": "T-P0-42", "status": "completed", ...}
        - {"cmd": "depend", "id": "T-P0-42", "on": "T-P0-41"}
        - {"cmd": "delete", "id": "T-P0-42"}

        Returns:
            List of result dicts, one per command.
        """
        conn = self._get_conn()
        results: list[dict[str, Any]] = []
        last_id: str | None = None

        try:
            for cmd_dict in commands:
                cmd = cmd_dict.get("cmd", "")

                # Support both flat and nested-args formats:
                #   {"cmd": "add", "title": "..."}          (flat)
                #   {"cmd": "add", "args": {"title": "..."}} (nested)
                if "args" in cmd_dict and isinstance(cmd_dict["args"], dict):
                    merged = {**cmd_dict["args"], **{
                        k: v for k, v in cmd_dict.items()
                        if k not in ("cmd", "args")
                    }}
                    cmd_dict = {"cmd": cmd, **merged}

                # Replace $LAST references
                for key in ("id", "on"):
                    if cmd_dict.get(key) == "$LAST" and last_id:
                        cmd_dict[key] = last_id

                if cmd == "add":
                    title = cmd_dict.get("title", "")
                    if not title.strip():
                        raise ValueError(
                            "batch add: title is required and cannot be empty"
                        )
                    task = self.add(
                        title=title,
                        priority=cmd_dict.get("priority", "P2"),
                        complexity=cmd_dict.get("complexity", "S"),
                        description=cmd_dict.get("description", ""),
                        depends_on=cmd_dict.get("depends_on"),
                        task_id=cmd_dict.get("task_id"),
                    )
                    last_id = task.id
                    results.append({"ok": True, "id": task.id})

                elif cmd == "update":
                    task = self.update(
                        cmd_dict["id"],
                        title=cmd_dict.get("title"),
                        status=cmd_dict.get("status"),
                        priority=cmd_dict.get("priority"),
                        complexity=cmd_dict.get("complexity"),
                        description=cmd_dict.get("description"),
                    )
                    results.append({"ok": task is not None, "id": cmd_dict["id"]})

                elif cmd == "depend":
                    ok = self.add_dependency(cmd_dict["id"], cmd_dict["on"])
                    results.append({"ok": ok})

                elif cmd == "delete":
                    ok = self.delete(cmd_dict["id"])
                    results.append({"ok": ok})

                else:
                    results.append({"ok": False, "error": f"unknown command: {cmd}"})

            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise RuntimeError(f"Batch failed: {exc}") from exc

        return results

    # --- Import Verification ---

    def verify_import(self, original_content: str) -> list[str]:
        """Verify import by comparing original parse vs DB re-projection parse.

        Args:
            original_content: Original TASKS.md content.

        Returns:
            List of difference strings. Empty list means verification passed.
        """
        original_parsed = self._parse_tasks_md(original_content)
        projection = self.project()
        reparsed = self._parse_tasks_md(projection)

        differences: list[str] = []

        # Build lookup by ID
        orig_by_id = {t.id: t for t in original_parsed}
        repr_by_id = {t.id: t for t in reparsed}

        # Check all original tasks exist in re-parse
        for task_id, orig in orig_by_id.items():
            if task_id not in repr_by_id:
                differences.append(f"MISSING: {task_id} not in re-projected output")
                continue

            repr_task = repr_by_id[task_id]

            if orig.title != repr_task.title:
                differences.append(
                    f"TITLE DIFF {task_id}: {orig.title!r} vs {repr_task.title!r}"
                )
            if orig.status != repr_task.status:
                differences.append(
                    f"STATUS DIFF {task_id}: {orig.status} vs {repr_task.status}"
                )
            if orig.priority != repr_task.priority:
                differences.append(
                    f"PRIORITY DIFF {task_id}: {orig.priority} vs {repr_task.priority}"
                )

        # Check for unexpected tasks in re-parse
        for task_id in repr_by_id:
            if task_id not in orig_by_id:
                differences.append(f"EXTRA: {task_id} in re-projected but not original")

        return differences
