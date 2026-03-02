"""Tests for the ReviewPipeline.

Uses mock Anthropic clients to test approve, reject, disagree, progress
callback, synthesis, and error-handling scenarios.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.models import ExecutorType, Task, TaskStatus
from src.review_pipeline import ReviewPipeline

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_response(text: str) -> MagicMock:
    """Create a mock Anthropic response with the given text."""
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _make_json_response(
    verdict: str,
    summary: str,
    suggestions: list[str] | None = None,
) -> MagicMock:
    """Create a mock Anthropic response with JSON review data."""
    data = {
        "verdict": verdict,
        "summary": summary,
        "suggestions": suggestions or [],
    }
    return _make_response(json.dumps(data))


def _make_synthesis_response(
    score: float,
    disagreements: list[str] | None = None,
) -> MagicMock:
    """Create a mock Anthropic response with synthesis JSON."""
    data = {
        "score": score,
        "disagreements": disagreements or [],
    }
    return _make_response(json.dumps(data))


def _default_config() -> ReviewPipelineConfig:
    """Create a default review pipeline config with 1 required + 1 optional."""
    return ReviewPipelineConfig(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                api="anthropic",
                required=True,
            ),
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="adversarial_red_team",
                api="anthropic",
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


def _mock_client() -> MagicMock:
    """Create a mock Anthropic client with async messages.create."""
    client = MagicMock()
    client.messages = MagicMock()
    client.messages.create = AsyncMock()
    return client


# ------------------------------------------------------------------
# Single-reviewer tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_reviewer_approve() -> None:
    """Single required reviewer approves -> score 1.0, no human decision."""
    client = _mock_client()
    client.messages.create.return_value = _make_json_response(
        "approve", "Plan looks good", []
    )

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

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
    assert client.messages.create.call_count == 1


@pytest.mark.asyncio
async def test_single_reviewer_reject() -> None:
    """Single required reviewer rejects -> score 0.3, human decision needed."""
    client = _mock_client()
    client.messages.create.return_value = _make_json_response(
        "reject", "Plan has issues", ["Fix error handling", "Add tests"]
    )

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

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
async def test_multi_reviewer_disagree() -> None:
    """Two reviewers disagree -> synthesis called, score from synthesis."""
    client = _mock_client()
    approve_resp = _make_json_response("approve", "Looks feasible")
    reject_resp = _make_json_response("reject", "Security risk", ["Add auth check"])
    synth_resp = _make_synthesis_response(0.65, ["Security concerns"])

    client.messages.create.side_effect = [approve_resp, reject_resp, synth_resp]

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t: None, complexity="M"
    )

    assert result.consensus_score == 0.65
    assert result.human_decision_needed is True
    assert len(result.reviews) == 2
    assert result.decision_points == ["Security concerns"]
    # 2 review calls + 1 synthesis call = 3 total
    assert client.messages.create.call_count == 3


@pytest.mark.asyncio
async def test_multi_reviewer_agree() -> None:
    """Two reviewers both approve -> synthesis called, high score."""
    client = _mock_client()
    approve1 = _make_json_response("approve", "Feasible")
    approve2 = _make_json_response("approve", "No risks found")
    synth_resp = _make_synthesis_response(0.95, [])

    client.messages.create.side_effect = [approve1, approve2, synth_resp]

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

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
async def test_progress_callback() -> None:
    """on_progress is called with (completed, total) after each reviewer."""
    client = _mock_client()
    approve_resp = _make_json_response("approve", "OK")
    synth_resp = _make_synthesis_response(0.9, [])
    client.messages.create.side_effect = [approve_resp, approve_resp, synth_resp]

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

    progress_calls: list[tuple[int, int]] = []
    await pipeline.review_task(
        _sample_task(),
        "Build the thing",
        lambda c, t: progress_calls.append((c, t)),
        complexity="M",
    )

    assert progress_calls == [(1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_progress_callback_single_reviewer() -> None:
    """Progress callback for single reviewer shows (1, 1)."""
    client = _mock_client()
    client.messages.create.return_value = _make_json_response("approve", "OK")

    pipeline = ReviewPipeline(_default_config(), client)

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
async def test_s_complexity_skips_optional() -> None:
    """S complexity only runs required reviewers."""
    client = _mock_client()
    client.messages.create.return_value = _make_json_response("approve", "OK")

    pipeline = ReviewPipeline(_default_config(), client)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
    )

    assert len(result.reviews) == 1
    assert client.messages.create.call_count == 1


@pytest.mark.asyncio
async def test_m_complexity_includes_optional() -> None:
    """M complexity includes optional adversarial reviewer."""
    client = _mock_client()
    approve_resp = _make_json_response("approve", "OK")
    synth_resp = _make_synthesis_response(0.9, [])
    client.messages.create.side_effect = [approve_resp, approve_resp, synth_resp]

    pipeline = ReviewPipeline(_default_config(), client)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
    )

    assert len(result.reviews) == 2
    # 2 reviews + 1 synthesis
    assert client.messages.create.call_count == 3


@pytest.mark.asyncio
async def test_l_complexity_includes_optional() -> None:
    """L complexity also includes optional adversarial reviewer."""
    client = _mock_client()
    approve_resp = _make_json_response("approve", "OK")
    synth_resp = _make_synthesis_response(0.9, [])
    client.messages.create.side_effect = [approve_resp, approve_resp, synth_resp]

    pipeline = ReviewPipeline(_default_config(), client)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="L"
    )

    assert len(result.reviews) == 2


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_failure_treated_as_reject() -> None:
    """Invalid JSON from reviewer -> treated as reject."""
    client = _mock_client()
    client.messages.create.return_value = _make_response("This is not valid JSON")

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
    )

    assert result.consensus_score == 0.3
    assert result.human_decision_needed is True
    assert result.reviews[0].verdict == "reject"
    assert result.reviews[0].summary == "This is not valid JSON"


@pytest.mark.asyncio
async def test_synthesis_parse_failure() -> None:
    """Invalid JSON from synthesis -> default score 0.5, human decision needed."""
    client = _mock_client()
    approve_resp = _make_json_response("approve", "OK")
    reject_resp = _make_json_response("reject", "Bad")
    bad_synth = _make_response("not json at all")

    client.messages.create.side_effect = [approve_resp, reject_resp, bad_synth]

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

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
    pipeline = ReviewPipeline(_default_config(), _mock_client())

    prompt = pipeline._build_review_prompt("feasibility_and_edge_cases")

    assert "feasibility" in prompt.lower()
    assert "edge cases" in prompt.lower()


def test_build_review_prompt_adversarial() -> None:
    """Known focus 'adversarial_red_team' returns specific prompt."""
    pipeline = ReviewPipeline(_default_config(), _mock_client())

    prompt = pipeline._build_review_prompt("adversarial_red_team")

    assert "adversarial" in prompt.lower()
    assert "vulnerabilities" in prompt.lower()


def test_build_review_prompt_unknown_focus() -> None:
    """Unknown focus area returns the default prompt."""
    pipeline = ReviewPipeline(_default_config(), _mock_client())

    prompt = pipeline._build_review_prompt("unknown_focus")

    assert "code reviewer" in prompt.lower()


# ------------------------------------------------------------------
# API call content verification
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_receives_task_context() -> None:
    """API call includes task title, ID, description, and plan content."""
    client = _mock_client()
    client.messages.create.return_value = _make_json_response("approve", "OK")

    pipeline = ReviewPipeline(_default_config(), client)
    task = _sample_task()

    await pipeline.review_task(
        task, "My detailed plan", lambda c, t: None, complexity="S"
    )

    call_kwargs = client.messages.create.call_args
    messages = call_kwargs.kwargs["messages"]
    user_msg = messages[0]["content"]

    assert task.title in user_msg
    assert task.id in user_msg
    assert "My detailed plan" in user_msg


@pytest.mark.asyncio
async def test_reviewer_uses_correct_model() -> None:
    """API call uses the model specified in the reviewer config."""
    client = _mock_client()
    client.messages.create.return_value = _make_json_response("approve", "OK")

    pipeline = ReviewPipeline(_default_config(), client)

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
    )

    call_kwargs = client.messages.create.call_args
    assert call_kwargs.kwargs["model"] == "claude-sonnet-4-5"


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
                api="anthropic",
                required=False,
            ),
        ],
    )
    client = _mock_client()
    pipeline = ReviewPipeline(config, client)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="S"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False
    assert len(result.reviews) == 0
    assert result.rounds_total == 0
    # No API calls made
    assert client.messages.create.call_count == 0


@pytest.mark.asyncio
async def test_empty_reviewers_config() -> None:
    """Empty reviewers list -> auto-approve."""
    config = ReviewPipelineConfig(reviewers=[])
    client = _mock_client()
    pipeline = ReviewPipeline(config, client)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False
    assert client.messages.create.call_count == 0


@pytest.mark.asyncio
async def test_threshold_boundary() -> None:
    """Score exactly at threshold -> no human decision needed."""
    client = _mock_client()
    approve1 = _make_json_response("approve", "OK")
    approve2 = _make_json_response("approve", "OK")
    # Synthesis returns score exactly at threshold
    synth_resp = _make_synthesis_response(0.8, [])

    client.messages.create.side_effect = [approve1, approve2, synth_resp]

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
    )

    # Score 0.8 is NOT < 0.8, so no human decision needed
    assert result.consensus_score == 0.8
    assert result.human_decision_needed is False


@pytest.mark.asyncio
async def test_synthesis_score_clamped() -> None:
    """Synthesis score > 1.0 is clamped to 1.0."""
    client = _mock_client()
    approve1 = _make_json_response("approve", "OK")
    approve2 = _make_json_response("approve", "OK")
    # Synthesis returns out-of-range score
    synth_resp = _make_synthesis_response(1.5, [])

    client.messages.create.side_effect = [approve1, approve2, synth_resp]

    pipeline = ReviewPipeline(_default_config(), client, threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t: None, complexity="M"
    )

    assert result.consensus_score == 1.0
    assert result.human_decision_needed is False
