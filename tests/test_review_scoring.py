"""Tests for review scoring, thresholds, raw response handling, cost extraction,
budget, timeout, review attempt, plan snapshot, progress callbacks, and human feedback.

Split from test_review_pipeline.py for maintainability.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.models import Task, TaskStatus
from src.review_pipeline import (
    MAX_RAW_RESPONSE_BYTES,
    ReviewPipeline,
    _extract_cost_usd,
    _truncate_raw_response,
)
from src.sdk_adapter import ClaudeEvent, ClaudeEventType
from tests.factories import (
    make_review_events,
    make_review_pipeline_config,
    make_task,
    mock_run_claude_query_from_events,
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
# Threshold boundary
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_threshold_boundary(mock_query: MagicMock) -> None:
    """Score exactly at threshold -> no human decision needed."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    # Deterministic: 2/2 = 1.0, which is >= 0.8
    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_deterministic_score_all_reject(mock_query: MagicMock) -> None:
    """All reviewers reject -> score 0.0."""
    setup_mock_query(mock_query, [
        make_review_events("reject", "Bad plan"),
        make_review_events("reject", "Risky"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    assert result.consensus_score == 0.0  # 0 approves / 2 total
    assert result.human_decision_needed is True


# ------------------------------------------------------------------
# raw_response capture
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_raw_response_captured(mock_query: MagicMock) -> None:
    """raw_response is structured JSON with model, usage, result, session_id."""
    inner_dict = {
        "blocking_issues": [],
        "suggestions": [],
        "pass": True,
    }
    setup_mock_query(mock_query, [
        make_review_events("approve", "Plan looks good"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t, p: None, complexity="S"
    )

    raw = json.loads(result.reviews[0].raw_response)
    assert "result" in raw
    assert raw["result"] == inner_dict


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_raw_response_captured_on_parse_failure(mock_query: MagicMock) -> None:
    """raw_response is captured even when JSON parsing fails (legacy result-as-string)."""
    raw_text = "This is not valid JSON but is the raw reviewer output"
    events = [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-test"),
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            result_text=raw_text,
            structured_output=None,
            model="claude-sonnet-4-5",
        ),
    ]
    setup_mock_query(mock_query, [events])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    raw = json.loads(result.reviews[0].raw_response)
    assert raw["result"] == raw_text
    assert result.reviews[0].verdict == "reject"


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_raw_response_contains_fields_beyond_result(mock_query: MagicMock) -> None:
    """raw_response must contain fields beyond just 'result' (invariant test from AC)."""
    setup_mock_query(mock_query, [
        make_review_events(
            "approve", "OK",
            model="claude-sonnet-4-5",
            usage={"input_tokens": 1000, "output_tokens": 500},
            session_id="sess-abc123",
        ),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    raw = json.loads(result.reviews[0].raw_response)
    # Invariant: raw_response keys minus "result" must be non-empty
    assert set(raw.keys()) - {"result"}
    assert raw["model"] == "claude-sonnet-4-5"
    assert raw["usage"] == {"input_tokens": 1000, "output_tokens": 500}
    assert raw["session_id"] == "sess-abc123"


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_raw_response_explicit_fields_only(mock_query: MagicMock) -> None:
    """raw_response contains only model/usage/result/session_id, not entire CLI blob."""
    setup_mock_query(mock_query, [
        make_review_events(
            "approve", "OK",
            model="claude-sonnet-4-5",
            usage={"input_tokens": 100, "output_tokens": 50},
            session_id="sess-xyz",
        ),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    raw = json.loads(result.reviews[0].raw_response)
    assert set(raw.keys()) == {"model", "usage", "result", "session_id"}


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_raw_response_decoupled_from_parsed_fields(mock_query: MagicMock) -> None:
    """Integration test: raw_response metadata keys are disjoint from parsed review fields.

    Validates the decoupling invariant (T-P2-75 postmortem): raw_response
    contains metadata (model, usage, session_id) NOT present in summary/
    suggestions, and the parsed fields (verdict, summary, suggestions) are
    extracted independently from the structured result -- not copied from
    raw_response top-level keys.
    """
    review_usage = {"input_tokens": 2500, "output_tokens": 800}
    review_suggestions = ["Add error handling", "Improve naming"]
    setup_mock_query(mock_query, [
        make_review_events(
            "approve",
            "Implementation is solid with minor nits",
            suggestions=review_suggestions,
            model="claude-sonnet-4-5",
            usage=review_usage,
            session_id="sess-decoupling-test",
        ),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan text", lambda c, t, p: None, complexity="S"
    )

    review = result.reviews[0]
    raw = json.loads(review.raw_response)

    # -- Invariant 1: raw_response metadata keys are disjoint from parsed fields --
    raw_metadata_keys = set(raw.keys()) - {"result"}  # {model, usage, session_id}
    parsed_field_names = {"verdict", "summary", "suggestions", "blocking_issues"}
    assert raw_metadata_keys.isdisjoint(parsed_field_names), (
        f"raw_response metadata keys {raw_metadata_keys} overlap with "
        f"parsed field names {parsed_field_names}"
    )

    # -- Invariant 2: raw_response metadata contains expected SDK fields --
    assert raw["model"] == "claude-sonnet-4-5"
    assert raw["usage"] == review_usage
    assert raw["session_id"] == "sess-decoupling-test"

    # -- Invariant 3: parsed fields come from structured result, not raw top-level --
    assert review.verdict == "approve"
    # Summary is auto-generated from suggestions when pass=true
    assert "Add error handling" in review.summary
    assert review.suggestions == review_suggestions

    # -- Invariant 4: raw_response["result"] has the LLM schema (not internal) --
    assert raw["result"]["pass"] is True
    assert raw["result"]["suggestions"] == review.suggestions
    assert raw["result"]["blocking_issues"] == []

    # -- Invariant 5: parsed fields are NOT among raw_response top-level keys --
    assert "verdict" not in raw
    assert "summary" not in raw
    assert "suggestions" not in raw


# ------------------------------------------------------------------
# _truncate_raw_response
# ------------------------------------------------------------------


def test_truncate_raw_response_short_text() -> None:
    """Short text passes through unchanged."""
    assert _truncate_raw_response("hello") == "hello"


def test_truncate_raw_response_at_limit() -> None:
    """Text exactly at limit passes through unchanged."""
    text = "x" * MAX_RAW_RESPONSE_BYTES
    assert _truncate_raw_response(text) == text


def test_truncate_raw_response_over_limit() -> None:
    """Text over 200KB is truncated with marker."""
    text = "x" * (MAX_RAW_RESPONSE_BYTES + 1000)
    result = _truncate_raw_response(text)
    assert len(result.encode("utf-8")) <= MAX_RAW_RESPONSE_BYTES
    assert result.endswith("[TRUNCATED at 200KB]")


# ------------------------------------------------------------------
# _extract_cost_usd
# ------------------------------------------------------------------


def test_extract_cost_usd_with_usage() -> None:
    """Cost is computed from usage.input_tokens and usage.output_tokens."""
    cli_output = {
        "type": "result",
        "result": "...",
        "usage": {"input_tokens": 1000, "output_tokens": 500},
    }
    # claude-sonnet-4-5: input=$3/1M, output=$15/1M
    # cost = (1000/1M)*3 + (500/1M)*15 = 0.003 + 0.0075 = 0.0105
    cost = _extract_cost_usd(cli_output, "claude-sonnet-4-5")
    assert cost is not None
    assert abs(cost - 0.0105) < 0.0001


def test_extract_cost_usd_opus() -> None:
    """Opus model uses its pricing tier."""
    cli_output = {
        "type": "result",
        "result": "...",
        "usage": {"input_tokens": 1000, "output_tokens": 500},
    }
    # claude-opus-4-6: input=$5/1M, output=$25/1M
    # cost = (1000/1M)*5 + (500/1M)*25 = 0.005 + 0.0125 = 0.0175
    cost = _extract_cost_usd(cli_output, "claude-opus-4-6")
    assert cost is not None
    assert abs(cost - 0.0175) < 0.0001


def test_extract_cost_usd_no_usage() -> None:
    """Returns None when usage field is missing."""
    cli_output = {"type": "result", "result": "..."}
    assert _extract_cost_usd(cli_output, "claude-sonnet-4-5") is None


def test_extract_cost_usd_partial_usage() -> None:
    """Returns None when usage is missing output_tokens."""
    cli_output = {
        "type": "result",
        "result": "...",
        "usage": {"input_tokens": 1000},
    }
    assert _extract_cost_usd(cli_output, "claude-sonnet-4-5") is None


def test_extract_cost_usd_invalid_usage() -> None:
    """Returns None when usage has non-numeric tokens."""
    cli_output = {
        "type": "result",
        "result": "...",
        "usage": {"input_tokens": "not_a_number", "output_tokens": 500},
    }
    assert _extract_cost_usd(cli_output, "claude-sonnet-4-5") is None


def test_extract_cost_usd_unknown_model() -> None:
    """Unknown model uses default pricing."""
    cli_output = {
        "type": "result",
        "result": "...",
        "usage": {"input_tokens": 1000, "output_tokens": 500},
    }
    # Default: input=$3/1M, output=$15/1M (same as sonnet)
    cost = _extract_cost_usd(cli_output, "some-future-model")
    assert cost is not None
    assert abs(cost - 0.0105) < 0.0001


# ------------------------------------------------------------------
# max_budget_usd in QueryOptions
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_reviewer_uses_max_budget_usd(mock_query: MagicMock) -> None:
    """QueryOptions includes max_budget_usd when reviewer sets it."""
    captured_options: list[Any] = []

    def _capture_query(prompt: str, options: Any = None):
        captured_options.append(options)
        return mock_run_claude_query_from_events(
            make_review_events("approve", "OK")
        )

    mock_query.side_effect = _capture_query

    config = ReviewPipelineConfig(
        reviewers=[
            ReviewerConfig(
                model="claude-opus-4-6",
                focus="feasibility_and_edge_cases",
                api="claude_cli",
                required=True,
                max_budget_usd=2.00,
            ),
        ],
    )
    pipeline = ReviewPipeline(config)

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert len(captured_options) >= 1
    assert captured_options[0].max_budget_usd == 2.00


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_reviewer_default_budget_omits_flag(mock_query: MagicMock) -> None:
    """Reviewer without explicit max_budget_usd passes None in QueryOptions."""
    captured_options: list[Any] = []

    def _capture_query(prompt: str, options: Any = None):
        captured_options.append(options)
        return mock_run_claude_query_from_events(
            make_review_events("approve", "OK")
        )

    mock_query.side_effect = _capture_query

    pipeline = ReviewPipeline(_default_config())

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert len(captured_options) >= 1
    assert captured_options[0].max_budget_usd is None


# ------------------------------------------------------------------
# cost_usd on LLMReview
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_cost_usd_captured_on_review(mock_query: MagicMock) -> None:
    """cost_usd is populated on LLMReview when usage data is in SDK output."""
    setup_mock_query(mock_query, [
        make_review_events(
            "approve", "OK",
            usage={"input_tokens": 1000, "output_tokens": 500},
            cost_usd=0.0105,
        ),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert result.reviews[0].cost_usd is not None
    assert result.reviews[0].cost_usd > 0


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_cost_usd_none_when_no_usage(mock_query: MagicMock) -> None:
    """cost_usd is None when SDK output has no usage data."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    # With no cost_usd from SDK and no usage for token-based estimate,
    # cost_usd will be None
    assert result.reviews[0].cost_usd is None


# ------------------------------------------------------------------
# Timeout behavior
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_timeout_raises_runtime_error(mock_query: MagicMock) -> None:
    """When SDK call hangs past timeout, RuntimeError is raised."""
    async def _hang_forever(prompt: str, options: Any = None):
        yield ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-hang")
        await asyncio.sleep(9999)

    mock_query.side_effect = _hang_forever

    config = ReviewPipelineConfig(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                api="claude_cli",
                required=True,
            ),
        ],
        review_timeout_minutes=1,
    )
    pipeline = ReviewPipeline(config, threshold=0.8)
    # Override to tiny value so test doesn't wait 60 seconds
    pipeline._timeout_minutes = 0.001

    with pytest.raises(RuntimeError, match="timed out"):
        await pipeline.review_task(
            _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
        )


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_no_timeout_when_zero(mock_query: MagicMock) -> None:
    """When review_timeout_minutes is 0, no timeout is applied."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
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
        review_timeout_minutes=0,
    )
    pipeline = ReviewPipeline(config, threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert result.consensus_score == 1.0
    assert len(result.reviews) == 1


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_normal_completion_within_timeout(mock_query: MagicMock) -> None:
    """Normal review completes within timeout -- no error."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
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
        review_timeout_minutes=10,
    )
    pipeline = ReviewPipeline(config, threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert result.consensus_score == 1.0
    assert result.reviews[0].verdict == "approve"


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_deterministic_merge_no_synthesis_call(mock_query: MagicMock) -> None:
    """Deterministic merge does not call synthesis -- only reviewer calls are made."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
        make_review_events("reject", "Bad"),
    ])

    config = ReviewPipelineConfig(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                api="claude_cli",
                required=True,
            ),
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="adversarial_red_team",
                api="claude_cli",
                required=False,
            ),
        ],
        review_timeout_minutes=1,
    )
    pipeline = ReviewPipeline(config, threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    # Only 2 reviewer calls, no synthesis
    assert mock_query.call_count == 2
    assert result.consensus_score == 0.5


# ------------------------------------------------------------------
# review_attempt parameter (T-P0-31)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_review_attempt_passed_to_history_writer(mock_query: MagicMock) -> None:
    """review_attempt is forwarded to HistoryWriter.write_review()."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    mock_writer = AsyncMock()
    mock_writer.write_review = AsyncMock()

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, history_writer=mock_writer,
    )

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S", review_attempt=3,
    )

    mock_writer.write_review.assert_called_once()
    kw = mock_writer.write_review.call_args.kwargs
    assert kw["review_attempt"] == 3


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_review_attempt_defaults_to_1(mock_query: MagicMock) -> None:
    """review_attempt defaults to 1 when not specified."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    mock_writer = AsyncMock()
    mock_writer.write_review = AsyncMock()

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, history_writer=mock_writer,
    )

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )

    kw = mock_writer.write_review.call_args.kwargs
    assert kw["review_attempt"] == 1


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_review_attempt_on_multi_reviewer(mock_query: MagicMock) -> None:
    """review_attempt is the same for all reviewers in a single pipeline run."""
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
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="M", review_attempt=2,
    )

    assert mock_writer.write_review.call_count == 2
    for c in mock_writer.write_review.call_args_list:
        assert c.kwargs["review_attempt"] == 2


# ------------------------------------------------------------------
# review_timeout_minutes config (T-P0-31)
# ------------------------------------------------------------------


def test_review_timeout_minutes_default() -> None:
    """ReviewPipelineConfig defaults review_timeout_minutes to 60."""
    config = ReviewPipelineConfig()
    assert config.review_timeout_minutes == 60


def test_review_timeout_minutes_custom() -> None:
    """review_timeout_minutes can be customized."""
    config = ReviewPipelineConfig(review_timeout_minutes=30)
    assert config.review_timeout_minutes == 30


def test_review_timeout_minutes_zero_disables() -> None:
    """review_timeout_minutes=0 is valid (disables timeout)."""
    config = ReviewPipelineConfig(review_timeout_minutes=0)
    assert config.review_timeout_minutes == 0


# ------------------------------------------------------------------
# plan_snapshot storage (T-P0-35)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_plan_snapshot_stored_on_first_round(mock_query: MagicMock) -> None:
    """plan_snapshot is passed to write_review only on the first round (i==0)."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    mock_writer = AsyncMock()
    mock_writer.write_review = AsyncMock()

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, history_writer=mock_writer,
    )

    plan_text = "## My Plan\n- Step 1"
    await pipeline.review_task(
        _sample_task(), plan_text, lambda c, t, p: None, complexity="S",
    )

    mock_writer.write_review.assert_called_once()
    kw = mock_writer.write_review.call_args.kwargs
    assert kw["plan_snapshot"] == plan_text


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_plan_snapshot_none_on_subsequent_rounds(mock_query: MagicMock) -> None:
    """plan_snapshot is None for the second reviewer (round 2) in multi-reviewer."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
        make_review_events("approve", "OK"),
    ])

    mock_writer = AsyncMock()
    mock_writer.write_review = AsyncMock()

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, history_writer=mock_writer,
    )

    plan_text = "Plan text here"
    await pipeline.review_task(
        _sample_task(), plan_text, lambda c, t, p: None, complexity="M",
    )

    assert mock_writer.write_review.call_count == 2
    # First round gets the snapshot
    first_kw = mock_writer.write_review.call_args_list[0].kwargs
    assert first_kw["plan_snapshot"] == plan_text
    # Second round gets None
    second_kw = mock_writer.write_review.call_args_list[1].kwargs
    assert second_kw["plan_snapshot"] is None


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_plan_snapshot_empty_plan(mock_query: MagicMock) -> None:
    """Empty plan text is stored as plan_snapshot (not converted to None)."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    mock_writer = AsyncMock()
    mock_writer.write_review = AsyncMock()

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, history_writer=mock_writer,
    )

    await pipeline.review_task(
        _sample_task(), "", lambda c, t, p: None, complexity="S",
    )

    kw = mock_writer.write_review.call_args.kwargs
    assert kw["plan_snapshot"] == ""


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_plan_snapshot_no_history_writer(mock_query: MagicMock) -> None:
    """When no history_writer is configured, pipeline runs without error."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, history_writer=None,
    )

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )

    assert result.consensus_score == 1.0


def test_review_timeout_minutes_stored_on_pipeline() -> None:
    """ReviewPipeline stores _timeout_minutes from config."""
    config = ReviewPipelineConfig(review_timeout_minutes=15)
    pipeline = ReviewPipeline(config)
    assert pipeline._timeout_minutes == 15


# ------------------------------------------------------------------
# Phase strings in on_progress (T-P0-32)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_phase_string_starting(mock_query: MagicMock) -> None:
    """First phase call is 'Starting {focus} review...'."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    phases: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan",
        lambda _c, _t, p: phases.append(p),
        complexity="S",
    )

    assert phases[0] == "Starting feasibility_and_edge_cases review..."


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_phase_string_completed(mock_query: MagicMock) -> None:
    """Second phase call is 'Completed {focus} review'."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    phases: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan",
        lambda _c, _t, p: phases.append(p),
        complexity="S",
    )

    assert phases[1] == "Completed feasibility_and_edge_cases review"


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_no_synthesizing_for_multi_reviewer(mock_query: MagicMock) -> None:
    """Deterministic merge: no 'Synthesizing...' phase emitted."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
        make_review_events("reject", "Bad"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    phases: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan",
        lambda _c, _t, p: phases.append(p),
        complexity="M",
    )

    assert "Synthesizing..." not in phases


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_no_synthesizing_for_single_reviewer(mock_query: MagicMock) -> None:
    """'Synthesizing...' phase is NOT emitted for single-reviewer runs."""
    setup_mock_query(mock_query, [
        make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    phases: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan",
        lambda _c, _t, p: phases.append(p),
        complexity="S",
    )

    assert "Synthesizing..." not in phases


# ------------------------------------------------------------------
# Human feedback injection tests (T-P0-34)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_human_feedback_injected_into_prompt(mock_query: MagicMock) -> None:
    """When human_feedback is provided, it should appear in the SDK prompt."""
    captured_prompts: list[str] = []

    def _capture_query(prompt: str, options: Any = None):
        captured_prompts.append(prompt)
        return mock_run_claude_query_from_events(
            make_review_events("approve", "OK")
        )

    mock_query.side_effect = _capture_query

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    feedback = [
        {
            "human_decision": "request_changes",
            "human_reason": "Add timeout handling",
            "review_attempt": 1,
            "timestamp": "2026-03-03T05:00:00",
        },
        {
            "human_decision": "request_changes",
            "human_reason": "Also fix error path",
            "review_attempt": 2,
            "timestamp": "2026-03-03T06:00:00",
        },
    ]

    await pipeline.review_task(
        _sample_task(), "Build the thing",
        lambda c, t, p: None,
        complexity="S",
        human_feedback=feedback,
    )

    assert len(captured_prompts) >= 1
    prompt = captured_prompts[0]
    assert "Previous human feedback" in prompt
    assert "Add timeout handling" in prompt
    assert "Also fix error path" in prompt
    assert "[Attempt 1] REQUEST_CHANGES" in prompt
    assert "[Attempt 2] REQUEST_CHANGES" in prompt


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_no_feedback_no_injection(mock_query: MagicMock) -> None:
    """When no human_feedback is provided, prompt should not contain feedback section."""
    captured_prompts: list[str] = []

    def _capture_query(prompt: str, options: Any = None):
        captured_prompts.append(prompt)
        return mock_run_claude_query_from_events(
            make_review_events("approve", "OK")
        )

    mock_query.side_effect = _capture_query

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    await pipeline.review_task(
        _sample_task(), "Build the thing",
        lambda c, t, p: None,
        complexity="S",
        human_feedback=None,
    )

    assert len(captured_prompts) >= 1
    prompt = captured_prompts[0]
    assert "Previous human feedback" not in prompt


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_empty_feedback_list_no_injection(mock_query: MagicMock) -> None:
    """Empty feedback list should not inject feedback section."""
    captured_prompts: list[str] = []

    def _capture_query(prompt: str, options: Any = None):
        captured_prompts.append(prompt)
        return mock_run_claude_query_from_events(
            make_review_events("approve", "OK")
        )

    mock_query.side_effect = _capture_query

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    await pipeline.review_task(
        _sample_task(), "Build the thing",
        lambda c, t, p: None,
        complexity="S",
        human_feedback=[],
    )

    assert len(captured_prompts) >= 1
    prompt = captured_prompts[0]
    assert "Previous human feedback" not in prompt


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_feedback_without_reason(mock_query: MagicMock) -> None:
    """Feedback entry with empty reason includes only the decision."""
    captured_prompts: list[str] = []

    def _capture_query(prompt: str, options: Any = None):
        captured_prompts.append(prompt)
        return mock_run_claude_query_from_events(
            make_review_events("approve", "OK")
        )

    mock_query.side_effect = _capture_query

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    feedback = [
        {
            "human_decision": "reject",
            "human_reason": "",
            "review_attempt": 1,
            "timestamp": "2026-03-03T05:00:00",
        },
    ]

    await pipeline.review_task(
        _sample_task(), "Build the thing",
        lambda c, t, p: None,
        complexity="S",
        human_feedback=feedback,
    )

    prompt = captured_prompts[0]
    assert "Previous human feedback" in prompt
    assert "[Attempt 1] REJECT" in prompt
