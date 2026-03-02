"""Tests for ProjectSettingsStore (execution_paused persistence)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import Base
from src.project_settings import ProjectSettingsStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_factory():
    """In-memory async engine + session factory for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def store(session_factory) -> ProjectSettingsStore:
    """ProjectSettingsStore backed by the in-memory DB."""
    return ProjectSettingsStore(session_factory)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIsPaused:
    """Tests for is_paused()."""

    async def test_default_not_paused(self, store: ProjectSettingsStore):
        """Projects default to not paused when no row exists."""
        assert await store.is_paused("proj-a") is False

    async def test_paused_after_set(self, store: ProjectSettingsStore):
        """is_paused returns True after set_paused(paused=True)."""
        await store.set_paused("proj-a", paused=True)
        assert await store.is_paused("proj-a") is True

    async def test_resumed_after_set(self, store: ProjectSettingsStore):
        """is_paused returns False after set_paused(paused=False)."""
        await store.set_paused("proj-a", paused=True)
        await store.set_paused("proj-a", paused=False)
        assert await store.is_paused("proj-a") is False


class TestSetPaused:
    """Tests for set_paused()."""

    async def test_creates_row(self, store: ProjectSettingsStore):
        """set_paused creates a row if none exists (upsert)."""
        await store.set_paused("proj-new", paused=True)
        assert await store.is_paused("proj-new") is True

    async def test_updates_existing_row(self, store: ProjectSettingsStore):
        """set_paused updates an existing row."""
        await store.set_paused("proj-a", paused=True)
        await store.set_paused("proj-a", paused=False)
        assert await store.is_paused("proj-a") is False

    async def test_idempotent_pause(self, store: ProjectSettingsStore):
        """Pausing twice does not error."""
        await store.set_paused("proj-a", paused=True)
        await store.set_paused("proj-a", paused=True)
        assert await store.is_paused("proj-a") is True

    async def test_idempotent_resume(self, store: ProjectSettingsStore):
        """Resuming when already not paused does not error."""
        await store.set_paused("proj-a", paused=False)
        assert await store.is_paused("proj-a") is False


class TestGetAllPaused:
    """Tests for get_all_paused()."""

    async def test_empty_when_none_paused(self, store: ProjectSettingsStore):
        """Returns empty set when no projects are paused."""
        result = await store.get_all_paused()
        assert result == set()

    async def test_returns_paused_projects(self, store: ProjectSettingsStore):
        """Returns the set of paused project IDs."""
        await store.set_paused("proj-a", paused=True)
        await store.set_paused("proj-b", paused=True)
        await store.set_paused("proj-c", paused=False)
        result = await store.get_all_paused()
        assert result == {"proj-a", "proj-b"}

    async def test_excludes_resumed_projects(self, store: ProjectSettingsStore):
        """Resumed projects are excluded from the result."""
        await store.set_paused("proj-a", paused=True)
        await store.set_paused("proj-a", paused=False)
        result = await store.get_all_paused()
        assert result == set()

    async def test_multiple_projects(self, store: ProjectSettingsStore):
        """Multiple projects can be tracked independently."""
        await store.set_paused("proj-a", paused=True)
        await store.set_paused("proj-b", paused=False)
        await store.set_paused("proj-c", paused=True)
        result = await store.get_all_paused()
        assert result == {"proj-a", "proj-c"}
