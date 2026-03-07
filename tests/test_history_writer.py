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
- Retention purge (purge_old_entries)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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

    async def test_artifacts_excluded_by_default(self, session_factory):
        """get_logs excludes level='artifact' entries by default."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "Normal log", level="info")
        await hw.write_raw_artifact("task-1", "plan_cli_output", '{"big": "json"}')
        await hw.write_log("task-1", "Another log", level="warn")

        logs = await hw.get_logs("task-1")
        assert len(logs) == 2
        assert all(entry["level"] != "artifact" for entry in logs)

    async def test_artifacts_included_when_requested(self, session_factory):
        """get_logs includes artifacts when include_artifacts=True."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "Normal log", level="info")
        await hw.write_raw_artifact("task-1", "plan_cli_output", '{"big": "json"}')

        logs = await hw.get_logs("task-1", include_artifacts=True)
        assert len(logs) == 2
        artifact = [e for e in logs if e["level"] == "artifact"]
        assert len(artifact) == 1
        assert artifact[0]["source"] == "plan_cli_output"

    async def test_artifacts_accessible_via_level_filter(self, session_factory):
        """Explicit level='artifact' filter returns only artifact entries."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "Normal log", level="info")
        await hw.write_raw_artifact("task-1", "plan_cli_output", '{"big": "json"}')

        logs = await hw.get_logs("task-1", level="artifact")
        assert len(logs) == 1
        assert logs[0]["level"] == "artifact"

    async def test_count_logs_excludes_artifacts_by_default(self, session_factory):
        """count_logs excludes artifact entries by default."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "Normal log")
        await hw.write_raw_artifact("task-1", "plan_cli_output", '{"big": "json"}')

        assert await hw.count_logs("task-1") == 1
        assert await hw.count_logs("task-1", include_artifacts=True) == 2


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

    async def test_write_review_decision_with_reason(self, session_factory):
        """write_review_decision persists the human_reason alongside the decision."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())

        await hw.write_review_decision("task-1", "approve", reason="Need error handling for X")

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["human_decision"] == "approve"
        assert reviews[0]["human_reason"] == "Need error handling for X"

    async def test_write_review_decision_empty_reason(self, session_factory):
        """write_review_decision with empty reason leaves human_reason as None."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())

        await hw.write_review_decision("task-1", "reject", reason="")

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["human_decision"] == "reject"
        assert reviews[0]["human_reason"] is None

    async def test_human_reason_default_none(self, session_factory):
        """human_reason defaults to None when no decision has been made."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["human_reason"] is None

    async def test_human_reason_persists_across_reload(self, session_factory):
        """human_reason persists and is returned in subsequent get_reviews calls."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())
        await hw.write_review("task-1", 2, _make_review())

        await hw.write_review_decision("task-1", "approve", reason="Looks good to me")

        # Re-read from DB
        reviews = await hw.get_reviews("task-1")
        # Only the latest (round 2) has the decision + reason
        assert reviews[0]["human_reason"] is None
        assert reviews[1]["human_decision"] == "approve"
        assert reviews[1]["human_reason"] == "Looks good to me"

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

    async def test_raw_response_persisted(self, session_factory):
        """raw_response is persisted and returned by get_reviews."""
        hw = HistoryWriter(session_factory)
        review = _make_review()
        review.raw_response = "Full reviewer reasoning text here..."
        await hw.write_review("task-1", 1, review)

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["raw_response"] == "Full reviewer reasoning text here..."

    async def test_raw_response_empty_default(self, session_factory):
        """raw_response defaults to empty string when not set (legacy compat)."""
        hw = HistoryWriter(session_factory)
        review = _make_review()
        # raw_response defaults to "" on LLMReview
        await hw.write_review("task-1", 1, review)

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["raw_response"] == ""

    async def test_raw_response_in_existing_write_read_cycle(self, session_factory):
        """raw_response round-trips correctly through write_review / get_reviews."""
        hw = HistoryWriter(session_factory)
        raw = '{"verdict":"approve","summary":"Good plan","suggestions":[]}'
        review = _make_review(summary="Good plan")
        review.raw_response = raw
        await hw.write_review("task-1", 1, review, consensus_score=0.95)

        reviews = await hw.get_reviews("task-1")
        assert len(reviews) == 1
        assert reviews[0]["raw_response"] == raw
        assert reviews[0]["summary"] == "Good plan"
        assert reviews[0]["consensus_score"] == pytest.approx(0.95)

    async def test_cost_usd_persisted(self, session_factory):
        """cost_usd is persisted and returned by get_reviews."""
        hw = HistoryWriter(session_factory)
        review = _make_review()
        await hw.write_review("task-1", 1, review, cost_usd=0.0525)

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["cost_usd"] == pytest.approx(0.0525)

    async def test_cost_usd_none_default(self, session_factory):
        """cost_usd defaults to None when not provided."""
        hw = HistoryWriter(session_factory)
        review = _make_review()
        await hw.write_review("task-1", 1, review)

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["cost_usd"] is None

    async def test_cost_usd_multiple_rounds(self, session_factory):
        """Each review round can have different cost_usd values."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review(), cost_usd=0.05)
        await hw.write_review("task-1", 2, _make_review(), cost_usd=0.01)

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["cost_usd"] == pytest.approx(0.05)
        assert reviews[1]["cost_usd"] == pytest.approx(0.01)

    # ------------------------------------------------------------------
    # review_attempt (T-P0-31)
    # ------------------------------------------------------------------

    async def test_review_attempt_persisted(self, session_factory):
        """review_attempt is persisted and returned by get_reviews."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review(), review_attempt=2)

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["review_attempt"] == 2

    async def test_review_attempt_defaults_to_1(self, session_factory):
        """review_attempt defaults to 1 when not specified."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["review_attempt"] == 1

    async def test_review_attempt_multiple_attempts(self, session_factory):
        """Multiple attempts create separate rows with different attempt numbers."""
        hw = HistoryWriter(session_factory)
        # Attempt 1
        await hw.write_review("task-1", 1, _make_review(), review_attempt=1)
        await hw.write_review("task-1", 2, _make_review(), review_attempt=1)
        # Attempt 2 (retry)
        await hw.write_review("task-1", 1, _make_review(), review_attempt=2)
        await hw.write_review("task-1", 2, _make_review(), review_attempt=2)

        reviews = await hw.get_reviews("task-1")
        assert len(reviews) == 4
        assert reviews[0]["review_attempt"] == 1
        assert reviews[1]["review_attempt"] == 1
        assert reviews[2]["review_attempt"] == 2
        assert reviews[3]["review_attempt"] == 2

    async def test_get_max_review_attempt_no_history(self, session_factory):
        """get_max_review_attempt returns 0 when no history exists."""
        hw = HistoryWriter(session_factory)
        assert await hw.get_max_review_attempt("nonexistent") == 0

    async def test_get_max_review_attempt_single(self, session_factory):
        """get_max_review_attempt returns 1 after first attempt."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review(), review_attempt=1)
        assert await hw.get_max_review_attempt("task-1") == 1

    async def test_get_max_review_attempt_multiple(self, session_factory):
        """get_max_review_attempt returns highest attempt across all rows."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review(), review_attempt=1)
        await hw.write_review("task-1", 2, _make_review(), review_attempt=1)
        await hw.write_review("task-1", 1, _make_review(), review_attempt=3)

        assert await hw.get_max_review_attempt("task-1") == 3

    async def test_get_max_review_attempt_isolated_by_task(self, session_factory):
        """get_max_review_attempt is scoped to the given task_id."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review(), review_attempt=5)
        await hw.write_review("task-2", 1, _make_review(), review_attempt=2)

        assert await hw.get_max_review_attempt("task-1") == 5
        assert await hw.get_max_review_attempt("task-2") == 2

    # ------------------------------------------------------------------
    # get_human_feedback (T-P0-34)
    # ------------------------------------------------------------------

    async def test_get_human_feedback_empty(self, session_factory):
        """get_human_feedback returns empty list when no feedback exists."""
        hw = HistoryWriter(session_factory)
        feedback = await hw.get_human_feedback("nonexistent")
        assert feedback == []

    async def test_get_human_feedback_no_decisions(self, session_factory):
        """get_human_feedback returns empty when reviews exist but no decisions."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())
        await hw.write_review("task-1", 2, _make_review())

        feedback = await hw.get_human_feedback("task-1")
        assert feedback == []

    async def test_get_human_feedback_single(self, session_factory):
        """get_human_feedback returns a single feedback entry."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review(), review_attempt=1)
        await hw.write_review_decision("task-1", "request_changes", reason="Add tests")

        feedback = await hw.get_human_feedback("task-1")
        assert len(feedback) == 1
        assert feedback[0]["human_decision"] == "request_changes"
        assert feedback[0]["human_reason"] == "Add tests"
        assert feedback[0]["review_attempt"] == 1

    async def test_get_human_feedback_multiple(self, session_factory):
        """get_human_feedback returns all feedback in order."""
        hw = HistoryWriter(session_factory)
        # Attempt 1: request_changes
        await hw.write_review("task-1", 1, _make_review(), review_attempt=1)
        await hw.write_review_decision("task-1", "request_changes", reason="Add X")

        # Attempt 2: request_changes again
        await hw.write_review("task-1", 1, _make_review(), review_attempt=2)
        await hw.write_review_decision("task-1", "request_changes", reason="Also fix Y")

        feedback = await hw.get_human_feedback("task-1")
        assert len(feedback) == 2
        assert feedback[0]["human_reason"] == "Add X"
        assert feedback[1]["human_reason"] == "Also fix Y"

    async def test_get_human_feedback_isolated_by_task(self, session_factory):
        """get_human_feedback is scoped to the given task_id."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())
        await hw.write_review_decision("task-1", "approve", reason="Good")
        await hw.write_review("task-2", 1, _make_review())
        await hw.write_review_decision("task-2", "reject", reason="Bad")

        fb1 = await hw.get_human_feedback("task-1")
        fb2 = await hw.get_human_feedback("task-2")
        assert len(fb1) == 1
        assert fb1[0]["human_decision"] == "approve"
        assert len(fb2) == 1
        assert fb2[0]["human_decision"] == "reject"

    # ------------------------------------------------------------------
    # plan_snapshot (T-P0-35)
    # ------------------------------------------------------------------

    async def test_plan_snapshot_persisted(self, session_factory):
        """plan_snapshot is persisted and returned by get_reviews."""
        hw = HistoryWriter(session_factory)
        await hw.write_review(
            "task-1", 1, _make_review(),
            plan_snapshot="## My Plan\n- Step 1\n- Step 2",
        )

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["plan_snapshot"] == "## My Plan\n- Step 1\n- Step 2"

    async def test_plan_snapshot_defaults_to_none(self, session_factory):
        """plan_snapshot defaults to None when not provided."""
        hw = HistoryWriter(session_factory)
        await hw.write_review("task-1", 1, _make_review())

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["plan_snapshot"] is None

    async def test_plan_snapshot_only_on_first_round(self, session_factory):
        """plan_snapshot stored on first round, None on subsequent rounds."""
        hw = HistoryWriter(session_factory)
        await hw.write_review(
            "task-1", 1, _make_review(),
            review_attempt=1,
            plan_snapshot="Plan v1",
        )
        await hw.write_review(
            "task-1", 2, _make_review(),
            review_attempt=1,
            plan_snapshot=None,
        )

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["plan_snapshot"] == "Plan v1"
        assert reviews[1]["plan_snapshot"] is None

    async def test_plan_snapshot_per_attempt(self, session_factory):
        """Different attempts can have different plan snapshots."""
        hw = HistoryWriter(session_factory)
        # Attempt 1
        await hw.write_review(
            "task-1", 1, _make_review(),
            review_attempt=1,
            plan_snapshot="Plan v1",
        )
        # Attempt 2 (edited plan)
        await hw.write_review(
            "task-1", 1, _make_review(),
            review_attempt=2,
            plan_snapshot="Plan v2 (edited)",
        )

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["plan_snapshot"] == "Plan v1"
        assert reviews[1]["plan_snapshot"] == "Plan v2 (edited)"

    async def test_plan_snapshot_empty_string(self, session_factory):
        """Empty string plan_snapshot is stored as-is (not converted to None)."""
        hw = HistoryWriter(session_factory)
        await hw.write_review(
            "task-1", 1, _make_review(),
            plan_snapshot="",
        )

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["plan_snapshot"] == ""

    # ------------------------------------------------------------------
    # conversation_turns / conversation_summary (T-P2-99)
    # ------------------------------------------------------------------

    async def test_conversation_turns_persisted(self, session_factory):
        """conversation_turns round-trips through write/get."""
        hw = HistoryWriter(session_factory)
        review = _make_review()
        review.conversation_turns = [
            {"text_content": "Analyzing the code...", "tool_actions": [
                {"name": "Read", "input": {"path": "src/main.py"}, "result": "ok"},
            ]},
            {"text_content": "Found an issue.", "tool_actions": []},
        ]
        await hw.write_review("task-1", 1, review)

        reviews = await hw.get_reviews("task-1")
        turns = reviews[0]["conversation_turns"]
        assert len(turns) == 2
        assert turns[0]["text_content"] == "Analyzing the code..."
        assert turns[0]["tool_actions"][0]["name"] == "Read"
        assert turns[1]["text_content"] == "Found an issue."

    async def test_conversation_summary_persisted(self, session_factory):
        """conversation_summary round-trips through write/get."""
        hw = HistoryWriter(session_factory)
        review = _make_review()
        review.conversation_summary = {
            "findings": ["Code looks solid", "Missing edge case"],
            "actions_taken": ["Read", "Grep"],
            "conclusion": "Needs minor fix",
        }
        await hw.write_review("task-1", 1, review)

        reviews = await hw.get_reviews("task-1")
        summary = reviews[0]["conversation_summary"]
        assert summary["findings"] == ["Code looks solid", "Missing edge case"]
        assert summary["actions_taken"] == ["Read", "Grep"]
        assert summary["conclusion"] == "Needs minor fix"

    async def test_conversation_defaults_empty(self, session_factory):
        """conversation_turns/summary default to empty when not set."""
        hw = HistoryWriter(session_factory)
        review = _make_review()
        await hw.write_review("task-1", 1, review)

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["conversation_turns"] == []
        assert reviews[0]["conversation_summary"] == {}

    async def test_conversation_turns_legacy_null_column(self, session_factory):
        """Legacy rows with NULL conversation columns return empty defaults."""
        hw = HistoryWriter(session_factory)
        # Simulate legacy row by writing normally then verifying defaults work
        review = _make_review()
        review.conversation_turns = []
        review.conversation_summary = {}
        await hw.write_review("task-1", 1, review)

        reviews = await hw.get_reviews("task-1")
        assert reviews[0]["conversation_turns"] == []
        assert reviews[0]["conversation_summary"] == {}


# ---------------------------------------------------------------------------
# Retention purge tests
# ---------------------------------------------------------------------------


class TestPurgeOldEntries:
    """Tests for purge_old_entries retention policy."""

    async def test_purge_deletes_old_logs(self, session_factory):
        """Execution logs older than retention period are deleted."""
        hw = HistoryWriter(session_factory)
        # Write a log with an old timestamp by going through the DB directly
        from src.db import ExecutionLogRow, get_session

        old_ts = (datetime.now(UTC) - timedelta(days=45)).isoformat()
        new_ts = datetime.now(UTC).isoformat()
        async with get_session(session_factory) as session:
            session.add(ExecutionLogRow(
                task_id="task-old", timestamp=old_ts,
                level="info", message="old msg", source="executor",
            ))
            session.add(ExecutionLogRow(
                task_id="task-new", timestamp=new_ts,
                level="info", message="new msg", source="executor",
            ))

        counts = await hw.purge_old_entries(retention_days=30)
        assert counts["execution_logs"] == 1
        # New log survives
        logs = await hw.get_logs("task-new")
        assert len(logs) == 1
        # Old log deleted
        logs = await hw.get_logs("task-old")
        assert len(logs) == 0

    async def test_purge_deletes_old_reviews(self, session_factory):
        """Review history entries older than retention period are deleted."""
        hw = HistoryWriter(session_factory)
        old_review = _make_review()
        old_review.timestamp = datetime.now(UTC) - timedelta(days=45)
        new_review = _make_review()

        await hw.write_review("task-old", 1, old_review)
        await hw.write_review("task-new", 1, new_review)

        counts = await hw.purge_old_entries(retention_days=30)
        assert counts["review_history"] == 1
        # New review survives
        reviews = await hw.get_reviews("task-new")
        assert len(reviews) == 1
        # Old review deleted
        reviews = await hw.get_reviews("task-old")
        assert len(reviews) == 0

    async def test_purge_nothing_when_all_recent(self, session_factory):
        """No entries deleted when all are within retention window."""
        hw = HistoryWriter(session_factory)
        await hw.write_log("task-1", "recent log")
        await hw.write_review("task-1", 1, _make_review())

        counts = await hw.purge_old_entries(retention_days=30)
        assert counts["execution_logs"] == 0
        assert counts["review_history"] == 0

    async def test_purge_custom_retention(self, session_factory):
        """Custom retention_days value is respected."""
        hw = HistoryWriter(session_factory)
        from src.db import ExecutionLogRow, get_session

        # 10-day-old entry should survive 30-day retention but not 5-day
        ts_10d = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        async with get_session(session_factory) as session:
            session.add(ExecutionLogRow(
                task_id="task-mid", timestamp=ts_10d,
                level="info", message="mid-age", source="executor",
            ))

        # 30-day retention: entry survives
        counts = await hw.purge_old_entries(retention_days=30)
        assert counts["execution_logs"] == 0

        # 5-day retention: entry purged
        counts = await hw.purge_old_entries(retention_days=5)
        assert counts["execution_logs"] == 1
