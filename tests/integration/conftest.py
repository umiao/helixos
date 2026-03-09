"""Shared fixtures for integration tests.

Provides a complete service stack (DB, TaskManager, Scheduler, EventBus,
etc.) using in-memory SQLite and temp directories, with a mock executor
so no real subprocess or API calls are made.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import (
    GitConfig,
    OrchestratorConfig,
    OrchestratorSettings,
    ProjectConfig,
    ReviewerConfig,
    ReviewPipelineConfig,
    StagedSafetyCheck,
)
from src.db import Base
from src.env_loader import EnvLoader
from src.events import EventBus
from src.executors.base import BaseExecutor, ExecutorResult
from src.models import ExecutorType, Project, Task
from src.task_manager import TaskManager


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark all tests under tests/integration/ as integration."""
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)

# ---------------------------------------------------------------------------
# Mock executor
# ---------------------------------------------------------------------------


class MockExecutor(BaseExecutor):
    """Configurable mock executor for integration tests.

    Pass a list of ``ExecutorResult`` objects; each call to ``execute()``
    returns the next result in sequence (last result repeats forever).
    """

    def __init__(
        self,
        results: list[ExecutorResult] | None = None,
        delay: float = 0.0,
    ) -> None:
        """Initialize with a list of results to return in order.

        Args:
            results: Results to return. Defaults to a single success.
            delay: Seconds to sleep during execute (simulates work).
        """
        self._results = results or [
            ExecutorResult(
                success=True, exit_code=0, duration_seconds=0.1,
            ),
        ]
        self._call_count = 0
        self._cancelled = False
        self._delay = delay

    async def execute(
        self,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
        on_stream_event: Callable[[dict], None] | None = None,
        review_feedback: str | None = None,
    ) -> ExecutorResult:
        """Return the next configured result."""
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        idx = min(self._call_count, len(self._results) - 1)
        result = self._results[idx]
        self._call_count += 1
        on_log(f"Mock execution attempt {self._call_count}")
        return result

    async def cancel(self) -> None:
        """Mark as cancelled."""
        self._cancelled = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_engine():
    """Create an in-memory SQLite async engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(
    async_engine,
) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory."""
    return async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
def task_manager(session_factory: async_sessionmaker[AsyncSession]) -> TaskManager:
    """Create a TaskManager backed by in-memory DB."""
    return TaskManager(session_factory)


@pytest.fixture
def event_bus() -> EventBus:
    """Create a fresh EventBus."""
    return EventBus()


@pytest.fixture
def temp_project_repo(tmp_path: Path) -> Path:
    """Create a temp git repo with an initial commit.

    Returns the repo directory path.
    """
    repo = tmp_path / "test_project"
    repo.mkdir()
    subprocess.run(
        ["git", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.email", "test@helixos.test"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    # Create initial commit (empty repos cause issues)
    readme = repo / "README.md"
    readme.write_text("# Test Project\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return repo


@pytest.fixture
def sample_tasks_md() -> str:
    """A minimal TASKS.md with two active tasks."""
    return (
        "# Task Backlog\n"
        "\n"
        "## Active Tasks\n"
        "\n"
        "#### T-P0-1: Implement feature A\n"
        "- Build the widget\n"
        "\n"
        "#### T-P0-2: Implement feature B\n"
        "- Depends on A\n"
        "\n"
        "## Completed Tasks\n"
    )


@pytest.fixture
def make_config(
    tmp_path: Path,
    temp_project_repo: Path,
) -> Callable[..., OrchestratorConfig]:
    """Factory fixture to create OrchestratorConfig with temp paths.

    Returns a callable ``make_config(**overrides)`` that builds a config
    with sensible defaults for integration testing.
    """

    def _make(
        *,
        global_concurrency_limit: int = 3,
        per_project_concurrency: int = 1,
        auto_commit: bool = True,
        max_files: int = 50,
        projects: dict[str, ProjectConfig] | None = None,
        reviewers: list[ReviewerConfig] | None = None,
    ) -> OrchestratorConfig:
        env_path = tmp_path / ".env"
        env_path.write_text(
            "API_KEY=test-key\n", encoding="utf-8",
        )
        db_path = tmp_path / "state.db"

        if projects is None:
            projects = {
                "proj_a": ProjectConfig(
                    name="Project A",
                    repo_path=temp_project_repo,
                    executor_type=ExecutorType.CODE,
                    max_concurrency=per_project_concurrency,
                ),
            }

        if reviewers is None:
            reviewers = []

        return OrchestratorConfig(
            orchestrator=OrchestratorSettings(
                global_concurrency_limit=global_concurrency_limit,
                per_project_concurrency=per_project_concurrency,
                unified_env_path=env_path,
                state_db_path=db_path,
                session_timeout_minutes=5,
            ),
            projects=projects,
            git=GitConfig(
                auto_commit=auto_commit,
                commit_message_template="[helixos] {project}: {task_id} {task_title}",
                staged_safety_check=StagedSafetyCheck(max_files=max_files),
            ),
            review_pipeline=ReviewPipelineConfig(reviewers=reviewers),
        )

    return _make


@pytest.fixture
def env_loader(tmp_path: Path) -> EnvLoader:
    """Create an EnvLoader with a temp .env file."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "API_KEY=test-key\n", encoding="utf-8",
    )
    return EnvLoader(env_path)
