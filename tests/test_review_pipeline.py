"""Tests for the ReviewPipeline.

Uses subprocess mocking (patching asyncio.create_subprocess_exec) to test
approve, reject, disagree, progress callback, synthesis, and error-handling
scenarios. All LLM calls go through the Claude CLI subprocess.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.models import ExecutorType, Task, TaskStatus
from src.review_pipeline import (
    MAX_RAW_RESPONSE_BYTES,
    ReviewPipeline,
    _truncate_raw_response,
)

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_cli_output(inner_json: str) -> bytes:
    """Create mock Claude CLI stdout bytes wrapping an inner JSON string.

    The Claude CLI with ``--output-format json`` returns a JSON object
    with a ``result`` field containing the LLM response text.
    """
    cli_output = {"type": "result", "result": inner_json}
    return json.dumps(cli_output).encode("utf-8")


def _make_review_output(
    verdict: str,
    summary: str,
    suggestions: list[str] | None = None,
) -> bytes:
    """Create mock Claude CLI stdout for a review response."""
    inner = json.dumps({
        "verdict": verdict,
        "summary": summary,
        "suggestions": suggestions or [],
    })
    return _make_cli_output(inner)


def _make_synthesis_output(
    score: float,
    disagreements: list[str] | None = None,
) -> bytes:
    """Create mock Claude CLI stdout for a synthesis response."""
    inner = json.dumps({
        "score": score,
        "disagreements": disagreements or [],
    })
    return _make_cli_output(inner)


def _mock_proc(stdout: bytes, returncode: int = 0) -> AsyncMock:
    """Create a mock subprocess with given stdout and return code."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
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

    progress_calls: list[tuple[int, int]] = []
    result = await pipeline.review_task(
        _sample_task(),
        "Build the thing",
        lambda c, t: progress_calls.append((c, t)),
        complexity="S",
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False
    assert len(result.reviews) == 1
    assert result.reviews[0].verdict == "approve"
    assert result.rounds_total == 1
    assert result.rounds_completed == 1
    # Only required reviewer called for S complexity
    assert mock_exec.call_count == 1


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_single_reviewer_reject(mock_exec: AsyncMock) -> None:
    """Single required reviewer rejects -> score 0.3, human decision needed."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("reject", "Plan has issues", ["Fix error handling", "Add tests"])
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t: None, complexity="S"
    )

    assert result.consensus_score == 0.3
    assert result.human_decision_needed is True
    assert len(result.reviews) == 1
    assert result.reviews[0].verdict == "reject"
    assert result.decision_points == ["Fix error handling", "Add tests"]


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
        _sample_task(), "Build the thing", lambda c, t: None, complexity="M"
    )

    assert result.consensus_score == 0.65
    assert result.human_decision_needed is True
    assert len(result.reviews) == 2
    assert result.decision_points == ["Security concerns"]
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
        _sample_task(), "Build the thing", lambda c, t: None, complexity="L"
    )

    assert result.consensus_score == 0.95
    assert result.human_decision_needed is False
    assert len(result.reviews) == 2
    assert result.decision_points == []


# ------------------------------------------------------------------
# Progress callback
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_progress_callback(mock_exec: AsyncMock) -> None:
    """on_progress is called with (completed, total) after each reviewer."""
    mock_exec.side_effect = [
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_review_output("approve", "OK")),
        _mock_proc(_make_synthesis_output(0.9, [])),
    ]

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    progress_calls: list[tuple[int, int]] = []
    await pipeline.review_task(
        _sample_task(),
        "Build the thing",
        lambda c, t: progress_calls.append((c, t)),
        complexity="M",
    )

    assert progress_calls == [(1, 2), (2, 2)]


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_progress_callback_single_reviewer(mock_exec: AsyncMock) -> None:
    """Progress callback for single reviewer shows (1, 1)."""
    mock_exec.return_value = _mock_proc(
        _make_review_output("approve", "OK")
    )

    pipeline = ReviewPipeline(_default_config())

    progress_calls: list[tuple[int, int]] = []
    await pipeline.review_task(
        _sample_task(),
        "Plan",
        lambda c, t: progress_calls.append((c, t)),
        complexity="S",
    )

    assert progress_calls == [(1, 1)]


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
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
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
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
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
        _sample_task(), "Plan", lambda c, t: None, complexity="L"
    )

    assert len(result.reviews) == 2


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_parse_failure_treated_as_reject(mock_exec: AsyncMock) -> None:
    """Invalid JSON from reviewer -> treated as reject."""
    mock_exec.return_value = _mock_proc(
        _make_cli_output("This is not valid JSON")
    )

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
    )

    assert result.consensus_score == 0.3
    assert result.human_decision_needed is True
    assert result.reviews[0].verdict == "reject"
    assert result.reviews[0].summary == "This is not valid JSON"


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
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
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
        task, "My detailed plan", lambda c, t: None, complexity="S"
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
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
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
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False
    assert len(result.reviews) == 0
    assert result.rounds_total == 0


@pytest.mark.asyncio
async def test_empty_reviewers_config() -> None:
    """Empty reviewers list -> auto-approve."""
    config = ReviewPipelineConfig(reviewers=[])
    pipeline = ReviewPipeline(config)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False


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
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
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
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False


# ------------------------------------------------------------------
# raw_response capture
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_raw_response_captured(mock_exec: AsyncMock) -> None:
    """raw_response is captured on each LLMReview from the CLI result text."""
    inner_json = json.dumps({
        "verdict": "approve",
        "summary": "Plan looks good",
        "suggestions": [],
    })
    mock_exec.return_value = _mock_proc(_make_cli_output(inner_json))

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t: None, complexity="S"
    )

    assert result.reviews[0].raw_response == inner_json


@pytest.mark.asyncio
@patch("src.review_pipeline.asyncio.create_subprocess_exec")
async def test_raw_response_captured_on_parse_failure(mock_exec: AsyncMock) -> None:
    """raw_response is captured even when JSON parsing fails."""
    raw_text = "This is not valid JSON but is the raw reviewer output"
    mock_exec.return_value = _mock_proc(_make_cli_output(raw_text))

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)
    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
    )

    assert result.reviews[0].raw_response == raw_text
    assert result.reviews[0].verdict == "reject"


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
