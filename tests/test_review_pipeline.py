"""Tests for the ReviewPipeline.

Uses subprocess mocking (patching asyncio.create_subprocess_exec) to test
approve, reject, disagree, progress callback, synthesis, and error-handling
scenarios. All LLM calls go through the Claude CLI subprocess.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.models import ExecutorType, ReviewLifecycleState, Task, TaskStatus
from src.review_pipeline import (
    MAX_RAW_RESPONSE_BYTES,
    ReviewPipeline,
    ReviewResult,
    SynthesisResult,
    _extract_cost_usd,
    _kill_review_process,
    _terminate_review_process,
    _truncate_raw_response,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_cli_output(inner: dict | str) -> bytes:
    """Create mock Claude CLI stdout bytes.

    When ``inner`` is a dict, simulates ``--json-schema`` mode where
    ``structured_output`` is already a parsed object and ``result`` is null.
    When ``inner`` is a str, simulates legacy mode with ``result`` as string.
    """
    if isinstance(inner, dict):
        cli_output = {"type": "result", "structured_output": inner, "result": None}
    else:
        cli_output = {"type": "result", "result": inner}
    return json.dumps(cli_output).encode("utf-8")


def _make_review_output(
    verdict: str,
    summary: str,
    suggestions: list[str] | None = None,
) -> bytes:
    """Create mock Claude CLI stdout for a review response."""
    inner = {
        "verdict": verdict,
        "summary": summary,
        "suggestions": suggestions or [],
    }
    return _make_cli_output(inner)


def _make_synthesis_output(
    score: float,
    disagreements: list[str] | None = None,
) -> bytes:
    """Create mock Claude CLI stdout for a synthesis response."""
    inner = {
        "score": score,
        "disagreements": disagreements or [],
    }
    return _make_cli_output(inner)


def _mock_proc(stdout: bytes, returncode: int = 0) -> AsyncMock:
    """Create a mock subprocess with readline-based stdout.

    Simulates line-by-line reading used by the streaming _call_claude_cli.
    """
    proc = AsyncMock()
    proc.returncode = returncode
    proc.wait = AsyncMock()
    proc.kill = MagicMock()

    # Build a mock stdout that yields lines then EOF
    lines = stdout.split(b"\n") if stdout else []
    line_queue: list[bytes] = [line + b"\n" for line in lines if line]
    line_queue.append(b"")  # EOF sentinel

    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=line_queue)

    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"")

    proc.stdout = mock_stdout
    proc.stderr = mock_stderr

    return proc


def _default_config() -> ReviewPipelineConfig:
    """Create a default review pipeline config with 1 required + 1 optional."""
    return ReviewPipelineConfig(
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
    )


def _sample_task() -> Task:
    """Create a sample task for testing."""
    return Task(
        id="P0::T-P0-7",
        project_id="P0",
        local_task_id="T-P0-7",
        title="Review pipeline implementation",
        description="Implement the LLM review pipeline",
        status=TaskStatus.REVIEW,
        executor_type=ExecutorType.CODE,
    )


# ------------------------------------------------------------------
# Single-reviewer tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_single_reviewer_approve(mock_exec: AsyncMock) -> None:
    """Single required reviewer approves -> score 1.0, no human decision."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "Plan looks good", [])
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    progress_calls: list[tuple[int, int, str]] = []
    result = await pipeline.review_task(
        _sample_task(),
        "Build the thing",
        lambda c, t, p: progress_calls.append((c, t, p)),
        complexity="S",
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False
    assert len(result.reviews) == 1
    assert result.reviews[0].verdict == "approve"
    assert result.rounds_total == 1
    assert result.rounds_completed == 1
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED
    # Only required reviewer called for S complexity
    assert mock_exec.call_count == 1


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_single_reviewer_reject(mock_exec: AsyncMock) -> None:
    """Single required reviewer rejects -> score 0.0, REJECTED_SINGLE lifecycle."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("reject", "Plan has issues", ["Fix error handling", "Add tests"])
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t, p: None, complexity="S"
    )

    assert result.consensus_score == 0.0
    assert result.human_decision_needed is True
    assert len(result.reviews) == 1
    assert result.reviews[0].verdict == "reject"
    assert result.decision_points == ["Fix error handling", "Add tests"]
    assert result.lifecycle_state == "rejected_single"


# ------------------------------------------------------------------
# Multi-reviewer tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_multi_reviewer_disagree(mock_exec: AsyncMock) -> None:
    """Two reviewers disagree -> synthesis called, score from synthesis."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "Looks feasible")),
        _mock_proc(_make_review_output("reject", "Security risk", ["Add auth check"])),
        _mock_proc(_make_synthesis_output(0.65, ["Security concerns"])),
    ]

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t, p: None, complexity="M"
    )

    assert result.consensus_score == 0.65
    assert result.human_decision_needed is True
    assert len(result.reviews) == 2
    assert result.decision_points == ["Security concerns"]
    assert result.lifecycle_state == ReviewLifecycleState.REJECTED_CONSENSUS
    # 2 review calls + 1 synthesis call = 3 total
    assert mock_exec.call_count == 3


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_multi_reviewer_agree(mock_exec: AsyncMock) -> None:
    """Two reviewers both approve -> synthesis called, high score."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "Feasible")),
        _mock_proc(_make_review_output("approve", "No risks found")),
        _mock_proc(_make_synthesis_output(0.95, [])),
    ]

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t, p: None, complexity="L"
    )

    assert result.consensus_score == 0.95
    assert result.human_decision_needed is False
    assert len(result.reviews) == 2
    assert result.decision_points == []
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED


# ------------------------------------------------------------------
# Progress callback
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_progress_callback(mock_exec: AsyncMock) -> None:
    """on_progress is called with (completed, total, phase) around each reviewer."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(0.9, [])),
    ]

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    progress_calls: list[tuple[int, int, str]] = []
    await pipeline.review_task(
        _sample_task(),
        "Build the thing",
        lambda c, t, p: progress_calls.append((c, t, p)),
        complexity="M",
    )

    assert progress_calls == [
        (0, 2, "Starting feasibility_and_edge_cases review..."),
        (1, 2, "Completed feasibility_and_edge_cases review"),
        (1, 2, "Starting adversarial_red_team review..."),
        (2, 2, "Completed adversarial_red_team review"),
        (2, 2, "Synthesizing..."),
    ]


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_progress_callback_single_reviewer(mock_exec: AsyncMock) -> None:
    """Progress callback for single reviewer: start + complete phases."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config())

    progress_calls: list[tuple[int, int, str]] = []
    await pipeline.review_task(
        _sample_task(),
        "Plan",
        lambda c, t, p: progress_calls.append((c, t, p)),
        complexity="S",
    )

    assert progress_calls == [
        (0, 1, "Starting feasibility_and_edge_cases review..."),
        (1, 1, "Completed feasibility_and_edge_cases review"),
    ]


# ------------------------------------------------------------------
# Complexity-based reviewer selection
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_s_complexity_skips_optional(mock_exec: AsyncMock) -> None:
    """S complexity only runs required reviewers."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config())

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert len(result.reviews) == 1
    assert mock_exec.call_count == 1


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_m_complexity_includes_optional(mock_exec: AsyncMock) -> None:
    """M complexity includes optional adversarial reviewer."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(0.9, [])),
    ]

    pipeline = ReviewPipeline(_default_config())

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    assert len(result.reviews) == 2
    # 2 reviews + 1 synthesis
    assert mock_exec.call_count == 3


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_l_complexity_includes_optional(mock_exec: AsyncMock) -> None:
    """L complexity also includes optional adversarial reviewer."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(0.9, [])),
    ]

    pipeline = ReviewPipeline(_default_config())

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="L"
    )

    assert len(result.reviews) == 2


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_parse_failure_treated_as_reject(mock_exec: AsyncMock) -> None:
    """Invalid JSON from reviewer -> treated as reject with REJECTED_SINGLE lifecycle."""
    mock_exec.return_value = _mock_proc(
        _make_cli_output("This is not valid JSON")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert result.consensus_score == 0.0
    assert result.human_decision_needed is True
    assert result.reviews[0].verdict == "reject"
    assert result.reviews[0].summary == "This is not valid JSON"
    assert result.lifecycle_state == "rejected_single"


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_synthesis_parse_failure(mock_exec: AsyncMock) -> None:
    """Invalid JSON from synthesis -> default score 0.5, human decision needed."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("reject", "Bad")),
        _mock_proc(_make_cli_output("not json at all")),
    ]

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    assert result.consensus_score == 0.5
    assert result.human_decision_needed is True
    assert len(result.decision_points) == 1
    assert "manual review" in result.decision_points[0].lower()


# ------------------------------------------------------------------
# Prompt building
# ------------------------------------------------------------------


def test_build_review_prompt_feasibility() -> None:
    """Known focus 'feasibility_and_edge_cases' returns specific prompt."""
    pipeline = ReviewPipeline(_default_config())

    prompt = pipeline._build_review_prompt("feasibility_and_edge_cases")

    assert "feasibility" in prompt.lower()
    assert "edge cases" in prompt.lower()


def test_build_review_prompt_adversarial() -> None:
    """Known focus 'adversarial_red_team' returns specific prompt."""
    pipeline = ReviewPipeline(_default_config())

    prompt = pipeline._build_review_prompt("adversarial_red_team")

    assert "adversarial" in prompt.lower()
    assert "vulnerabilities" in prompt.lower()


def test_build_review_prompt_unknown_focus() -> None:
    """Unknown focus area returns the default prompt."""
    pipeline = ReviewPipeline(_default_config())

    prompt = pipeline._build_review_prompt("unknown_focus")

    assert "code reviewer" in prompt.lower()


# ------------------------------------------------------------------
# CLI call content verification
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_reviewer_receives_task_context(mock_exec: AsyncMock) -> None:
    """CLI call includes task title, ID, description, and plan content."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config())
    task = _sample_task()

    await pipeline.review_task(
        task, "My detailed plan", lambda c, t, p: None, complexity="S"
    )

    call_args = mock_exec.call_args.args
    # call_args = ("claude", "-p", prompt, "--system-prompt", system, ...)
    prompt = call_args[2]  # The -p argument value

    assert task.title in prompt
    assert task.id in prompt
    assert "My detailed plan" in prompt


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_reviewer_uses_correct_model(mock_exec: AsyncMock) -> None:
    """CLI call uses the model specified in the reviewer config."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config())

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    call_args = mock_exec.call_args.args
    # Find --model flag and its value
    model_idx = list(call_args).index("--model") + 1
    assert call_args[model_idx] == "claude-sonnet-4-5"


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_active_reviewers_auto_approve() -> None:
    """No reviewers configured for S complexity -> auto-approve."""
    config = ReviewPipelineConfig(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="adversarial",
                api="claude_cli",
                required=False,
            ),
        ],
    )
    pipeline = ReviewPipeline(config)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False
    assert len(result.reviews) == 0
    assert result.rounds_total == 0
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED


@pytest.mark.asyncio
async def test_empty_reviewers_config() -> None:
    """Empty reviewers list -> auto-approve."""
    config = ReviewPipelineConfig(reviewers=[])
    pipeline = ReviewPipeline(config)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_threshold_boundary(mock_exec: AsyncMock) -> None:
    """Score exactly at threshold -> no human decision needed."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(0.8, [])),
    ]

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    # Score 0.8 is NOT < 0.8, so no human decision needed
    assert result.consensus_score == 0.8
    assert result.human_decision_needed is False


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_synthesis_score_clamped(mock_exec: AsyncMock) -> None:
    """Synthesis score > 1.0 is clamped to 1.0."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(1.5, [])),
    ]

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False


# ------------------------------------------------------------------
# raw_response capture
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_raw_response_captured(mock_exec: AsyncMock) -> None:
    """raw_response is structured JSON with model, usage, result, session_id."""
    inner_dict = {
        "verdict": "approve",
        "summary": "Plan looks good",
        "suggestions": [],
    }
    mock_exec.return_value = _mock_proc(_make_review_output("approve", "Plan looks good"))

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t, p: None, complexity="S"
    )

    raw = json.loads(result.reviews[0].raw_response)
    assert "result" in raw
    assert raw["result"] == inner_dict


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_raw_response_captured_on_parse_failure(mock_exec: AsyncMock) -> None:
    """raw_response is captured even when JSON parsing fails (legacy result-as-string)."""
    raw_text = "This is not valid JSON but is the raw reviewer output"
    # Simulate legacy mode: structured_output absent, result is a raw string
    cli_bytes = json.dumps({
        "type": "result", "result": raw_text, "structured_output": None,
    }).encode("utf-8")
    mock_exec.return_value = _mock_proc(cli_bytes)

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    raw = json.loads(result.reviews[0].raw_response)
    assert raw["result"] == raw_text
    assert result.reviews[0].verdict == "reject"


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_raw_response_contains_fields_beyond_result(mock_exec: AsyncMock) -> None:
    """raw_response must contain fields beyond just 'result' (invariant test from AC)."""
    inner_dict = {"verdict": "approve", "summary": "OK", "suggestions": []}
    # CLI output with structured_output + usage + model data
    cli_output = {
        "type": "result",
        "structured_output": inner_dict,
        "result": None,
        "model": "claude-sonnet-4-5",
        "usage": {"input_tokens": 1000, "output_tokens": 500},
        "session_id": "sess-abc123",
    }
    mock_exec.return_value = _mock_proc(
        json.dumps(cli_output).encode("utf-8")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_raw_response_explicit_fields_only(mock_exec: AsyncMock) -> None:
    """raw_response contains only model/usage/result/session_id, not entire CLI blob."""
    inner_dict = {"verdict": "approve", "summary": "OK", "suggestions": []}
    # CLI output with extra fields that should NOT be in raw_response
    cli_output = {
        "type": "result",
        "structured_output": inner_dict,
        "result": None,
        "model": "claude-sonnet-4-5",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "session_id": "sess-xyz",
        "some_internal_field": "should_not_be_stored",
        "num_turns": 1,
    }
    mock_exec.return_value = _mock_proc(
        json.dumps(cli_output).encode("utf-8")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    raw = json.loads(result.reviews[0].raw_response)
    assert set(raw.keys()) == {"model", "usage", "result", "session_id"}


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
# max_budget_usd in CLI args
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_reviewer_uses_max_budget_usd(mock_exec: AsyncMock) -> None:
    """CLI call includes --max-budget-usd when reviewer sets it."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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

    call_args = mock_exec.call_args.args
    budget_idx = list(call_args).index("--max-budget-usd") + 1
    assert call_args[budget_idx] == "2.00"


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_reviewer_default_budget_omits_flag(mock_exec: AsyncMock) -> None:
    """Reviewer without explicit max_budget_usd omits --max-budget-usd flag."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config())

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    call_args = list(mock_exec.call_args.args)
    assert "--max-budget-usd" not in call_args


# ------------------------------------------------------------------
# cost_usd on LLMReview
# ------------------------------------------------------------------


def _make_cli_output_with_usage(
    inner: dict | str,
    input_tokens: int = 500,
    output_tokens: int = 200,
) -> bytes:
    """Create mock Claude CLI stdout with usage data."""
    if isinstance(inner, dict):
        cli_output = {
            "type": "result",
            "structured_output": inner,
            "result": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    else:
        cli_output = {
            "type": "result",
            "result": inner,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    return json.dumps(cli_output).encode("utf-8")


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_cost_usd_captured_on_review(mock_exec: AsyncMock) -> None:
    """cost_usd is populated on LLMReview when usage data is in CLI output."""
    inner = {"verdict": "approve", "summary": "OK", "suggestions": []}
    mock_exec.return_value = _mock_proc(
        _make_cli_output_with_usage(inner, input_tokens=1000, output_tokens=500)
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert result.reviews[0].cost_usd is not None
    assert result.reviews[0].cost_usd > 0


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_cost_usd_none_when_no_usage(mock_exec: AsyncMock) -> None:
    """cost_usd is None when CLI output has no usage data."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert result.reviews[0].cost_usd is None


# ------------------------------------------------------------------
# Process group flags (T-P0-31)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_subprocess_created_with_process_group_flags(mock_exec: AsyncMock) -> None:
    """Subprocess is created with process group isolation flags."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    kwargs = mock_exec.call_args.kwargs
    if sys.platform == "win32":
        assert "creationflags" in kwargs
    else:
        assert kwargs.get("start_new_session") is True


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_subprocess_stdout_stderr_pipes(mock_exec: AsyncMock) -> None:
    """Subprocess is created with PIPE for stdout and stderr."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    kwargs = mock_exec.call_args.kwargs
    assert kwargs["stdout"] == asyncio.subprocess.PIPE
    assert kwargs["stderr"] == asyncio.subprocess.PIPE


# ------------------------------------------------------------------
# Timeout behavior (T-P0-31)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline._kill_review_process")
@patch("src.review_pipeline._terminate_review_process")
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_timeout_kills_process_group(
    mock_exec: AsyncMock,
    mock_terminate: MagicMock,
    mock_kill: MagicMock,
) -> None:
    """When readline() hangs past timeout, process group is terminated and RuntimeError raised."""
    proc = AsyncMock()
    proc.wait = AsyncMock(return_value=0)
    proc.pid = 12345
    proc.kill = MagicMock()

    # Mock stdout.readline that never returns (hangs forever)
    mock_stdout = AsyncMock()

    async def _hang_forever() -> bytes:
        await asyncio.sleep(9999)
        return b""

    mock_stdout.readline = AsyncMock(side_effect=_hang_forever)
    proc.stdout = mock_stdout

    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"")
    proc.stderr = mock_stderr

    mock_exec.return_value = proc

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

    mock_terminate.assert_called_once_with(proc)


@pytest.mark.asyncio
@patch("src.review_pipeline._kill_review_process")
@patch("src.review_pipeline._terminate_review_process")
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_timeout_force_kill_on_stubborn_process(
    mock_exec: AsyncMock,
    mock_terminate: MagicMock,
    mock_kill: MagicMock,
) -> None:
    """When process doesn't exit after SIGTERM, force-kill is used."""
    proc = AsyncMock()
    proc.pid = 12345
    proc.kill = MagicMock()
    # wait() times out after terminate (grace period), then succeeds after kill
    proc.wait = AsyncMock(side_effect=[TimeoutError, 0])

    # Mock stdout.readline that hangs forever
    mock_stdout = AsyncMock()

    async def _hang_forever() -> bytes:
        await asyncio.sleep(9999)
        return b""

    mock_stdout.readline = AsyncMock(side_effect=_hang_forever)
    proc.stdout = mock_stdout

    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"")
    proc.stderr = mock_stderr

    mock_exec.return_value = proc

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

    mock_terminate.assert_called_once_with(proc)
    mock_kill.assert_called_once_with(proc)


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_no_timeout_when_zero(mock_exec: AsyncMock) -> None:
    """When review_timeout_minutes is 0, no timeout is applied."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_normal_completion_within_timeout(mock_exec: AsyncMock) -> None:
    """Normal review completes within timeout -- no error."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
@patch("src.review_pipeline._terminate_review_process")
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_synthesis_timeout_also_covered(
    mock_exec: AsyncMock,
    mock_terminate: MagicMock,
) -> None:
    """Timeout applies to synthesis step too, not just individual reviewers."""
    review_proc_1 = _mock_proc(_make_review_output("approve", "OK"))
    review_proc_2 = _mock_proc(_make_review_output("approve", "OK"))

    # Synthesis subprocess hangs (readline never returns)
    synthesis_proc = AsyncMock()
    synthesis_proc.wait = AsyncMock(return_value=0)
    synthesis_proc.pid = 99999
    synthesis_proc.kill = MagicMock()

    mock_stdout = AsyncMock()

    async def _hang_forever() -> bytes:
        await asyncio.sleep(9999)
        return b""

    mock_stdout.readline = AsyncMock(side_effect=_hang_forever)
    synthesis_proc.stdout = mock_stdout

    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"")
    synthesis_proc.stderr = mock_stderr

    mock_exec.side_effect = [review_proc_1, review_proc_2, synthesis_proc]

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
    # Override to tiny value so test doesn't wait 60 seconds
    pipeline._timeout_minutes = 0.001

    with pytest.raises(RuntimeError, match="timed out"):
        await pipeline.review_task(
            _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
        )

    mock_terminate.assert_called_once_with(synthesis_proc)


# ------------------------------------------------------------------
# review_attempt parameter (T-P0-31)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_review_attempt_passed_to_history_writer(mock_exec: AsyncMock) -> None:
    """review_attempt is forwarded to HistoryWriter.write_review()."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_review_attempt_defaults_to_1(mock_exec: AsyncMock) -> None:
    """review_attempt defaults to 1 when not specified."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_review_attempt_on_multi_reviewer(mock_exec: AsyncMock) -> None:
    """review_attempt is the same for all reviewers in a single pipeline run."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(0.95, [])),
    ]

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_plan_snapshot_stored_on_first_round(mock_exec: AsyncMock) -> None:
    """plan_snapshot is passed to write_review only on the first round (i==0)."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_plan_snapshot_none_on_subsequent_rounds(mock_exec: AsyncMock) -> None:
    """plan_snapshot is None for the second reviewer (round 2) in multi-reviewer."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(0.95, [])),
    ]

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_plan_snapshot_empty_plan(mock_exec: AsyncMock) -> None:
    """Empty plan text is stored as plan_snapshot (not converted to None)."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_plan_snapshot_no_history_writer(mock_exec: AsyncMock) -> None:
    """When no history_writer is configured, pipeline runs without error."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
# _terminate_review_process / _kill_review_process helpers (T-P0-31)
# ------------------------------------------------------------------


def test_terminate_review_process_none_pid() -> None:
    """_terminate_review_process is a no-op when pid is None."""
    proc = MagicMock()
    proc.pid = None
    # Should not raise
    _terminate_review_process(proc)


def test_kill_review_process_none_pid() -> None:
    """_kill_review_process is a no-op when pid is None."""
    proc = MagicMock()
    proc.pid = None
    # Should not raise
    _kill_review_process(proc)


# ------------------------------------------------------------------
# Phase strings in on_progress (T-P0-32)
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_phase_string_starting(mock_exec: AsyncMock) -> None:
    """First phase call is 'Starting {focus} review...'."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    phases: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan",
        lambda _c, _t, p: phases.append(p),
        complexity="S",
    )

    assert phases[0] == "Starting feasibility_and_edge_cases review..."


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_phase_string_completed(mock_exec: AsyncMock) -> None:
    """Second phase call is 'Completed {focus} review'."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    phases: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan",
        lambda _c, _t, p: phases.append(p),
        complexity="S",
    )

    assert phases[1] == "Completed feasibility_and_edge_cases review"


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_phase_synthesizing_emitted(mock_exec: AsyncMock) -> None:
    """'Synthesizing...' phase is emitted before multi-review synthesis."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("reject", "Bad")),
        _mock_proc(_make_synthesis_output(0.7, [])),
    ]

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    phases: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan",
        lambda _c, _t, p: phases.append(p),
        complexity="M",
    )

    assert "Synthesizing..." in phases


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_no_synthesizing_for_single_reviewer(mock_exec: AsyncMock) -> None:
    """'Synthesizing...' phase is NOT emitted for single-reviewer runs."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_human_feedback_injected_into_prompt(mock_exec: AsyncMock) -> None:
    """When human_feedback is provided, it should appear in the CLI prompt."""
    captured_args: list[tuple] = []

    async def _capture_exec(*args, **kwargs):
        captured_args.append(args)
        return _mock_proc(_make_review_output("approve", "OK"))

    mock_exec.side_effect = _capture_exec

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

    # The first captured call should have the prompt with feedback
    assert len(captured_args) >= 1
    # args[0] is "claude", args[1] is "-p", args[2] is the prompt
    prompt = captured_args[0][2]
    assert "Previous human feedback" in prompt
    assert "Add timeout handling" in prompt
    assert "Also fix error path" in prompt
    assert "[Attempt 1] REQUEST_CHANGES" in prompt
    assert "[Attempt 2] REQUEST_CHANGES" in prompt


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_no_feedback_no_injection(mock_exec: AsyncMock) -> None:
    """When no human_feedback is provided, prompt should not contain feedback section."""
    captured_args: list[tuple] = []

    async def _capture_exec(*args, **kwargs):
        captured_args.append(args)
        return _mock_proc(_make_review_output("approve", "OK"))

    mock_exec.side_effect = _capture_exec

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    await pipeline.review_task(
        _sample_task(), "Build the thing",
        lambda c, t, p: None,
        complexity="S",
        human_feedback=None,
    )

    assert len(captured_args) >= 1
    prompt = captured_args[0][2]
    assert "Previous human feedback" not in prompt


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_empty_feedback_list_no_injection(mock_exec: AsyncMock) -> None:
    """Empty feedback list should not inject feedback section."""
    captured_args: list[tuple] = []

    async def _capture_exec(*args, **kwargs):
        captured_args.append(args)
        return _mock_proc(_make_review_output("approve", "OK"))

    mock_exec.side_effect = _capture_exec

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    await pipeline.review_task(
        _sample_task(), "Build the thing",
        lambda c, t, p: None,
        complexity="S",
        human_feedback=[],
    )

    assert len(captured_args) >= 1
    prompt = captured_args[0][2]
    assert "Previous human feedback" not in prompt


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_feedback_without_reason(mock_exec: AsyncMock) -> None:
    """Feedback entry with empty reason includes only the decision."""
    captured_args: list[tuple] = []

    async def _capture_exec(*args, **kwargs):
        captured_args.append(args)
        return _mock_proc(_make_review_output("approve", "OK"))

    mock_exec.side_effect = _capture_exec

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

    prompt = captured_args[0][2]
    assert "Previous human feedback" in prompt
    assert "[Attempt 1] REJECT" in prompt


# ------------------------------------------------------------------
# ReviewLifecycleState emission tests (T-P0-41)
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


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_lifecycle_state_on_review_state_approve(mock_exec: AsyncMock) -> None:
    """ReviewState.lifecycle_state is APPROVED on approval."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )
    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_lifecycle_state_on_review_state_rejected_single(mock_exec: AsyncMock) -> None:
    """ReviewState.lifecycle_state is REJECTED_SINGLE on single reject."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("reject", "Bad")
    )
    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )
    assert result.lifecycle_state == ReviewLifecycleState.REJECTED_SINGLE


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_lifecycle_state_on_review_state_rejected_consensus(
    mock_exec: AsyncMock,
) -> None:
    """ReviewState.lifecycle_state is REJECTED_CONSENSUS when multi-reviewer score < threshold."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("reject", "Bad")),
        _mock_proc(_make_synthesis_output(0.4, ["major issues"])),
    ]
    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M",
    )
    assert result.lifecycle_state == ReviewLifecycleState.REJECTED_CONSENSUS


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_lifecycle_state_passed_to_history_writer(mock_exec: AsyncMock) -> None:
    """lifecycle_state is forwarded to HistoryWriter.write_review() for each entry."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(0.95, [])),
    ]

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_lifecycle_single_reviewer_history_writer(mock_exec: AsyncMock) -> None:
    """Single reviewer: the only entry gets the terminal lifecycle state."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("reject", "Issues found")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_single_reviewer_reject_score_is_zero(mock_exec: AsyncMock) -> None:
    """Single reviewer reject score is 0.0 (not legacy 0.3)."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("reject", "Bad plan")
    )
    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S",
    )
    assert result.consensus_score == 0.0


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_no_reviewers_lifecycle_is_approved(mock_exec: AsyncMock) -> None:
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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_on_log_receives_lifecycle_messages(mock_exec: AsyncMock) -> None:
    """on_log callback receives review lifecycle messages (started, phase, completed)."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

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
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_on_log_receives_cli_output_lines(mock_exec: AsyncMock) -> None:
    """on_log receives simplified stream events including [DONE] for result."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "Looks good", ["Minor nit"])
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    log_lines: list[str] = []
    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
        on_log=lambda line: log_lines.append(line),
    )

    # With stream-json, result event is simplified to [DONE]
    done_lines = [line for line in log_lines if "[DONE]" in line]
    assert len(done_lines) >= 1


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_on_log_none_does_not_crash(mock_exec: AsyncMock) -> None:
    """Passing on_log=None (default) does not raise errors."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    # on_log=None is the default -- should work without errors
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
    )

    assert result.consensus_score == 1.0


# ------------------------------------------------------------------
# Blank-line preservation and persist-first tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_call_claude_cli_preserves_blank_lines(mock_exec: AsyncMock) -> None:
    """Blank lines in CLI output are preserved for JSON reassembly."""
    review_json = _make_review_output("approve", "Looks good")

    # Build raw lines with blank lines interspersed
    raw_lines = [
        review_json[:20] + b"\n",
        b"\n",  # blank line
        review_json[20:] + b"\n",
        b"",  # EOF
    ]
    proc = AsyncMock()
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=raw_lines)
    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"")
    proc.stdout = mock_stdout
    proc.stderr = mock_stderr
    mock_exec.return_value = proc

    artifact_content: list[str] = []

    async def capture_artifact(content: str) -> None:
        artifact_content.append(content)

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    cli_output = await pipeline._call_claude_cli(
        prompt="test",
        system_prompt="test",
        model="claude-sonnet-4-5",
        on_raw_artifact=capture_artifact,
    )

    # Raw artifact should contain blank lines
    assert len(artifact_content) == 1
    assert "\n\n" in artifact_content[0]  # blank line preserved

    # Should still parse (last-line fallback may kick in)
    assert isinstance(cli_output, dict)


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_raw_artifact_persisted_even_on_json_failure(
    mock_exec: AsyncMock,
) -> None:
    """on_raw_artifact is called even when CLI returns garbage (non-JSON)."""
    # CLI returns garbage text, not valid JSON
    raw_lines = [
        b"Some random garbage output\n",
        b"More garbage\n",
        b"",  # EOF
    ]
    proc = AsyncMock()
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=raw_lines)
    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"")
    proc.stdout = mock_stdout
    proc.stderr = mock_stderr
    mock_exec.return_value = proc

    artifact_content: list[str] = []

    async def capture_artifact(content: str) -> None:
        artifact_content.append(content)

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    cli_output = await pipeline._call_claude_cli(
        prompt="test",
        system_prompt="test",
        model="claude-sonnet-4-5",
        on_raw_artifact=capture_artifact,
    )

    # on_raw_artifact MUST have been called even though output is garbage
    assert len(artifact_content) == 1
    assert "Some random garbage output" in artifact_content[0]

    # cli_output should be empty dict (fallback)
    assert cli_output == {}


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_raw_artifact_persisted_on_nonzero_exit(
    mock_exec: AsyncMock,
) -> None:
    """on_raw_artifact is called BEFORE RuntimeError is raised on non-zero exit."""
    raw_lines = [
        b"partial output before crash\n",
        b"",  # EOF
    ]
    proc = AsyncMock()
    proc.returncode = 1  # non-zero exit
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=raw_lines)
    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"CLI crashed")
    proc.stdout = mock_stdout
    proc.stderr = mock_stderr
    mock_exec.return_value = proc

    artifact_content: list[str] = []

    async def capture_artifact(content: str) -> None:
        artifact_content.append(content)

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    with pytest.raises(RuntimeError, match="Claude CLI failed"):
        await pipeline._call_claude_cli(
            prompt="test",
            system_prompt="test",
            model="claude-sonnet-4-5",
            on_raw_artifact=capture_artifact,
        )

    # on_raw_artifact MUST have been called BEFORE the RuntimeError
    assert len(artifact_content) == 1
    assert "partial output before crash" in artifact_content[0]


# ------------------------------------------------------------------
# Unit tests: Pydantic validation models for review/synthesis
# ------------------------------------------------------------------


class TestReviewResultModel:
    """Tests for ReviewResult Pydantic validation."""

    def test_valid_approve(self) -> None:
        """Valid approve review passes validation."""
        result = ReviewResult.model_validate({
            "verdict": "approve",
            "summary": "Looks good",
            "suggestions": ["Minor nit"],
        })
        assert result.verdict == "approve"
        assert result.summary == "Looks good"

    def test_valid_reject(self) -> None:
        """Valid reject review passes validation."""
        result = ReviewResult.model_validate({
            "verdict": "reject",
            "summary": "Needs work",
            "suggestions": [],
        })
        assert result.verdict == "reject"

    def test_invalid_verdict_rejected(self) -> None:
        """Invalid verdict enum value is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="verdict"):
            ReviewResult.model_validate({
                "verdict": "maybe",
                "summary": "s",
                "suggestions": [],
            })

    def test_missing_summary_rejected(self) -> None:
        """Missing required summary field is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="summary"):
            ReviewResult.model_validate({
                "verdict": "approve",
                "suggestions": [],
            })


class TestSynthesisResultModel:
    """Tests for SynthesisResult Pydantic validation."""

    def test_valid_synthesis(self) -> None:
        """Valid synthesis data passes validation."""
        result = SynthesisResult.model_validate({
            "score": 0.85,
            "disagreements": ["Minor issue"],
        })
        assert result.score == 0.85
        assert result.disagreements == ["Minor issue"]

    def test_missing_score_rejected(self) -> None:
        """Missing required score field is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="score"):
            SynthesisResult.model_validate({"disagreements": []})

    def test_string_score_coerced(self) -> None:
        """Pydantic coerces numeric strings to float."""
        result = SynthesisResult.model_validate({
            "score": "0.9",
            "disagreements": [],
        })
        assert result.score == 0.9


class TestParseReviewWithValidation:
    """Tests that _parse_review uses Pydantic and logs raw content."""

    def test_invalid_verdict_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Invalid verdict triggers Pydantic rejection and logs raw content."""
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
        assert "maybe" in caplog.text

    def test_malformed_json_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed JSON logs raw content."""
        pipeline = ReviewPipeline(_default_config())
        reviewer = ReviewerConfig(model="claude-sonnet-4-5", focus="test")
        with caplog.at_level("WARNING"):
            review = pipeline._parse_review("not json!", reviewer)
        assert review.verdict == "reject"
        assert "Raw" in caplog.text
        assert "not json!" in caplog.text


class TestParseSynthesisWithValidation:
    """Tests that _parse_synthesis uses Pydantic and logs raw content."""

    def test_malformed_json_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed JSON logs raw content."""
        pipeline = ReviewPipeline(_default_config())
        with caplog.at_level("WARNING"):
            result = pipeline._parse_synthesis("bad json!!!")
        assert result.score == 0.5  # default fallback
        assert "Raw" in caplog.text
        assert "bad json" in caplog.text

    def test_missing_score_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing required field triggers Pydantic rejection."""
        text = json.dumps({"disagreements": ["issue"]})
        pipeline = ReviewPipeline(_default_config())
        with caplog.at_level("WARNING"):
            result = pipeline._parse_synthesis(text)
        assert result.score == 0.5  # default fallback
        assert "Raw" in caplog.text


# ------------------------------------------------------------------
# T-P0-94: Stream-json for review pipeline tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_on_stream_event_receives_parsed_events(mock_exec: AsyncMock) -> None:
    """on_stream_event callback receives parsed stream-json events."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "Looks good")
    )

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
        "verdict": "approve", "summary": "Looks good", "suggestions": [],
    }


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_stream_json_cli_args_used(mock_exec: AsyncMock) -> None:
    """CLI is invoked with --output-format stream-json --verbose."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
    )

    call_args = mock_exec.call_args[0]
    assert "--output-format" in call_args
    fmt_idx = list(call_args).index("--output-format")
    assert call_args[fmt_idx + 1] == "stream-json"
    assert "--verbose" in call_args


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_stream_json_multi_event_parsing(mock_exec: AsyncMock) -> None:
    """Multiple stream events are parsed correctly (init + assistant + result)."""
    init_event = json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-4-5"})
    assistant_event = json.dumps({
        "type": "assistant",
        "content": [{"type": "text", "text": "Reviewing the plan..."}],
    })
    result_event = json.dumps({
        "type": "result",
        "structured_output": {"verdict": "approve", "summary": "OK", "suggestions": []},
        "result": None,
    })

    raw = (init_event + "\n" + assistant_event + "\n" + result_event + "\n").encode("utf-8")

    proc = AsyncMock()
    proc.returncode = 0
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    lines = raw.split(b"\n")
    line_queue = [line + b"\n" for line in lines if line] + [b""]
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=line_queue)
    mock_stderr = AsyncMock()
    mock_stderr.read = AsyncMock(return_value=b"")
    proc.stdout = mock_stdout
    proc.stderr = mock_stderr
    mock_exec.return_value = proc

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    stream_events: list[dict] = []
    log_lines: list[str] = []
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
        on_log=lambda line: log_lines.append(line),
        on_stream_event=lambda ev: stream_events.append(ev),
    )

    # All 3 events should be received
    assert len(stream_events) >= 3
    types = [e.get("type") for e in stream_events]
    assert "system" in types
    assert "assistant" in types
    assert "result" in types

    # on_log should contain simplified text
    assert any("[INIT]" in line for line in log_lines)
    assert any("Reviewing the plan" in line for line in log_lines)
    assert any("[DONE]" in line for line in log_lines)

    # Result should still be correctly parsed
    assert result.consensus_score == 1.0


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_on_stream_event_none_does_not_crash(mock_exec: AsyncMock) -> None:
    """Passing on_stream_event=None (default) does not raise errors."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
    )

    assert result.consensus_score == 1.0


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_jsonl_persistence_with_stream_log_dir(
    mock_exec: AsyncMock, tmp_path: Path,
) -> None:
    """JSONL stream logs are written when stream_log_dir is set."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(
        _default_config(), threshold=0.8, stream_log_dir=tmp_path,
    )

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None,
        complexity="S",
    )

    # Check JSONL files were created
    task_dir = tmp_path / _sample_task().id.replace(":", "_")
    assert task_dir.is_dir()

    jsonl_files = list(task_dir.glob("review_stream_*.jsonl"))
    assert len(jsonl_files) >= 1

    # JSONL should contain at least the result event
    content = jsonl_files[0].read_text(encoding="utf-8")
    events = [json.loads(line) for line in content.strip().split("\n") if line.strip()]
    assert len(events) >= 1
    assert any(e.get("type") == "result" for e in events)

    # Raw log files too
    raw_files = list(task_dir.glob("review_raw_*.log"))
    assert len(raw_files) >= 1
