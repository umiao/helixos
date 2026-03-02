"""Git operations for HelixOS orchestrator.

Provides ``GitOps`` with auto-commit after successful task execution and
a staged-file safety check per PRD Section 8.  Also exposes a helper to
check whether a repository working tree is clean.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.config import GitConfig
from src.events import EventBus
from src.models import Project, Task

logger = logging.getLogger(__name__)


async def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git subprocess and return (returncode, stdout, stderr).

    All output is decoded as UTF-8.

    Args:
        args: Arguments to pass after ``git``.
        cwd: Working directory for the git command.

    Returns:
        Tuple of (return code, stdout text, stderr text).
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    assert proc.returncode is not None
    return (
        proc.returncode,
        stdout_bytes.decode("utf-8"),
        stderr_bytes.decode("utf-8"),
    )


class GitOps:
    """Git helper for auto-committing after successful task execution."""

    @staticmethod
    async def auto_commit(
        project: Project,
        task: Task,
        config: GitConfig,
        event_bus: EventBus,
    ) -> bool:
        """Stage all changes and commit if within safety limits.

        Flow:
        1. If auto_commit is disabled in config, return True (no-op).
        2. ``git add -A`` to stage everything.
        3. ``git diff --cached --numstat`` to count staged files.
        4. If nothing staged, return True silently.
        5. If file count exceeds ``config.staged_safety_check.max_files``,
           unstage with ``git reset HEAD``, emit alert, return False.
        6. Format commit message from template and commit.

        Args:
            project: The project whose repo to commit in.
            task: The successfully completed task.
            config: Git configuration (template, safety limits).
            event_bus: Event bus for emitting alerts.

        Returns:
            True if commit succeeded or was a no-op, False if safety
            check failed.
        """
        if not config.auto_commit:
            return True

        repo_path = project.repo_path
        if repo_path is None:
            logger.warning(
                "Project %s has no repo_path; skipping auto-commit",
                project.id,
            )
            return True

        # Stage all changes
        rc, _out, err = await _run_git(["add", "-A"], cwd=repo_path)
        if rc != 0:
            logger.error("git add -A failed: %s", err.strip())
            return False

        # Count staged files via numstat
        rc, out, err = await _run_git(
            ["diff", "--cached", "--numstat"], cwd=repo_path,
        )
        if rc != 0:
            logger.error("git diff --cached --numstat failed: %s", err.strip())
            return False

        staged_lines = [line for line in out.strip().splitlines() if line]
        file_count = len(staged_lines)

        if file_count == 0:
            return True  # Nothing to commit

        # Safety check: too many files staged
        max_files = config.staged_safety_check.max_files
        if file_count > max_files:
            await _run_git(["reset", "HEAD"], cwd=repo_path)
            event_bus.emit(
                "alert",
                task.id,
                {
                    "error": (
                        f"Staged {file_count} files exceeds limit {max_files}"
                    ),
                },
            )
            logger.warning(
                "Auto-commit aborted for task %s: %d files > limit %d",
                task.id,
                file_count,
                max_files,
            )
            return False

        # Format commit message
        message = config.commit_message_template.format(
            project=project.name,
            task_id=task.local_task_id,
            task_title=task.title,
        )

        # Commit
        rc, _out, err = await _run_git(
            ["commit", "-m", message], cwd=repo_path,
        )
        if rc != 0:
            logger.error("git commit failed: %s", err.strip())
            return False

        logger.info(
            "Auto-committed %d file(s) for task %s: %s",
            file_count,
            task.id,
            message,
        )
        return True

    @staticmethod
    async def check_repo_clean(repo_path: Path) -> bool:
        """Check if the git working tree is clean (no uncommitted changes).

        Args:
            repo_path: Path to the git repository.

        Returns:
            True if the working tree is clean, False otherwise.
        """
        rc, out, _err = await _run_git(
            ["status", "--porcelain"], cwd=repo_path,
        )
        if rc != 0:
            return False
        return out.strip() == ""
