"""Tests for GitOps auto-commit and repo-clean checking."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from src.config import GitConfig, StagedSafetyCheck
from src.events import EventBus
from src.git_ops import GitOps
from src.models import ExecutorType, Project, Task, TaskStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo(path: Path) -> None:
    """Create a bare git repo with required user config at *path*."""
    subprocess.run(
        ["git", "init"],
        cwd=str(path),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    # Initial commit so HEAD exists
    (path / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(path),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(path),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )


def _make_project(repo_path: Path) -> Project:
    """Return a minimal Project pointing at *repo_path*."""
    return Project(
        id="proj-1",
        name="TestProject",
        repo_path=repo_path,
        executor_type=ExecutorType.CODE,
    )


def _make_task() -> Task:
    """Return a minimal Task for testing."""
    return Task(
        id="proj-1/T-001",
        project_id="proj-1",
        local_task_id="T-001",
        title="Add widget",
        status=TaskStatus.DONE,
        executor_type=ExecutorType.CODE,
    )


def _default_git_config(**overrides: object) -> GitConfig:
    """Return a GitConfig with optional overrides."""
    kwargs: dict[str, object] = {}
    if "max_files" in overrides:
        kwargs["staged_safety_check"] = StagedSafetyCheck(
            max_files=int(overrides.pop("max_files")),  # type: ignore[arg-type]
        )
    for k, v in overrides.items():
        kwargs[k] = v
    return GitConfig(**kwargs)  # type: ignore[arg-type]


def _git_log(repo_path: Path) -> str:
    """Return the git log --oneline output."""
    result = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(repo_path),
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_commit_success(tmp_path: Path) -> None:
    """Create a file, auto_commit -> commit appears in git log."""
    _init_repo(tmp_path)
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")

    project = _make_project(tmp_path)
    task = _make_task()
    config = _default_git_config()
    bus = EventBus()

    result = await GitOps.auto_commit(project, task, config, bus)
    assert result is True

    log_output = _git_log(tmp_path)
    assert "T-001" in log_output
    assert "Add widget" in log_output


@pytest.mark.asyncio
async def test_auto_commit_safety_abort(tmp_path: Path) -> None:
    """Staging too many files -> returns False, files unstaged, alert emitted."""
    _init_repo(tmp_path)

    # Create 6 files with max_files=5
    for i in range(6):
        (tmp_path / f"file_{i}.txt").write_text(
            f"content {i}", encoding="utf-8",
        )

    project = _make_project(tmp_path)
    task = _make_task()
    config = _default_git_config(max_files=5)
    bus = EventBus()

    # Subscribe to capture events
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=100)
    bus._subscribers.append(queue)  # noqa: SLF001

    result = await GitOps.auto_commit(project, task, config, bus)
    assert result is False

    # Files should have been unstaged
    status_result = subprocess.run(
        ["git", "diff", "--cached", "--numstat"],
        cwd=str(tmp_path),
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert status_result.stdout.strip() == ""

    # Alert event should have been emitted
    event = queue.get_nowait()
    assert event.type == "alert"  # type: ignore[union-attr]
    assert "exceeds limit" in str(event.data)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_auto_commit_no_changes(tmp_path: Path) -> None:
    """Clean repo with no changes -> returns True, no new commit."""
    _init_repo(tmp_path)

    project = _make_project(tmp_path)
    task = _make_task()
    config = _default_git_config()
    bus = EventBus()

    log_before = _git_log(tmp_path)
    result = await GitOps.auto_commit(project, task, config, bus)
    log_after = _git_log(tmp_path)

    assert result is True
    assert log_before == log_after  # No new commit


@pytest.mark.asyncio
async def test_auto_commit_message_format(tmp_path: Path) -> None:
    """Verify commit message matches the configured template."""
    _init_repo(tmp_path)
    (tmp_path / "code.py").write_text("x = 1\n", encoding="utf-8")

    project = _make_project(tmp_path)
    task = _make_task()
    template = "auto: {project} -- {task_id} -- {task_title}"
    config = _default_git_config(commit_message_template=template)
    bus = EventBus()

    await GitOps.auto_commit(project, task, config, bus)

    result = subprocess.run(
        ["git", "log", "-1", "--pretty=%s"],
        cwd=str(tmp_path),
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    msg = result.stdout.strip()
    assert msg == "auto: TestProject -- T-001 -- Add widget"


@pytest.mark.asyncio
async def test_auto_commit_disabled(tmp_path: Path) -> None:
    """auto_commit=False -> returns True without running git."""
    _init_repo(tmp_path)
    (tmp_path / "should_not_commit.py").write_text(
        "x = 1\n", encoding="utf-8",
    )

    project = _make_project(tmp_path)
    task = _make_task()
    config = _default_git_config(auto_commit=False)
    bus = EventBus()

    result = await GitOps.auto_commit(project, task, config, bus)
    assert result is True

    # File should still be untracked (not committed)
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(tmp_path),
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert "should_not_commit.py" in status.stdout


@pytest.mark.asyncio
async def test_check_repo_clean_dirty(tmp_path: Path) -> None:
    """Dirty repo -> check_repo_clean returns False."""
    _init_repo(tmp_path)
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    assert await GitOps.check_repo_clean(tmp_path) is False


@pytest.mark.asyncio
async def test_check_repo_clean_clean(tmp_path: Path) -> None:
    """Clean repo -> check_repo_clean returns True."""
    _init_repo(tmp_path)

    assert await GitOps.check_repo_clean(tmp_path) is True


@pytest.mark.asyncio
async def test_auto_commit_no_repo_path() -> None:
    """Project with no repo_path -> returns True (skipped)."""
    project = Project(
        id="proj-1",
        name="NoRepo",
        repo_path=None,
        executor_type=ExecutorType.CODE,
    )
    task = _make_task()
    config = _default_git_config()
    bus = EventBus()

    result = await GitOps.auto_commit(project, task, config, bus)
    assert result is True
