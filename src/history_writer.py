"""HistoryWriter -- DB-first persistence for execution logs and review history.

Provides ``write_log()`` for execution log entries and ``write_review()``
for review history entries.  Both enforce a 2KB cap on text fields.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import ExecutionLogRow, ReviewHistoryRow, get_session
from src.models import LLMReview

logger = logging.getLogger(__name__)

# Maximum size for text fields (message, summary) in bytes/chars
MAX_TEXT_LENGTH = 2048


def _truncate(text: str, max_len: int = MAX_TEXT_LENGTH) -> str:
    """Truncate text to max_len characters, appending '...[truncated]' if needed."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 14] + "...[truncated]"


class HistoryWriter:
    """Writes execution logs and review history directly to the database.

    All writes are "DB-first" -- the log/review is persisted to SQLite
    before being emitted to SSE or stored in-memory.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize with an async session factory from db.py."""
        self._sf = session_factory

    # ------------------------------------------------------------------
    # Execution logs
    # ------------------------------------------------------------------

    async def write_log(
        self,
        task_id: str,
        message: str,
        level: str = "info",
        source: str = "executor",
    ) -> None:
        """Persist a single execution log entry.

        Args:
            task_id: The task this log belongs to.
            message: Log message (truncated to 2KB).
            level: Log level (info, warn, error).
            source: Source of the log (executor, scheduler, system).
        """
        now = datetime.now(UTC).isoformat()
        async with get_session(self._sf) as session:
            row = ExecutionLogRow(
                task_id=task_id,
                timestamp=now,
                level=level,
                message=_truncate(message),
                source=source,
            )
            session.add(row)

    async def write_logs_batch(
        self,
        task_id: str,
        messages: list[str],
        level: str = "info",
        source: str = "executor",
    ) -> None:
        """Persist multiple execution log entries in a single transaction.

        Args:
            task_id: The task these logs belong to.
            messages: Log messages (each truncated to 2KB).
            level: Log level for all entries.
            source: Source for all entries.
        """
        if not messages:
            return
        now = datetime.now(UTC).isoformat()
        async with get_session(self._sf) as session:
            for msg in messages:
                row = ExecutionLogRow(
                    task_id=task_id,
                    timestamp=now,
                    level=level,
                    message=_truncate(msg),
                    source=source,
                )
                session.add(row)

    async def get_logs(
        self,
        task_id: str,
        limit: int = 100,
        offset: int = 0,
        level: str | None = None,
    ) -> list[dict]:
        """Retrieve execution logs for a task.

        Args:
            task_id: The task to fetch logs for.
            limit: Maximum number of entries to return.
            offset: Number of entries to skip.
            level: Optional filter by log level.

        Returns:
            List of log entry dicts with id, timestamp, level, message, source.
        """
        async with get_session(self._sf) as session:
            stmt = (
                select(ExecutionLogRow)
                .where(ExecutionLogRow.task_id == task_id)
            )
            if level is not None:
                stmt = stmt.where(ExecutionLogRow.level == level)
            stmt = (
                stmt
                .order_by(ExecutionLogRow.id)
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "task_id": r.task_id,
                    "timestamp": r.timestamp,
                    "level": r.level,
                    "message": r.message,
                    "source": r.source,
                }
                for r in rows
            ]

    async def count_logs(self, task_id: str) -> int:
        """Count total log entries for a task."""
        async with get_session(self._sf) as session:
            stmt = select(ExecutionLogRow).where(
                ExecutionLogRow.task_id == task_id,
            )
            result = await session.execute(stmt)
            return len(result.scalars().all())

    # ------------------------------------------------------------------
    # Review history
    # ------------------------------------------------------------------

    async def write_review(
        self,
        task_id: str,
        round_number: int,
        review: LLMReview,
        consensus_score: float | None = None,
        human_decision: str | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Persist a single review history entry.

        Args:
            task_id: The task this review belongs to.
            round_number: The review round (1-based).
            review: The LLMReview with verdict, summary, suggestions.
            consensus_score: Overall consensus score (set on final round).
            human_decision: Human decision if applicable.
            cost_usd: Approximate cost in USD for this reviewer call.
        """
        async with get_session(self._sf) as session:
            row = ReviewHistoryRow(
                task_id=task_id,
                round_number=round_number,
                reviewer_model=review.model,
                reviewer_focus=review.focus,
                verdict=review.verdict,
                summary=_truncate(review.summary),
                suggestions_json=json.dumps(review.suggestions),
                consensus_score=consensus_score,
                human_decision=human_decision,
                raw_response=getattr(review, "raw_response", ""),
                cost_usd=cost_usd,
                timestamp=review.timestamp.isoformat(),
            )
            session.add(row)

    async def write_review_decision(
        self,
        task_id: str,
        decision: str,
    ) -> None:
        """Update the latest review entry for a task with a human decision.

        Args:
            task_id: The task to update.
            decision: The human decision (approve/reject).
        """
        async with get_session(self._sf) as session:
            stmt = (
                select(ReviewHistoryRow)
                .where(ReviewHistoryRow.task_id == task_id)
                .order_by(ReviewHistoryRow.id.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is not None:
                row.human_decision = decision

    async def get_reviews(
        self,
        task_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Retrieve review history for a task.

        Args:
            task_id: The task to fetch reviews for.
            limit: Maximum number of entries to return.
            offset: Number of entries to skip.

        Returns:
            List of review entry dicts.
        """
        async with get_session(self._sf) as session:
            stmt = (
                select(ReviewHistoryRow)
                .where(ReviewHistoryRow.task_id == task_id)
                .order_by(ReviewHistoryRow.id)
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "task_id": r.task_id,
                    "round_number": r.round_number,
                    "reviewer_model": r.reviewer_model,
                    "reviewer_focus": r.reviewer_focus,
                    "verdict": r.verdict,
                    "summary": r.summary,
                    "suggestions": json.loads(r.suggestions_json),
                    "consensus_score": r.consensus_score,
                    "human_decision": r.human_decision,
                    "raw_response": getattr(r, "raw_response", ""),
                    "cost_usd": getattr(r, "cost_usd", None),
                    "timestamp": r.timestamp,
                }
                for r in rows
            ]

    async def count_reviews(self, task_id: str) -> int:
        """Count total review entries for a task."""
        async with get_session(self._sf) as session:
            stmt = select(ReviewHistoryRow).where(
                ReviewHistoryRow.task_id == task_id,
            )
            result = await session.execute(stmt)
            return len(result.scalars().all())

    async def has_approved_review(self, task_id: str) -> bool:
        """Check if any review entry for *task_id* has an approved verdict.

        An "approved" review is one where verdict == "approve" OR
        human_decision == "approve".  This is used by the scheduler's
        Layer 2 review gate to ensure only reviewed tasks execute.

        Args:
            task_id: The task to check.

        Returns:
            True if at least one approved review record exists.
        """
        async with get_session(self._sf) as session:
            stmt = (
                select(ReviewHistoryRow)
                .where(ReviewHistoryRow.task_id == task_id)
                .where(
                    (ReviewHistoryRow.verdict == "approve")
                    | (ReviewHistoryRow.human_decision == "approve")
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None
