"""Shared pytest fixtures."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import Base
from src.sync.task_store_bridge import TaskStoreBridge


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip cli_integration tests unless explicitly selected via -m."""
    marker_expr = config.getoption("-m", default="")
    if "cli_integration" in str(marker_expr):
        return  # user explicitly requested cli_integration tests
    skip_cli = pytest.mark.skip(reason="cli_integration tests require -m cli_integration")
    for item in items:
        if "cli_integration" in item.keywords:
            item.add_marker(skip_cli)


# ---------------------------------------------------------------------------
# task_store.py loader (loaded once per test session)
# ---------------------------------------------------------------------------


def _load_project_task_store() -> Any:
    """Load task_store.py from the project's own .claude/hooks/ via importlib."""
    store_path = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "task_store.py"
    if not store_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_test_task_store", str(store_path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PROJECT_TASK_STORE = _load_project_task_store()


def setup_tasks_db(repo: Path, tasks: list[dict[str, Any]]) -> None:
    """Create .claude/tasks.db in a test repo with given tasks.

    Uses dependency injection to avoid needing .claude/hooks/task_store.py
    at the test repo path.

    Args:
        repo: Root of the test repo.
        tasks: List of dicts with keys: title, priority, task_id, description, etc.
    """
    if _PROJECT_TASK_STORE is None:
        pytest.skip("task_store.py not found in project .claude/hooks/")

    db_dir = repo / ".claude"
    db_dir.mkdir(parents=True, exist_ok=True)

    bridge = TaskStoreBridge(repo, _module=_PROJECT_TASK_STORE)
    # Ensure DB file is created even with zero tasks
    store = bridge._open_store()
    store.close()
    for t in tasks:
        bridge.add_task(**t)


# ---------------------------------------------------------------------------
# Monkeypatch fixture for TaskStoreBridge loader fallback
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_task_store_loader():
    """Allow TaskStoreBridge to load without .claude/hooks/task_store.py in test repos.

    Opt-in: add ``patch_task_store_loader`` to your test's fixture list.
    """
    if _PROJECT_TASK_STORE is None:
        yield
        return

    from src.sync import task_store_bridge

    original = task_store_bridge._load_task_store_module

    def _fallback_loader(repo_path: Path) -> Any:
        try:
            return original(repo_path)
        except FileNotFoundError:
            return _PROJECT_TASK_STORE

    task_store_bridge._load_task_store_module = _fallback_loader
    yield
    task_store_bridge._load_task_store_module = original


# ---------------------------------------------------------------------------
# Standard DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def async_engine():
    """Create an in-memory SQLite async engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(async_engine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the in-memory test engine."""
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
