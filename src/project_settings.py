"""Per-project runtime settings backed by SQLite.

Provides async helpers to get and set the ``execution_paused`` and
``review_gate_enabled`` flags for each project.  Both flags persist
across server restarts.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import ProjectSettingsRow, get_session

logger = logging.getLogger(__name__)


class ProjectSettingsStore:
    """Thin async wrapper around the project_settings DB table."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize with a session factory.

        Args:
            session_factory: SQLAlchemy async session factory.
        """
        self._session_factory = session_factory

    async def is_paused(self, project_id: str) -> bool:
        """Return True if execution is paused for *project_id*.

        Returns False if no row exists (default: not paused).

        Args:
            project_id: The project to check.

        Returns:
            Whether execution is paused.
        """
        async with get_session(self._session_factory) as session:
            row = await session.get(ProjectSettingsRow, project_id)
            if row is None:
                return False
            return row.execution_paused

    async def set_paused(self, project_id: str, *, paused: bool) -> None:
        """Set the execution_paused flag for *project_id*.

        Creates the row if it does not already exist (upsert).

        Args:
            project_id: The project to update.
            paused: The desired pause state.
        """
        async with get_session(self._session_factory) as session:
            row = await session.get(ProjectSettingsRow, project_id)
            if row is None:
                row = ProjectSettingsRow(
                    project_id=project_id,
                    execution_paused=paused,
                )
                session.add(row)
            else:
                row.execution_paused = paused

    async def get_all_paused(self) -> set[str]:
        """Return the set of project IDs that are currently paused.

        Returns:
            Set of paused project IDs.
        """
        async with get_session(self._session_factory) as session:
            stmt = select(ProjectSettingsRow.project_id).where(
                ProjectSettingsRow.execution_paused.is_(True),
            )
            result = await session.execute(stmt)
            return {row[0] for row in result.all()}

    # ------------------------------------------------------------------
    # Review gate
    # ------------------------------------------------------------------

    async def is_review_gate_enabled(self, project_id: str) -> bool:
        """Return True if the review gate is enabled for *project_id*.

        Returns True if no row exists (default: gate enabled).

        Args:
            project_id: The project to check.

        Returns:
            Whether the review gate is enabled.
        """
        async with get_session(self._session_factory) as session:
            row = await session.get(ProjectSettingsRow, project_id)
            if row is None:
                return True
            return row.review_gate_enabled

    async def set_review_gate(
        self, project_id: str, *, enabled: bool,
    ) -> None:
        """Set the review_gate_enabled flag for *project_id*.

        Creates the row if it does not already exist (upsert).

        Args:
            project_id: The project to update.
            enabled: The desired review gate state.
        """
        async with get_session(self._session_factory) as session:
            row = await session.get(ProjectSettingsRow, project_id)
            if row is None:
                row = ProjectSettingsRow(
                    project_id=project_id,
                    review_gate_enabled=enabled,
                )
                session.add(row)
            else:
                row.review_gate_enabled = enabled

    async def get_all_review_gate_disabled(self) -> set[str]:
        """Return project IDs where the review gate is disabled.

        Returns:
            Set of project IDs with review gate disabled.
        """
        async with get_session(self._session_factory) as session:
            stmt = select(ProjectSettingsRow.project_id).where(
                ProjectSettingsRow.review_gate_enabled.is_(False),
            )
            result = await session.execute(stmt)
            return {row[0] for row in result.all()}
