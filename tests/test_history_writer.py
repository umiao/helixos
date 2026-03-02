"""Tests for HistoryWriter -- DB-first execution logs and review history.

Tests cover:
- ExecutionLogRow and ReviewHistoryRow table creation
- write_log / get_logs / count_logs
- write_review / get_reviews / count_reviews
- write_review_decision
- 2KB truncation
- Batch writes
- Pagination (offset/limit)
- Level filtering
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.history_writer import MAX_TEXT_LENGTH, HistoryWriter, _truncate
from src.models import LLMReview

# ---------------------------------------------------------------------------
# Truncation tests
# ---------------------------------------------------------------------------


class TestTruncate:
    """Tests for the _truncate helper."""

    def test_short_text_unchanged(self):
        """Short text passes through unchanged."""
        assert _truncate("hello") == "hello"

    def test_exact_limit_unchanged(self):
        """Text at exactly the limit passes through."""
        text = "x" * MAX_TEXT_LENGTH
        assert _truncate(text) == text

    def test_over_limit_truncated(self):
        """Text over the limit is truncated with marker."""
        text = "x" * (MAX_TEXT_LENGTH + 100)
        result = _truncate(text)
        assert len(result) == MAX_TEXT_LENGTH
        assert result.endswith("...[truncated]")

    def test_custom_limit(self):
        """Custom max_len is respected."""
        result = _truncate("abcdefghij" * 10, max_len=20)
        assert len(result) == 20
        assert result.endswith("...[truncated]")


# ---------------------------------------------------------------------------
# ExecutionLog tests
# ---------------------------------------------------------------------------


class TestExecutionLogs:
    """Tests for execution log persistence."""

    async def test_write_and_read_single_log(self, session_factory):
        """Write a single log entry and read it back."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "Build started", level="info", source="scheduler")

        logs = await hw.get_logs("task-1")
        assert len(logs) == 1
        assert logs[0]["task_id"] == "task-1"
        assert logs[0]["message"] == "Build started"
        assert logs[0]["level"] == "info"
        assert logs[0]["source"] == "scheduler"
        assert logs[0]["timestamp"] is not None

    async def test_write_multiple_logs(self, session_factory):
        """Multiple log entries for the same task are returned in order."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "Step 1")
        await hw.write_log("task-1", "Step 2")
        await hw.write_log("task-1", "Step 3")

        logs = await hw.get_logs("task-1")
        assert len(logs) == 3
        assert [entry["message"] for entry in logs] == ["Step 1", "Step 2", "Step 3"]

    async def test_logs_isolated_by_task(self, session_factory):
        """Logs for different tasks are isolated."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "Log A")
        await hw.write_log("task-2", "Log B")

        assert len(await hw.get_logs("task-1")) == 1
        assert len(await hw.get_logs("task-2")) == 1
        assert (await hw.get_logs("task-1"))[0]["message"] == "Log A"

    async def test_count_logs(self, session_factory):
        """count_logs returns the correct total."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "a")
        await hw.write_log("task-1", "b")
        await hw.write_log("task-2", "c")

        assert await hw.count_logs("task-1") == 2
        assert await hw.count_logs("task-2") == 1
        assert await hw.count_logs("nonexistent") == 0

    async def test_pagination(self, session_factory):
        """offset and limit work correctly."""
        hw = HistoryWriter(session_factory)
        for i in range(10):
            await hw.write_log("task-1", f"Line {i}")

        page1 = await hw.get_logs("task-1", limit=3, offset=0)
        assert len(page1) == 3
        assert page1[0]["message"] == "Line 0"

        page2 = await hw.get_logs("task-1", limit=3, offset=3)
        assert len(page2) == 3
        assert page2[0]["message"] == "Line 3"

        last_page = await hw.get_logs("task-1", limit=3, offset=9)
        assert len(last_page) == 1

    async def test_level_filter(self, session_factory):
        """Level filter returns only matching entries."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "Info msg", level="info")
        await hw.write_log("task-1", "Warn msg", level="warn")
        await hw.write_log("task-1", "Error msg", level="error")

        info_logs = await hw.get_logs("task-1", level="info")
        assert len(info_logs) == 1
        assert info_logs[0]["level"] == "info"

        error_logs = await hw.get_logs("task-1", level="error")
        assert len(error_logs) == 1

    async def test_truncation_on_write(self, session_factory):
        """Messages exceeding 2KB are truncated on write."""
        hw = HistoryWriter(session_factory)
        long_msg = "x" * (MAX_TEXT_LENGTH + 500)
        await hw.write_log("task-1", long_msg)

        logs = await hw.get_logs("task-1")
        assert len(logs[0]["message"]) == MAX_TEXT_LENGTH
        assert logs[0]["message"].endswith("...[truncated]")

    async def test_batch_write(self, session_factory):
        """write_logs_batch writes multiple entries in one transaction."""
        hw = HistoryWriter(session_factory)
        messages = [f"Batch line {i}" for i in range(5)]
        await hw.write_logs_batch("task-1", messages)

        logs = await hw.get_logs("task-1")
        assert len(logs) == 5

    async def test_batch_write_empty(self, session_factory):
        """write_logs_batch with empty list is a no-op."""
        hw = HistoryWriter(session_factory)
        await hw.write_logs_batch("task-1", [])
        assert await hw.count_logs("task-1") == 0


# ---------------------------------------------------------------------------
# ReviewHistory tests
# ---------------------------------------------------------------------------


def _make_review(
    model: str = "claude-sonnet-4-5",
    focus: str = "feasibility",
    verdict: str = "approve",
    summary: str = "Looks good",
) -> LLMReview:
    """Create a test LLMReview."""
    return LLMReview(
        model=model,
        focus=focus,
        verdict=verdict,
        summary=summary,
        suggestions=["Consider edge cases"],
        timestamp=datetime.now(UTC),
    )


class TestReviewHistory:
    """Tests for review history persistence."""

    async def test_write_and_read_review(self, session_factory):
        """Write a review entry and read it back."""
        hw = HistoryWriter(session_factory)
        review = _make_review()
        await hw.write_review("task-1", round_number=1, review=review)

        reviews = await hw.get_reviews("task-1")
        assert len(reviews) == 1
        r = reviews[0]
        assert r["task_id"] == "task-1"
        assert r["round_number"] == 1
        assert r["reviewer_model"] == "claude-sonnet-4-5"
        assert r["reviewer_focus"] == "feasibility"
        assert r["verdict"] == "approve"
        assert r["summary"] == "Looks good"
        assert r["suggestions"] == ["Consider edge cases"]
        assert r["consensus_score"] is None
        assert r["human_decision"] is None

    async def test_multiple_rounds(self, session_factory):
        """Multiple review rounds are stored and returned in order."""
        hw = HistoryWriter(session_factory)
        await hw.write_review(
            "task-1", 1, _make_review(focus="feasibility", verdict="approve"),
        )
        await hw.write_review(
            "task-1", 2, _make_review(focus="adversarial", verdict="reject"),
            consensus_score=0.65,
        )

        reviews = await hw.get_reviews("task-1")
        assert len(reviews) == 2
        assert reviews[0]["round_number"] == 1
        assert reviews[1]["round_number"] == 2
        assert reviews[1]["consensus_score"] == 0.65

    async def test_count_reviews(self, session_factory):
        """count_reviews returns the correct total."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())
        await hw.write_review("task-1", 2, _make_review())

        assert await hw.count_reviews("task-1") == 2
        assert await hw.count_reviews("nonexistent") == 0

    async def test_review_pagination(self, session_factory):
        """offset and limit work correctly for reviews."""
        hw = HistoryWriter(session_factory)
        for i in range(5):
            await hw.write_review("task-1", i + 1, _make_review())

        page1 = await hw.get_reviews("task-1", limit=2, offset=0)
        assert len(page1) == 2
        assert page1[0]["round_number"] == 1

        page2 = await hw.get_reviews("task-1", limit=2, offset=2)
        assert len(page2) == 2
        assert page2[0]["round_number"] == 3

    async def test_reviews_isolated_by_task(self, session_factory):
        """Reviews for different tasks are isolated."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())
        await hw.write_review("task-2", 1, _make_review())

        assert await hw.count_reviews("task-1") == 1
        assert await hw.count_reviews("task-2") == 1

    async def test_summary_truncation(self, session_factory):
        """Review summaries exceeding 2KB are truncated."""
        hw = HistoryWriter(session_factory)
        long_summary = "y" * (MAX_TEXT_LENGTH + 500)
        review = _make_review(summary=long_summary)
        await hw.write_review("task-1", 1, review)

        reviews = await hw.get_reviews("task-1")
        assert len(reviews[0]["summary"]) == MAX_TEXT_LENGTH
        assert reviews[0]["summary"].endswith("...[truncated]")

    async def test_write_review_decision(self, session_factory):
        """write_review_decision updates the latest review entry."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())
        await hw.write_review("task-1", 2, _make_review())

        await hw.write_review_decision("task-1", "approve")

        reviews = await hw.get_reviews("task-1")
        # Only the latest (round 2) should have the decision
        assert reviews[0]["human_decision"] is None
        assert reviews[1]["human_decision"] == "approve"

    async def test_write_review_decision_no_reviews(self, session_factory):
        """write_review_decision is a no-op if no reviews exist."""
        hw = HistoryWriter(session_factory)
        # Should not raise
        await hw.write_review_decision("nonexistent", "reject")

    async def test_consensus_score_on_final_round(self, session_factory):
        """consensus_score is stored only on the final round."""
        hw = HistoryWriter(session_factory)
        await hw.write_review(
            "task-1", 1, _make_review(), consensus_score=None,
        )
        await hw.write_review(
            "task-1", 2, _make_review(), consensus_score=0.92,
        )

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["consensus_score"] is None
        assert reviews[1]["consensus_score"] == pytest.approx(0.92)
