"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import Base


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
