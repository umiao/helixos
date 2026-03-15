#!/usr/bin/env python3
"""Multi-project orchestrator for autonomous Claude Code sessions.

Reads orchestrator_config.yaml, checks each project for unblocked tasks,
and runs claude sessions serially -- one project at a time.

Usage:
    python scripts/orchestrator.py [--max-sessions N] [--dry-run]
"""

import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

LOCKFILE = Path.home() / ".helixos" / "orchestrator.lock"


def load_config() -> dict:
    """Load orchestrator_config.yaml from the helixos repo root."""
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "orchestrator_config.yaml"
    if not config_path.exists():
        print(f"[orchestrator] Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(repo_path: str) -> Path:
    """Resolve a repo path, expanding ~ and making absolute."""
    return Path(os.path.expanduser(repo_path)).resolve()


def project_has_unblocked_tasks(repo_dir: Path) -> bool:
    """Check if a project has unblocked active tasks via task_db.py."""
    task_db = repo_dir / ".claude" / "hooks" / "task_db.py"
    if not task_db.exists():
        # Fallback: check tasks.db existence
        return (repo_dir / ".claude" / "tasks.db").exists()

    try:
        result = subprocess.run(
            [sys.executable, str(task_db), "has-unblocked"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"[orchestrator]   Warning: has-unblocked check failed: {exc}",
              file=sys.stderr)
        return False


def check_all_done(repo_dir: Path) -> bool:
    """Check if project's session_state.json says all_done."""
    state_file = repo_dir / ".claude" / "session_state.json"
    if not state_file.exists():
        return False
    try:
        with open(state_file, encoding="utf-8") as f:
            state = json.load(f)
        return state.get("all_done", False)
    except (json.JSONDecodeError, OSError):
        return False


def acquire_lock() -> bool:
    """Acquire PID lockfile. Returns True if acquired."""
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    if LOCKFILE.exists():
        try:
            old_pid = int(LOCKFILE.read_text(encoding="utf-8").strip())
            # Check if process is still running
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x100000, False, old_pid)  # SYNCHRONIZE
                if handle:
                    kernel32.CloseHandle(handle)
                    print(f"[orchestrator] Another instance running (PID {old_pid}). Exiting.",
                          file=sys.stderr)
                    return False
            else:
                os.kill(old_pid, 0)
                print(f"[orchestrator] Another instance running (PID {old_pid}). Exiting.",
                      file=sys.stderr)
                return False
        except (ValueError, OSError, ProcessLookupError):
            pass  # Stale lockfile, safe to overwrite

    LOCKFILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    """Release PID lockfile."""
    with contextlib.suppress(OSError):
        LOCKFILE.unlink(missing_ok=True)


def run_session(repo_dir: Path) -> int:
    """Run a single autonomous claude session in the given project directory.

    Returns the process exit code.
    """
    prompt = (
        "Autonomous mode. Read TASKS.md, pick ONE highest-priority unblocked task, "
        "and complete it. After completing the task: "
        "1) run tests, 2) update PROGRESS.md, update tasks via task_db.py, "
        "3) git commit with message format '[T-XX-N] description', "
        "4) update .claude/session_state.json, then stop. "
        "If no unblocked tasks remain, set all_done=true in session_state.json and stop."
    )
    cmd = [
        "claude",
        "-p", prompt,
        "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep,Task",
        "--max-turns", "200",
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_dir),
            timeout=60 * 120,  # 2 hour max per session
            encoding="utf-8",
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        print(f"[orchestrator]   Session timed out in {repo_dir.name}")
        return 1
    except FileNotFoundError:
        print("[orchestrator] 'claude' CLI not found. Is it installed and on PATH?",
              file=sys.stderr)
        return 1


def get_ordered_projects(config: dict) -> list[tuple[str, dict]]:
    """Return projects ordered: primary first, then alphabetical by key."""
    projects = config.get("projects", {})
    primary = []
    others = []
    for key, proj in projects.items():
        if proj.get("is_primary"):
            primary.append((key, proj))
        else:
            others.append((key, proj))
    others.sort(key=lambda x: x[0])
    return primary + others


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Multi-project autonomous orchestrator")
    parser.add_argument("--max-sessions", type=int, default=5,
                        help="Max total sessions across all projects (default: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would run without executing")
    args = parser.parse_args()

    config = load_config()

    if not args.dry_run and not acquire_lock():
        sys.exit(1)

    try:
        projects = get_ordered_projects(config)
        sessions_used = 0
        failures: dict[str, int] = {}  # key -> consecutive failure count
        max_failures_per_project = 2

        print(f"[orchestrator] Starting multi-project run (max {args.max_sessions} sessions)")
        print(f"[orchestrator] Projects: {', '.join(k for k, _ in projects)}")
        print()

        # Keep iterating until budget exhausted or all projects done/skipped
        while sessions_used < args.max_sessions:
            made_progress = False

            for key, proj in projects:
                if sessions_used >= args.max_sessions:
                    break

                if failures.get(key, 0) >= max_failures_per_project:
                    continue

                repo_dir = resolve_path(proj["repo_path"])
                name = proj.get("name", key)

                # Skip if repo doesn't exist
                if not repo_dir.exists():
                    print(f"[orchestrator] [{name}] Skipping: repo not found at {repo_dir}")
                    continue

                # Skip if no tasks.db
                if not (repo_dir / ".claude" / "tasks.db").exists():
                    print(f"[orchestrator] [{name}] Skipping: no .claude/tasks.db")
                    continue

                # Skip if all_done
                if check_all_done(repo_dir):
                    print(f"[orchestrator] [{name}] Skipping: all_done=true")
                    continue

                # Skip if no unblocked tasks
                if not project_has_unblocked_tasks(repo_dir):
                    print(f"[orchestrator] [{name}] Skipping: no unblocked tasks")
                    continue

                sessions_used += 1
                print(f"--- Session {sessions_used}/{args.max_sessions}: "
                      f"{name} ({repo_dir}) ---")

                if args.dry_run:
                    print(f"[orchestrator] [DRY-RUN] Would run claude session in {repo_dir}")
                    made_progress = True
                    continue

                exit_code = run_session(repo_dir)

                if exit_code == 0:
                    failures[key] = 0
                    made_progress = True
                    print(f"[orchestrator] [{name}] Session completed successfully")
                else:
                    failures[key] = failures.get(key, 0) + 1
                    print(f"[orchestrator] [{name}] Session failed "
                          f"({failures[key]}/{max_failures_per_project})")
                    if failures[key] >= max_failures_per_project:
                        print(f"[orchestrator] [{name}] Max failures reached, skipping project")
                    made_progress = True  # We did try something

                print()

            if not made_progress:
                print("[orchestrator] No projects have runnable work. Stopping.")
                break

        print(f"[orchestrator] Finished after {sessions_used} session(s)")
    finally:
        if not args.dry_run:
            release_lock()


if __name__ == "__main__":
    main()
