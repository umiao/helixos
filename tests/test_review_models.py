"""Tests for review lifecycle state computation, ReviewResult/ParseReview validation,
stream event callbacks, conversation summary extraction, selective hooks loading,
and parallel reviewer execution.

Split from test_review_pipeline.py for maintainability.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.models import ReviewLifecycleState, Task, TaskStatus
from src.review_pipeline import (
    ReviewPipeline,
    ReviewResult,
    _extract_conversation_summary,
    _extract_cost_usd,
)
from src.sdk_adapter import ClaudeEvent, ClaudeEventType
from tests.factories import (
    make_error_events,
    make_review_events,
    make_review_pipeline_config,
    make_task,
    setup_mock_query,
)

# ------------------------------------------------------------------
# Local helpers (simple wrappers around shared factories)
# ------------------------------------------------------------------


def _default_config() -> ReviewPipelineConfig:
    """Create a default review pipeline config with 1 required + 1 optional."""
    return make_review_pipeline_config()


def _sample_task() -> Task:
    """Create a sample task for testing."""
    return make_task(
        task_id="P0::T-P0-7",
        project_id="P0",
        local_task_id="T-P0-7",
        title="Review pipeline implementation",
        description="Implement the LLM review pipeline",
        status=TaskStatus.REVIEW,
    )


# ------------------------------------------------------------------
# TestLifecycleStateComputation
# ------------------------------------------------------------------


class TestLifecycleStateComputation:
    """Test _compute_lifecycle_state static method."""

    def test_single_reviewer_approve(self) -> None:
        """Single reviewer approve -> APPROVED."""
        from src.models import LLMReview

        reviews = [LLMReview(
            model="test", focus="test", verdict="approve",
            summary="ok", timestamp=datetime.now(UTC),
        )]
        state = ReviewPipeline._compute_lifecycle_state(reviews, 1.0, 1)
        assert state == ReviewLifecycleState.APPROVED

    def test_single_reviewer_reject(self) -> None:
        """Single reviewer reject -> REJECTED_SINGLE."""
        from src.models import LLMReview

        reviews = [LLMReview(
            model="test", focus="test", verdict="reject",
            summary="bad", timestamp=datetime.now(UTC),
        )]
        state = ReviewPipeline._compute_lifecycle_state(reviews, 0.0, 1)
        assert state == ReviewLifecycleState.REJECTED_SINGLE

    def test_multi_reviewer_above_threshold(self) -> None:
        """Multi-reviewer score >= threshold -> APPROVED."""
        from src.models import LLMReview

        reviews = [
            LLMReview(model="a", focus="a", verdict="approve",
                      summary="ok", timestamp=datetime.now(UTC)),
            LLMReview(model="b", focus="b", verdict="approve",
                      summary="ok", timestamp=datetime.now(UTC)),
        ]
        state = ReviewPipeline._compute_lifecycle_state(reviews, 0.9, 2, 0.8)
        assert state == ReviewLifecycleState.APPROVED

    def test_multi_reviewer_below_threshold(self) -> None:
        """Multi-reviewer score < threshold -> REJECTED_CONSENSUS."""
        from src.models import LLMReview

        reviews = [
            LLMReview(model="a", focus="a", verdict="approve",
                      summary="ok", timestamp=datetime.now(UTC)),
            LLMReview(model="b", focus="b", verdict="reject",
                      summary="bad", timestamp=datetime.now(UTC)),
        ]
        state = ReviewPipeline._compute_lifecycle_state(reviews, 0.5, 2, 0.8)
        assert state == ReviewLifecycleState.REJECTED_CONSENSUS

    def test_multi_reviewer_at_threshold(self) -> None:
        """Multi-reviewer score exactly at threshold -> APPROVED."""
        from src.models import LLMReview

        reviews = [
            LLMReview(model="a", focus="a", verdict="approve",
                      summary="ok", timestamp=datetime.now(UTC)),
            LLMReview(model="b", focus="b", verdict="approve",
                      summary="ok", timestamp=datetime.now(UTC)),
        ]
        state = ReviewPipeline._compute_lifecycle_state(reviews, 0.8, 2, 0.8)
        assert state == ReviewLifecycleState.APPROVED

    def test_partial_reviews(self) -> None:
        """Fewer reviews than expected -> PARTIAL."""
        from src.models import LLMReview

        reviews = [LLMReview(
            model="a", focus="a", verdict="approve",
            summary="ok", timestamp=datetime.now(UTC),
        )]
        state = ReviewPipeline._compute_lifecycle_state(reviews, 1.0, 2, 0.8)
        assert state == ReviewLifecycleState.PARTIAL


# ------------------------------------------------------------------
# Lifecycle state on ReviewState
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_lifecycle_state_on_review_state_approve(mock_query: MagicMock) -> None:
    """ReviewState.lifecycle_state is APPROVED on approval."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])
    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_lifecycle_state_on_review_state_rejected_single(mock_query: MagicMock) -> None:
    """ReviewState.lifecycle_state is REJECTED_SINGLE on single reject."""
    setup_mock_query(mock_query, [
        make_review_events("reject", "Bad"),
    ])
    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )
    assert result.lifecycle_state == ReviewLifecycleState.REJECTED_SINGLE


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_lifecycle_state_on_review_state_rejected_consensus(
    mock_query: MagicMock,
) -> None:
    """ReviewState.lifecycle_state is REJECTED_CONSENSUS when multi-reviewer score < threshold."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
        make_review_events("reject", "Bad"),
    ])
    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M",
    )
    assert result.lifecycle_state == ReviewLifecycleState.REJECTED_CONSENSUS


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_lifecycle_state_passed_to_history_writer(mock_query: MagicMock) -> None:
    """lifecycle_state is forwarded to HistoryWriter.write_review() for each entry."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
        make_review_events("approve", "OK"),
    ])

    mock_writer = AsyncMock()
    mock_writer.write_review = AsyncMock()

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, history_writer=mock_writer,
    )

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M",
    )

    assert mock_writer.write_review.call_count == 2
    # First entry (non-final) should have RUNNING
    first_kw = mock_writer.write_review.call_args_list[0].kwargs
    assert first_kw["lifecycle_state"] == ReviewLifecycleState.RUNNING
    # Second entry (final) should have the terminal state
    second_kw = mock_writer.write_review.call_args_list[1].kwargs
    assert second_kw["lifecycle_state"] == ReviewLifecycleState.APPROVED


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_lifecycle_single_reviewer_history_writer(mock_query: MagicMock) -> None:
    """Single reviewer: the only entry gets the terminal lifecycle state."""
    setup_mock_query(mock_query, [
        make_review_events("reject", "Issues found"),
    ])

    mock_writer = AsyncMock()
    mock_writer.write_review = AsyncMock()

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, history_writer=mock_writer,
    )

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )

    assert mock_writer.write_review.call_count == 1
    kw = mock_writer.write_review.call_args.kwargs
    assert kw["lifecycle_state"] == ReviewLifecycleState.REJECTED_SINGLE


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_single_reviewer_reject_score_is_zero(mock_query: MagicMock) -> None:
    """Single reviewer reject score is 0.0 (not legacy 0.3)."""
    setup_mock_query(mock_query, [
        make_review_events("reject", "Bad plan"),
    ])
    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )
    assert result.consensus_score == 0.0


@pytest.mark.asyncio
async def test_no_reviewers_lifecycle_is_approved() -> None:
    """No active reviewers -> auto-approve with APPROVED lifecycle state."""
    config = ReviewPipelineConfig(reviewers=[])
    pipeline = ReviewPipeline(config)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED
    assert result.consensus_score == 1.0


def test_extract_cost_usd_haiku_updated_pricing() -> None:
    """Haiku 4.5 uses updated pricing ($1/$5 per million tokens)."""
    cli_output = {
        "type": "result",
        "result": "...",
        "usage": {"input_tokens": 1000, "output_tokens": 500},
    }
    # claude-haiku-4-5: input=$1/1M, output=$5/1M
    # cost = (1000/1M)*1 + (500/1M)*5 = 0.001 + 0.0025 = 0.0035
    cost = _extract_cost_usd(cli_output, "claude-haiku-4-5")
    assert cost is not None
    assert abs(cost - 0.0035) < 0.0001


# ------------------------------------------------------------------
# on_log streaming callback (T-P0-64)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_on_log_receives_lifecycle_messages(mock_query: MagicMock) -> None:
    """on_log callback receives review lifecycle messages (started, phase, completed)."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    log_lines: list[str] = []
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
        on_log=lambda line: log_lines.append(line),
    )

    assert result.consensus_score == 1.0
    # Check lifecycle messages
    assert any("Review started" in line for line in log_lines)
    assert any("Starting feasibility_and_edge_cases review" in line for line in log_lines)
    assert any("Completed feasibility_and_edge_cases review" in line for line in log_lines)
    assert any("Review completed: approved" in line for line in log_lines)


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_on_log_receives_sdk_output_lines(mock_query: MagicMock) -> None:
    """on_log receives simplified stream events including [DONE] for result."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "Looks good", ["Minor nit"]),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    log_lines: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
        on_log=lambda line: log_lines.append(line),
    )

    # With SDK, result event is simplified to [DONE]
    done_lines = [line for line in log_lines if "[DONE]" in line]
    assert len(done_lines) >= 1


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_on_log_none_does_not_crash(mock_query: MagicMock) -> None:
    """Passing on_log=None (default) does not raise errors."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    # on_log=None is the default -- should work without errors
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
    )

    assert result.consensus_score == 1.0


# ------------------------------------------------------------------
# Raw artifact persistence tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_raw_artifact_persisted(mock_query: MagicMock) -> None:
    """on_raw_artifact is called with serialized event data."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "Looks good"),
    ])

    artifact_content: list[str] = []

    async def capture_artifact(content: str) -> None:
        artifact_content.append(content)

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
        on_raw_artifact=capture_artifact,
    )

    # on_raw_artifact MUST have been called
    assert len(artifact_content) == 1
    assert len(artifact_content[0]) > 0


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_raw_artifact_persisted_on_error(mock_query: MagicMock) -> None:
    """on_raw_artifact is called BEFORE RuntimeError is raised on SDK error."""
    setup_mock_query(mock_query, [
        make_error_events("SDK crashed"),
    ])

    artifact_content: list[str] = []

    async def capture_artifact(content: str) -> None:
        artifact_content.append(content)

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    with pytest.raises(RuntimeError, match="SDK error"):
        await pipeline.review_task(
            _sample_task(), "Plan", lambda c, t, p: None,
            complexity="S",
            on_raw_artifact=capture_artifact,
        )

    # on_raw_artifact MUST have been called BEFORE the RuntimeError
    assert len(artifact_content) == 1


# ------------------------------------------------------------------
# Unit tests: Pydantic validation models for review/synthesis
# ------------------------------------------------------------------


class TestReviewResultModel:
    """Tests for ReviewResult Pydantic validation (new schema)."""

    def test_valid_pass(self) -> None:
        """Valid passing review passes validation."""
        result = ReviewResult.model_validate({
            "blocking_issues": [],
            "suggestions": ["Minor nit"],
            "pass": True,
        })
        assert result.pass_ is True
        assert result.blocking_issues == []
        assert result.suggestions == ["Minor nit"]

    def test_valid_fail(self) -> None:
        """Valid failing review passes validation."""
        result = ReviewResult.model_validate({
            "blocking_issues": [{"issue": "Missing tests", "severity": "high"}],
            "suggestions": [],
            "pass": False,
        })
        assert result.pass_ is False
        assert len(result.blocking_issues) == 1
        assert result.blocking_issues[0].issue == "Missing tests"

    def test_invalid_severity_rejected(self) -> None:
        """Invalid severity enum value is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ReviewResult.model_validate({
                "blocking_issues": [{"issue": "x", "severity": "low"}],
                "suggestions": [],
                "pass": False,
            })

    def test_missing_pass_rejected(self) -> None:
        """Missing required pass field is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ReviewResult.model_validate({
                "blocking_issues": [],
                "suggestions": [],
            })



class TestParseReviewWithValidation:
    """Tests that _parse_review uses Pydantic and logs raw content."""

    def test_invalid_schema_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Old-format schema triggers Pydantic rejection and logs raw content."""
        text = json.dumps({
            "verdict": "maybe",
            "summary": "s",
            "suggestions": [],
        })
        pipeline = ReviewPipeline(_default_config())
        reviewer = ReviewerConfig(model="claude-sonnet-4-5", focus="test")
        with caplog.at_level("WARNING"):
            review = pipeline._parse_review(text, reviewer)
        assert review.verdict == "reject"  # fallback
        assert review.summary == text  # raw text as summary
        assert "Raw" in caplog.text

    def test_malformed_json_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed JSON logs raw content."""
        pipeline = ReviewPipeline(_default_config())
        reviewer = ReviewerConfig(model="claude-sonnet-4-5", focus="test")
        with caplog.at_level("WARNING"):
            review = pipeline._parse_review("not json!", reviewer)
        assert review.verdict == "reject"
        assert "Raw" in caplog.text
        assert "not json!" in caplog.text



# ------------------------------------------------------------------
# T-P0-94: Stream event callback tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_on_stream_event_receives_parsed_events(mock_query: MagicMock) -> None:
    """on_stream_event callback receives parsed event dicts."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "Looks good"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    stream_events: list[dict] = []
    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
        on_stream_event=lambda ev: stream_events.append(ev),
    )

    # Should have received at least the result event
    assert len(stream_events) >= 1
    result_events = [e for e in stream_events if e.get("type") == "result"]
    assert len(result_events) >= 1
    assert result_events[0].get("structured_output") == {
        "blocking_issues": [], "suggestions": [], "pass": True,
    }


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_stream_events_include_init_and_text(mock_query: MagicMock) -> None:
    """Stream events include init and text event types."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    stream_events: list[dict] = []
    log_lines: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
        on_log=lambda line: log_lines.append(line),
        on_stream_event=lambda ev: stream_events.append(ev),
    )

    # All 3 events should be received (init + text + result)
    assert len(stream_events) >= 3
    types = [e.get("type") for e in stream_events]
    assert "init" in types
    assert "text" in types
    assert "result" in types

    # on_log should contain simplified text
    assert any("[INIT]" in line for line in log_lines)
    assert any("[DONE]" in line for line in log_lines)


# ------------------------------------------------------------------
# Conversation turns (T-P1-89)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_conversation_turns_populated(mock_query: MagicMock) -> None:
    """conversation_turns is populated on LLMReview after SDK call."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "Looks good"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )

    review = result.reviews[0]
    assert isinstance(review.conversation_turns, list)
    assert len(review.conversation_turns) >= 1
    # Each turn should have text and tool_actions keys
    turn = review.conversation_turns[0]
    assert "text" in turn
    assert "tool_actions" in turn


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_conversation_summary_populated(mock_query: MagicMock) -> None:
    """conversation_summary is populated with findings/actions/conclusion."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "Looks good"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )

    review = result.reviews[0]
    assert isinstance(review.conversation_summary, dict)
    assert "findings" in review.conversation_summary
    assert "actions_taken" in review.conversation_summary
    assert "conclusion" in review.conversation_summary


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_conversation_turns_with_tool_actions(mock_query: MagicMock) -> None:
    """conversation_turns captures tool use events."""
    events = [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-test"),
        ClaudeEvent(
            type=ClaudeEventType.TEXT,
            text="Let me check...",
            model="claude-sonnet-4-5",
        ),
        ClaudeEvent(
            type=ClaudeEventType.TOOL_USE,
            tool_name="Read",
            tool_input={"file": "src/main.py"},
            tool_use_id="tu-1",
            model="claude-sonnet-4-5",
        ),
        ClaudeEvent(
            type=ClaudeEventType.TOOL_RESULT,
            tool_result_content="file content here",
            tool_result_for_id="tu-1",
        ),
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output={"verdict": "approve", "summary": "OK", "suggestions": []},
            model="claude-sonnet-4-5",
        ),
    ]
    setup_mock_query(mock_query, [events])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )

    review = result.reviews[0]
    assert len(review.conversation_turns) >= 1
    # Should have at least one turn with a tool action
    has_tool = any(
        len(t.get("tool_actions", [])) > 0
        for t in review.conversation_turns
    )
    assert has_tool

    # conversation_summary should reflect the tool action
    assert "Read" in review.conversation_summary.get("actions_taken", [])


# ------------------------------------------------------------------
# _extract_conversation_summary unit tests
# ------------------------------------------------------------------


def test_extract_conversation_summary_empty() -> None:
    """Empty turns list produces empty summary."""
    summary = _extract_conversation_summary([])
    assert summary["findings"] == []
    assert summary["actions_taken"] == []
    assert summary["conclusion"] == ""


def test_extract_conversation_summary_text_only() -> None:
    """Turns with only text produce findings and conclusion."""
    from src.sdk_adapter import AssistantTurn

    turns = [
        AssistantTurn(text="First observation"),
        AssistantTurn(text="Final conclusion"),
    ]
    summary = _extract_conversation_summary(turns)
    assert len(summary["findings"]) == 2
    assert summary["conclusion"] == "Final conclusion"
    assert summary["actions_taken"] == []


def test_extract_conversation_summary_with_tools() -> None:
    """Turns with tool actions populate actions_taken."""
    from src.sdk_adapter import AssistantTurn, ToolAction

    turns = [
        AssistantTurn(
            text="Checking files",
            tool_actions=[
                ToolAction(tool_use_id="tu-1", name="Read", input={}),
                ToolAction(tool_use_id="tu-2", name="Grep", input={}),
            ],
        ),
    ]
    summary = _extract_conversation_summary(turns)
    assert "Read" in summary["actions_taken"]
    assert "Grep" in summary["actions_taken"]


# ------------------------------------------------------------------
# Selective hooks loading tests (T-P1-103)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_review_disables_cli_hooks(mock_query: MagicMock) -> None:
    """Review agent uses setting_sources=[] to disable CLI hooks."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "Looks good"),
    ])

    config = ReviewPipelineConfig(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                api="claude_cli",
                required=True,
            ),
        ],
    )
    pipeline = ReviewPipeline(config, threshold=0.8)

    await pipeline.review_task(
        _sample_task(),
        "Build the thing",
        lambda c, t, p: None,
        complexity="S",
    )

    # Check that all calls to run_claude_query used setting_sources=[]
    for call in mock_query.call_args_list:
        options = call[1].get("options") or call[0][1]
        assert options.setting_sources == [], (
            f"Expected setting_sources=[] but got {options.setting_sources}"
        )


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_review_injects_session_context(mock_query: MagicMock) -> None:
    """Review agent system prompt includes session context."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "Looks good"),
    ])

    config = ReviewPipelineConfig(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                api="claude_cli",
                required=True,
            ),
        ],
    )
    pipeline = ReviewPipeline(config, threshold=0.8)

    await pipeline.review_task(
        _sample_task(),
        "Build the thing",
        lambda c, t, p: None,
        complexity="S",
    )

    # Check that the system prompt contains session context markers
    for call in mock_query.call_args_list:
        options = call[1].get("options") or call[0][1]
        assert "Session Context" in options.system_prompt, (
            "Expected session context in system prompt"
        )


# ------------------------------------------------------------------
# Parallel reviewer execution (T-P1-130)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_parallel_reviewers_both_called_concurrently(
    mock_query: MagicMock,
) -> None:
    """Two reviewers are called via asyncio.gather, not sequentially."""
    call_times: list[tuple[str, float]] = []
    original_events = [
        make_review_events("approve", "Feasible"),
        make_review_events("approve", "No risks"),
    ]

    call_count = 0

    async def _delayed_query(prompt: str, options: Any = None):
        nonlocal call_count
        idx = min(call_count, len(original_events) - 1)
        call_count += 1
        # Record when each call starts
        call_times.append(("start", asyncio.get_event_loop().time()))
        # Small delay to prove concurrency
        await asyncio.sleep(0.05)
        call_times.append(("end", asyncio.get_event_loop().time()))
        for event in original_events[idx]:
            yield event

    mock_query.side_effect = _delayed_query

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M",
    )

    assert len(result.reviews) == 2
    assert mock_query.call_count == 2
    # Both calls should start before either finishes (concurrent)
    starts = [t for label, t in call_times if label == "start"]
    ends = [t for label, t in call_times if label == "end"]
    assert len(starts) == 2
    # Second call starts before first call ends -> concurrent
    assert starts[1] < ends[0], (
        "Expected concurrent execution: second reviewer should start "
        "before first reviewer finishes"
    )


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_parallel_partial_failure_captures_successful_review(
    mock_query: MagicMock,
) -> None:
    """If one reviewer returns an error, the other's result is still captured."""
    # First reviewer approves, second returns SDK error events
    setup_mock_query(mock_query, [
        make_review_events("approve", "Looks good"),
        make_error_events("SDK timeout"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M",
    )

    # Both reviews captured: one success, one error-reject
    assert len(result.reviews) == 2
    assert result.reviews[0].verdict == "approve"
    assert result.reviews[1].verdict == "reject"
    assert "SDK timeout" in result.reviews[1].summary
    # Consensus: 1 approve / 2 total = 0.5
    assert result.consensus_score == 0.5


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_single_reviewer_unchanged_with_parallel_code(
    mock_query: MagicMock,
) -> None:
    """Single reviewer (S complexity) still works as before -- no regression."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "All good"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    progress_calls: list[tuple[int, int, str]] = []
    result = await pipeline.review_task(
        _sample_task(),
        "Plan",
        lambda c, t, p: progress_calls.append((c, t, p)),
        complexity="S",
    )

    assert result.consensus_score == 1.0
    assert len(result.reviews) == 1
    assert result.reviews[0].verdict == "approve"
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED
    assert mock_query.call_count == 1
    # Progress follows sequential pattern for single reviewer
    assert progress_calls == [
        (0, 1, "Starting feasibility_and_edge_cases review..."),
        (1, 1, "Completed feasibility_and_edge_cases review"),
    ]
