"""Tests for the ReviewPipeline.

Uses SDK adapter mocking (patching run_claude_query) to test approve, reject,
disagree, progress callback, synthesis, and error-handling scenarios.
All LLM calls go through the Claude Agent SDK via ``sdk_adapter``.

Migrated from subprocess mocking to SDK mocking in T-P1-89.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.models import ExecutorType, ReviewLifecycleState, Task, TaskStatus
from src.review_pipeline import (
    ReviewPipeline,
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

    # With parallel execution, all "Starting" messages are emitted first,
    # then "Completed" messages as results are collected.
    assert progress_calls == [
        (0, 2, "Starting feasibility_and_edge_cases review..."),
        (1, 2, "Starting adversarial_red_team review..."),
        (1, 2, "Completed feasibility_and_edge_cases review"),
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


def test_review_prompts_include_calibration_examples() -> None:
    """All review prompts include pass/fail calibration examples."""
    pipeline = ReviewPipeline(_default_config())

    for focus in ["feasibility_and_edge_cases", "adversarial_red_team", "unknown_focus"]:
        prompt = pipeline._build_review_prompt(focus)
        # Verify pass example is present
        assert "calibration examples" in prompt.lower(), (
            f"{focus} missing calibration examples heading"
        )
        assert '"pass": true' in prompt, f"{focus} missing pass=true example"
        assert '"pass": false' in prompt, f"{focus} missing pass=false example"
        # Verify threshold guidance is present
        assert "threshold guidance" in prompt.lower(), (
            f"{focus} missing threshold guidance"
        )
        # Verify examples show the distinction clearly
        assert "blocking_issues" in prompt, f"{focus} missing blocking_issues in example"
        assert "dependency cycle" in prompt.lower(), (
            f"{focus} missing dependency cycle example in fail case"
        )


# ------------------------------------------------------------------
# Config-driven reviewer personas
# ------------------------------------------------------------------


def test_override_reviewer_persona_from_yaml(tmp_path: Any) -> None:
    """Override reviewer personas via custom YAML, verify new persona is used."""
    from src.review_pipeline import (
        _clear_reviewer_params_cache,
        _load_reviewer_personas,
    )

    custom_yaml = tmp_path / "custom_personas.yaml"
    custom_yaml.write_text(
        "custom_focus:\n"
        '  role: "You are a security auditor."\n'
        '  questions: "Check for OWASP top 10 issues."\n'
        "default:\n"
        '  role: "You are a generic reviewer."\n'
        '  questions: "Is this plan good?"\n',
        encoding="utf-8",
    )

    params = _load_reviewer_personas(custom_yaml)

    assert "custom_focus" in params
    assert params["custom_focus"][0] == "You are a security auditor."
    assert "OWASP" in params["custom_focus"][1]
    assert "default" in params
    _clear_reviewer_params_cache()


def test_missing_personas_file_falls_back_to_defaults(tmp_path: Any) -> None:
    """Missing YAML file returns defaults without crashing."""
    from src.review_pipeline import _load_reviewer_personas

    params = _load_reviewer_personas(tmp_path / "nonexistent.yaml")

    assert "default" in params
    assert "code reviewer" in params["default"][0].lower()


def test_custom_persona_used_in_build_review_prompt(tmp_path: Any) -> None:
    """End-to-end: custom YAML persona flows through _build_review_prompt."""
    from src.review_pipeline import (
        _clear_reviewer_params_cache,
        _load_reviewer_personas,
    )

    custom_yaml = tmp_path / "personas.yaml"
    custom_yaml.write_text(
        "my_new_persona:\n"
        '  role: "You are a performance reviewer."\n'
        '  questions: "Analyze for latency and throughput issues."\n'
        "default:\n"
        '  role: "You are a code reviewer."\n'
        '  questions: "Is this plan sound?"\n',
        encoding="utf-8",
    )

    import src.review_pipeline as rp

    # Inject custom personas into cache
    rp._reviewer_params_cache = _load_reviewer_personas(custom_yaml)
    try:
        pipeline = ReviewPipeline(_default_config())
        prompt = pipeline._build_review_prompt("my_new_persona")
        assert "performance reviewer" in prompt.lower()
        assert "latency" in prompt.lower()
    finally:
        _clear_reviewer_params_cache()


def test_adding_new_persona_requires_only_config() -> None:
    """Verify that _load_reviewer_personas accepts arbitrary focus keys."""
    import tempfile

    from src.review_pipeline import _load_reviewer_personas

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8",
    ) as f:
        f.write(
            "alpha:\n"
            '  role: "Alpha reviewer"\n'
            '  questions: "Alpha questions"\n'
            "beta:\n"
            '  role: "Beta reviewer"\n'
            '  questions: "Beta questions"\n'
            "gamma:\n"
            '  role: "Gamma reviewer"\n'
            '  questions: "Gamma questions"\n'
        )
        f.flush()
        params = _load_reviewer_personas(Path(f.name))

    assert len(params) == 4  # alpha, beta, gamma, + auto-added default
    assert "alpha" in params
    assert "beta" in params
    assert "gamma" in params
    assert "default" in params


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


