"""Tests for the ReviewPipeline.

Uses SDK adapter mocking (patching run_claude_query) to test approve, reject,
disagree, progress callback, synthesis, and error-handling scenarios.
All LLM calls go through the Claude Agent SDK via ``sdk_adapter``.

Migrated from subprocess mocking to SDK mocking in T-P1-89.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.models import ExecutorType, ReviewLifecycleState, Task, TaskStatus
from src.review_pipeline import (
    MAX_RAW_RESPONSE_BYTES,
    ReviewPipeline,
    ReviewResult,
    SynthesisResult,
    _extract_conversation_summary,
    _extract_cost_usd,
    _truncate_raw_response,
)
from src.sdk_adapter import ClaudeEvent, ClaudeEventType

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_review_events(
    verdict: str,
    summary: str,
    suggestions: list[str] | None = None,
    model: str = "claude-sonnet-4-5",
    usage: dict[str, int] | None = None,
    session_id: str | None = None,
    cost_usd: float | None = None,
    blocking_issues: list[dict] | None = None,
) -> list[ClaudeEvent]:
    """Create a list of ClaudeEvent objects simulating a review response.

    Generates INIT + TEXT + RESULT events, matching what the SDK adapter
    would produce for a simple structured-output query.

    The LLM now returns {blocking_issues, suggestions, pass} schema.
    ``verdict`` param maps to ``pass``: "approve" -> true, "reject" -> false.
    """
    pass_value = verdict == "approve"
    if blocking_issues is None:
        blocking_issues = [{"issue": summary, "severity": "high"}] if verdict == "reject" else []
    inner = {
        "blocking_issues": blocking_issues,
        "suggestions": suggestions or [],
        "pass": pass_value,
    }
    events = [
        ClaudeEvent(
            type=ClaudeEventType.INIT,
            session_id=session_id or "sess-test",
        ),
        ClaudeEvent(
            type=ClaudeEventType.TEXT,
            text=f"Reviewing: {summary}",
            model=model,
        ),
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output=inner,
            result_text=None,
            model=model,
            usage=usage,
            session_id=session_id or "sess-test",
            cost_usd=cost_usd,
        ),
    ]
    return events


def _make_synthesis_events(
    score: float,
    disagreements: list[str] | None = None,
    model: str = "claude-sonnet-4-5",
) -> list[ClaudeEvent]:
    """Create ClaudeEvent objects simulating a synthesis response."""
    inner = {
        "score": score,
        "disagreements": disagreements or [],
    }
    return [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-synth"),
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output=inner,
            result_text=None,
            model=model,
        ),
    ]


def _make_error_events(error_message: str = "SDK error") -> list[ClaudeEvent]:
    """Create ClaudeEvent objects simulating an SDK error."""
    return [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-err"),
        ClaudeEvent(
            type=ClaudeEventType.ERROR,
            error_message=error_message,
        ),
    ]


async def _mock_run_claude_query_from_events(
    events: list[ClaudeEvent],
):
    """Create an async generator that yields events from a list."""
    for event in events:
        yield event


def _setup_mock_query(
    mock_query: MagicMock,
    event_sequences: list[list[ClaudeEvent]],
) -> None:
    """Configure mock run_claude_query to yield events from sequences.

    Each call to run_claude_query() will return the next sequence of events.
    """
    call_count = 0

    def _side_effect(prompt: str, options: Any = None):
        nonlocal call_count
        idx = min(call_count, len(event_sequences) - 1)
        call_count += 1
        return _mock_run_claude_query_from_events(event_sequences[idx])

    mock_query.side_effect = _side_effect


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
@patch("src.review_pipeline.run_claude_query")
async def test_single_reviewer_approve(mock_query: MagicMock) -> None:
    """Single required reviewer approves -> score 1.0, no human decision."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Plan looks good"),
    ])

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
    assert mock_query.call_count == 1


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_single_reviewer_reject(mock_query: MagicMock) -> None:
    """Single required reviewer rejects -> score 0.0, REJECTED_SINGLE lifecycle."""
    _setup_mock_query(mock_query, [
        _make_review_events(
            "reject", "Plan has issues", ["Fix error handling", "Add tests"],
            blocking_issues=[
                {"issue": "Fix error handling", "severity": "high"},
                {"issue": "Add tests", "severity": "medium"},
            ],
        ),
    ])

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
@patch("src.review_pipeline.run_claude_query")
async def test_multi_reviewer_disagree(mock_query: MagicMock) -> None:
    """Two reviewers disagree -> deterministic score, no synthesis call."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks feasible"),
        _make_review_events("reject", "Security risk", ["Add auth check"]),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t, p: None, complexity="M"
    )

    assert result.consensus_score == 0.5  # 1 approve / 2 total
    assert result.human_decision_needed is True
    assert len(result.reviews) == 2
    # Blocking issues from the rejecting reviewer
    assert "Security risk" in result.decision_points
    assert result.lifecycle_state == ReviewLifecycleState.REJECTED_CONSENSUS
    # 2 review calls only, no synthesis
    assert mock_query.call_count == 2


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_multi_reviewer_agree(mock_query: MagicMock) -> None:
    """Two reviewers both approve -> deterministic score 1.0."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Feasible"),
        _make_review_events("approve", "No risks found"),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Build the thing", lambda c, t, p: None, complexity="L"
    )

    assert result.consensus_score == 1.0  # 2 approves / 2 total
    assert result.human_decision_needed is False
    assert len(result.reviews) == 2
    assert result.decision_points == []
    assert result.lifecycle_state == ReviewLifecycleState.APPROVED


# ------------------------------------------------------------------
# Progress callback
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_progress_callback(mock_query: MagicMock) -> None:
    """on_progress is called with (completed, total, phase) around each reviewer."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("approve", "OK"),
    ])

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
    ]


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_progress_callback_single_reviewer(mock_query: MagicMock) -> None:
    """Progress callback for single reviewer: start + complete phases."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
    ])

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
@patch("src.review_pipeline.run_claude_query")
async def test_s_complexity_skips_optional(mock_query: MagicMock) -> None:
    """S complexity only runs required reviewers."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config())

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert len(result.reviews) == 1
    assert mock_query.call_count == 1


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_m_complexity_includes_optional(mock_query: MagicMock) -> None:
    """M complexity includes optional adversarial reviewer."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config())

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    assert len(result.reviews) == 2
    # 2 reviews, no synthesis (deterministic merge)
    assert mock_query.call_count == 2


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_l_complexity_includes_optional(mock_query: MagicMock) -> None:
    """L complexity also includes optional adversarial reviewer."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("approve", "OK"),
    ])

    pipeline = ReviewPipeline(_default_config())

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="L"
    )

    assert len(result.reviews) == 2


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_parse_failure_treated_as_reject(mock_query: MagicMock) -> None:
    """Invalid JSON from reviewer -> treated as reject with REJECTED_SINGLE lifecycle."""
    # Result has raw text instead of structured review JSON
    events = [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-test"),
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            result_text="This is not valid JSON",
            structured_output=None,
            model="claude-sonnet-4-5",
        ),
    ]
    _setup_mock_query(mock_query, [events])

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
@patch("src.review_pipeline.run_claude_query")
async def test_deterministic_merge_mixed_verdicts(mock_query: MagicMock) -> None:
    """Mixed verdicts -> deterministic score 0.5, human decision needed."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("reject", "Bad", ["Fix this"]),
    ])

    pipeline = ReviewPipeline(_default_config(), threshold=0.8)

    result = await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="M"
    )

    assert result.consensus_score == 0.5  # 1 approve / 2 total
    assert result.human_decision_needed is True
    assert len(result.decision_points) >= 1
    # No synthesis call -- only 2 review calls
    assert mock_query.call_count == 2


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
    assert "logic gaps" in prompt.lower()


def test_build_review_prompt_unknown_focus() -> None:
    """Unknown focus area returns the default prompt."""
    pipeline = ReviewPipeline(_default_config())

    prompt = pipeline._build_review_prompt("unknown_focus")

    assert "code reviewer" in prompt.lower()


def test_review_prompts_include_task_planning_rules() -> None:
    """All review prompts include task planning rules from CLAUDE.md."""
    pipeline = ReviewPipeline(_default_config())

    for focus in ["feasibility_and_edge_cases", "adversarial_red_team", "unknown_focus"]:
        prompt = pipeline._build_review_prompt(focus)
        assert "journey-first" in prompt.lower(), f"{focus} missing journey-first rule"
        assert "scenario matrix" in prompt.lower(), f"{focus} missing scenario matrix rule"
        assert "smoke test" in prompt.lower(), f"{focus} missing smoke test rule"


def test_review_prompts_include_project_constraints() -> None:
    """All review prompts include key project constraints from CLAUDE.md."""
    pipeline = ReviewPipeline(_default_config())

    for focus in ["feasibility_and_edge_cases", "adversarial_red_team", "unknown_focus"]:
        prompt = pipeline._build_review_prompt(focus)
        assert "utf-8" in prompt.lower(), f"{focus} missing UTF-8 constraint"
        assert "type hints" in prompt.lower(), f"{focus} missing type hints constraint"
        assert "schema changes require migration" in prompt.lower(), (
            f"{focus} missing schema migration constraint"
        )


def test_review_prompts_include_task_schema_context() -> None:
    """All review prompts include task schema conventions."""
    pipeline = ReviewPipeline(_default_config())

    for focus in ["feasibility_and_edge_cases", "adversarial_red_team", "unknown_focus"]:
        prompt = pipeline._build_review_prompt(focus)
        assert "T-P{priority}-{number}" in prompt, f"{focus} missing task ID format"
        assert "Complexity" in prompt, f"{focus} missing complexity field"
        assert "Acceptance Criteria" in prompt, f"{focus} missing AC requirement"


def test_review_prompts_include_state_machine_rules() -> None:
    """Review prompts include state machine transition rules."""
    pipeline = ReviewPipeline(_default_config())

    prompt = pipeline._build_review_prompt("feasibility_and_edge_cases")
    assert "state machine" in prompt.lower() or "status transitions" in prompt.lower()
    assert "side-effects" in prompt.lower()


def test_feasibility_includes_structural_checks() -> None:
    """Feasibility reviewer includes specific structural check items."""
    pipeline = ReviewPipeline(_default_config())

    prompt = pipeline._build_review_prompt("feasibility_and_edge_cases")

    assert "for each step: is it actionable" in prompt.lower()
    assert "does at least one ac verify each step" in prompt.lower()
    assert "are listed files consistent with the codebase" in prompt.lower()


def test_adversarial_includes_structural_checks() -> None:
    """Adversarial reviewer includes specific structural check items."""
    pipeline = ReviewPipeline(_default_config())

    prompt = pipeline._build_review_prompt("adversarial_red_team")

    assert "dag" in prompt.lower() or "dag (no cycles)" in prompt.lower()
    assert "independently testable" in prompt.lower()
    assert "hidden assumptions" in prompt.lower()
    assert "scope creep" in prompt.lower()


def test_adversarial_no_owasp_security_checks() -> None:
    """Adversarial reviewer does not include code-level security checks (no code to inspect)."""
    pipeline = ReviewPipeline(_default_config())

    prompt = pipeline._build_review_prompt("adversarial_red_team")

    assert "owasp" not in prompt.lower()
    assert "security vulnerabilities" not in prompt.lower()
    assert "sql injection" not in prompt.lower()
    assert "xss" not in prompt.lower()


# ------------------------------------------------------------------
# SDK call content verification
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_reviewer_receives_task_context(mock_query: MagicMock) -> None:
    """SDK call includes task title, ID, description, and plan content in prompt."""
    captured_prompts: list[str] = []

    def _capture_query(prompt: str, options: Any = None):
        captured_prompts.append(prompt)
        return _mock_run_claude_query_from_events(
            _make_review_events("approve", "OK")
        )

    mock_query.side_effect = _capture_query

    pipeline = ReviewPipeline(_default_config())
    task = _sample_task()

    await pipeline.review_task(
        task, "My detailed plan", lambda c, t, p: None, complexity="S"
    )

    assert len(captured_prompts) >= 1
    prompt = captured_prompts[0]
    assert task.title in prompt
    assert task.id in prompt
    assert "My detailed plan" in prompt


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_reviewer_uses_correct_model(mock_query: MagicMock) -> None:
    """SDK call uses the model specified in the reviewer config via QueryOptions."""
    captured_options: list[Any] = []

    def _capture_query(prompt: str, options: Any = None):
        captured_options.append(options)
        return _mock_run_claude_query_from_events(
            _make_review_events("approve", "OK")
        )

    mock_query.side_effect = _capture_query

    pipeline = ReviewPipeline(_default_config())

    await pipeline.review_task(
        _sample_task(), "Plan", lambda c, t, p: None, complexity="S"
    )

    assert len(captured_options) >= 1
    assert captured_options[0].model == "claude-sonnet-4-5"


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
@patch("src.review_pipeline.run_claude_query")
async def test_threshold_boundary(mock_query: MagicMock) -> None:
    """Score exactly at threshold -> no human decision needed."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("reject", "Bad plan"),
        _make_review_events("reject", "Risky"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Plan looks good"),
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
    _setup_mock_query(mock_query, [events])

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
    _setup_mock_query(mock_query, [
        _make_review_events(
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
    _setup_mock_query(mock_query, [
        _make_review_events(
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
    _setup_mock_query(mock_query, [
        _make_review_events(
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
        return _mock_run_claude_query_from_events(
            _make_review_events("approve", "OK")
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
        return _mock_run_claude_query_from_events(
            _make_review_events("approve", "OK")
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
    _setup_mock_query(mock_query, [
        _make_review_events(
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("reject", "Bad"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("reject", "Bad"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
        return _mock_run_claude_query_from_events(
            _make_review_events("approve", "OK")
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
        return _mock_run_claude_query_from_events(
            _make_review_events("approve", "OK")
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
        return _mock_run_claude_query_from_events(
            _make_review_events("approve", "OK")
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
        return _mock_run_claude_query_from_events(
            _make_review_events("approve", "OK")
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
@patch("src.review_pipeline.run_claude_query")
async def test_lifecycle_state_on_review_state_approve(mock_query: MagicMock) -> None:
    """ReviewState.lifecycle_state is APPROVED on approval."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("reject", "Bad"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("reject", "Bad"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("reject", "Issues found"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("reject", "Bad plan"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks good", ["Minor nit"]),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks good"),
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
    _setup_mock_query(mock_query, [
        _make_error_events("SDK crashed"),
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
# T-P0-94: Stream event callback tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_on_stream_event_receives_parsed_events(mock_query: MagicMock) -> None:
    """on_stream_event callback receives parsed event dicts."""
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks good"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks good"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks good"),
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
    _setup_mock_query(mock_query, [events])

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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks good"),
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
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks good"),
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
