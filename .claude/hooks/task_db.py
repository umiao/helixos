#!/usr/bin/env python3
"""CLI wrapper for the SQLite task store.

Usage:
    python .claude/hooks/task_db.py add --title "Fix bug" --priority P0
    python .claude/hooks/task_db.py update T-P0-42 --status completed
    python .claude/hooks/task_db.py list [--status active] [--priority P0]
    python .claude/hooks/task_db.py get T-P0-42
    python .claude/hooks/task_db.py depend T-P0-42 --on T-P0-41
    python .claude/hooks/task_db.py archive
    python .claude/hooks/task_db.py project
    python .claude/hooks/task_db.py import [--verify]
    python .claude/hooks/task_db.py reorder T-P0-42 --after T-P0-41
    python .claude/hooks/task_db.py batch --commands '[...]'
"""

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_store import TaskStore  # noqa: E402


def _find_project_root() -> Path:
    """Find the project root by looking for CLAUDE.md."""
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent.parent,
    ]
    for candidate in candidates:
        if (candidate / "CLAUDE.md").exists():
            return candidate
    return Path.cwd()


def _get_store(root: Path) -> TaskStore:
    """Get a TaskStore for the project."""
    db_path = root / ".claude" / "tasks.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return TaskStore(str(db_path))


def _write_projection(root: Path, store: TaskStore) -> None:
    """Write TASKS.md from DB state, handling read-only attribute."""
    tasks_file = root / "TASKS.md"
    content = store.project()

    # Remove read-only if set
    if tasks_file.exists():
        _remove_readonly(tasks_file)

    # Atomic write via temp file
    fd, tmp_path = tempfile.mkstemp(
        dir=str(tasks_file.parent), suffix=".tmp"
    )
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp_path, str(tasks_file))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    # Store projection hash
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    store.set_projection_hash(h)

    # Restore read-only
    _set_readonly(tasks_file)


def _set_readonly(path: Path) -> None:
    """Set file to read-only (cross-platform)."""
    if sys.platform == "win32":
        os.system(f'attrib +R "{path}"')
    else:
        current = os.stat(path).st_mode
        os.chmod(path, current & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _remove_readonly(path: Path) -> None:
    """Remove read-only attribute (cross-platform)."""
    if sys.platform == "win32":
        os.system(f'attrib -R "{path}"')
    else:
        current = os.stat(path).st_mode
        os.chmod(path, current | stat.S_IWUSR)


def cmd_add(args: argparse.Namespace) -> None:
    """Handle 'add' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        depends = args.depends_on.split(",") if args.depends_on else None
        task = store.add(
            title=args.title,
            priority=args.priority,
            complexity=args.complexity,
            description=args.description or "",
            depends_on=depends,
        )
        _write_projection(root, store)
        print(json.dumps({"ok": True, "id": task.id, "title": task.title}))
    finally:
        store.close()


def cmd_update(args: argparse.Namespace) -> None:
    """Handle 'update' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        task = store.update(
            args.task_id,
            title=args.title,
            status=args.status,
            priority=args.priority,
            complexity=args.complexity,
            description=args.description,
        )
        if task:
            _write_projection(root, store)
            print(json.dumps({"ok": True, "id": task.id, "status": task.status}))
        else:
            print(json.dumps({"ok": False, "error": f"Task {args.task_id} not found"}))
            sys.exit(1)
    finally:
        store.close()


def cmd_list(args: argparse.Namespace) -> None:
    """Handle 'list' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        tasks = store.list_tasks(status=args.status, priority=args.priority)
        output = []
        for t in tasks:
            deps_str = ", ".join(t.depends_on) if t.depends_on else "None"
            output.append({
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "complexity": t.complexity,
                "depends_on": deps_str,
            })
        print(json.dumps(output, indent=2))
    finally:
        store.close()


def cmd_get(args: argparse.Namespace) -> None:
    """Handle 'get' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        task = store.get(args.task_id)
        if task:
            deps_str = ", ".join(task.depends_on) if task.depends_on else "None"
            print(json.dumps({
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "priority": task.priority,
                "complexity": task.complexity,
                "description": task.description,
                "depends_on": deps_str,
                "completed_at": task.completed_at,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "sort_order": task.sort_order,
            }, indent=2))
        else:
            print(json.dumps({"ok": False, "error": f"Task {args.task_id} not found"}))
            sys.exit(1)
    finally:
        store.close()


def cmd_depend(args: argparse.Namespace) -> None:
    """Handle 'depend' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        ok = store.add_dependency(args.task_id, args.on)
        if ok:
            _write_projection(root, store)
            print(json.dumps({"ok": True, "dependency": f"{args.task_id} depends on {args.on}"}))
        else:
            print(json.dumps({"ok": False, "error": "One or both tasks not found"}))
            sys.exit(1)
    finally:
        store.close()


def cmd_archive(args: argparse.Namespace) -> None:
    """Handle 'archive' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        count = store.archive()
        if count > 0:
            _write_projection(root, store)
        print(json.dumps({"ok": True, "archived": count}))
    finally:
        store.close()


def cmd_project(args: argparse.Namespace) -> None:
    """Handle 'project' command -- regenerate TASKS.md."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        _write_projection(root, store)
        print(json.dumps({"ok": True, "message": "TASKS.md regenerated"}))
    finally:
        store.close()


def cmd_import(args: argparse.Namespace) -> None:
    """Handle 'import' command -- import from TASKS.md."""
    root = _find_project_root()
    tasks_file = root / "TASKS.md"

    if not tasks_file.exists():
        print(json.dumps({"ok": False, "error": "TASKS.md not found"}))
        sys.exit(1)

    content = tasks_file.read_text(encoding="utf-8")
    store = _get_store(root)
    try:
        parsed = store.import_from_markdown(content)
        print(
            f"Imported {len(parsed)} tasks from TASKS.md",
            file=sys.stderr,
        )

        if args.verify:
            diffs = store.verify_import(content)
            if diffs:
                print("Import verification FAILED:", file=sys.stderr)
                for d in diffs:
                    print(f"  {d}", file=sys.stderr)
                print(json.dumps({"ok": False, "errors": diffs}))
                sys.exit(1)
            else:
                print("Import verification passed", file=sys.stderr)

        _write_projection(root, store)
        print(json.dumps({"ok": True, "imported": len(parsed)}))
    finally:
        store.close()


def cmd_reorder(args: argparse.Namespace) -> None:
    """Handle 'reorder' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        ok = store.reorder(args.task_id, after=args.after)
        if ok:
            _write_projection(root, store)
            print(json.dumps({"ok": True, "id": args.task_id}))
        else:
            print(json.dumps({"ok": False, "error": "Reorder failed (task not found or priority mismatch)"}))
            sys.exit(1)
    finally:
        store.close()


def cmd_delete(args: argparse.Namespace) -> None:
    """Handle 'delete' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        ok = store.delete(args.task_id)
        if ok:
            _write_projection(root, store)
            print(json.dumps({"ok": True, "id": args.task_id, "deleted": True}))
        else:
            print(json.dumps({"ok": False, "error": f"Task {args.task_id} not found"}))
            sys.exit(1)
    finally:
        store.close()


def cmd_has_unblocked(args: argparse.Namespace) -> None:
    """Handle 'has-unblocked' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        result = store.has_unblocked_tasks()
        if result:
            print("yes")
        else:
            print("no")
            sys.exit(1)
    finally:
        store.close()


def cmd_batch(args: argparse.Namespace) -> None:
    """Handle 'batch' command."""
    root = _find_project_root()
    store = _get_store(root)
    try:
        commands = json.loads(args.commands)
        results = store.batch(commands)
        _write_projection(root, store)
        print(json.dumps({"ok": True, "results": results}))
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"Invalid JSON: {exc}"}))
        sys.exit(1)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
    finally:
        store.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="SQLite-backed task management CLI",
        prog="task_db.py",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = subparsers.add_parser("add", help="Add a new task")
    p_add.add_argument("--title", required=True, help="Task title")
    p_add.add_argument("--priority", default="P2", choices=["P0", "P1", "P2", "P3"])
    p_add.add_argument("--complexity", default="S", choices=["S", "M", "L"])
    p_add.add_argument("--description", default="", help="Description/AC block")
    p_add.add_argument("--depends-on", default=None, help="Comma-separated dependency IDs")
    p_add.set_defaults(func=cmd_add)

    # update
    p_update = subparsers.add_parser("update", help="Update a task")
    p_update.add_argument("task_id", help="Task ID (e.g., T-P0-42)")
    p_update.add_argument("--title", default=None)
    p_update.add_argument("--status", default=None, choices=["active", "in_progress", "completed", "blocked"])
    p_update.add_argument("--priority", default=None, choices=["P0", "P1", "P2", "P3"])
    p_update.add_argument("--complexity", default=None, choices=["S", "M", "L"])
    p_update.add_argument("--description", default=None)
    p_update.set_defaults(func=cmd_update)

    # list
    p_list = subparsers.add_parser("list", help="List tasks")
    p_list.add_argument("--status", default=None, choices=["active", "in_progress", "completed", "blocked"])
    p_list.add_argument("--priority", default=None, choices=["P0", "P1", "P2", "P3"])
    p_list.set_defaults(func=cmd_list)

    # get
    p_get = subparsers.add_parser("get", help="Get task details")
    p_get.add_argument("task_id", help="Task ID")
    p_get.set_defaults(func=cmd_get)

    # depend
    p_dep = subparsers.add_parser("depend", help="Add dependency")
    p_dep.add_argument("task_id", help="Downstream task ID")
    p_dep.add_argument("--on", required=True, help="Upstream task ID")
    p_dep.set_defaults(func=cmd_depend)

    # archive
    p_archive = subparsers.add_parser("archive", help="Archive old completed tasks")
    p_archive.set_defaults(func=cmd_archive)

    # project
    p_project = subparsers.add_parser("project", help="Regenerate TASKS.md from DB")
    p_project.set_defaults(func=cmd_project)

    # import
    p_import = subparsers.add_parser("import", help="Import tasks from TASKS.md")
    p_import.add_argument("--verify", action="store_true", help="Verify import round-trip")
    p_import.set_defaults(func=cmd_import)

    # reorder
    p_reorder = subparsers.add_parser("reorder", help="Reorder a task")
    p_reorder.add_argument("task_id", help="Task ID to move")
    p_reorder.add_argument("--after", default=None, help="Place after this task ID (None = beginning)")
    p_reorder.set_defaults(func=cmd_reorder)

    # delete
    p_delete = subparsers.add_parser("delete", help="Delete a task")
    p_delete.add_argument("task_id", help="Task ID to delete")
    p_delete.set_defaults(func=cmd_delete)

    # has-unblocked
    p_has_unblocked = subparsers.add_parser(
        "has-unblocked", help="Check if project has runnable (unblocked) active tasks"
    )
    p_has_unblocked.set_defaults(func=cmd_has_unblocked)

    # batch
    p_batch = subparsers.add_parser("batch", help="Execute multiple commands atomically")
    p_batch.add_argument("--commands", required=True, help="JSON array of command objects")
    p_batch.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
