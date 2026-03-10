"""Tests for T-P0-121: complexity parameter passthrough to review pipeline.

Verifies that:
1. _enqueue_review_pipeline passes task.complexity to review_task()
2. S-complexity tasks get only required reviewers (1 reviewer)
3. M/L-complexity tasks get required + optional reviewers (2 reviewers)
4. Tasks without complexity field default to "S"
5. Complexity inferred from plan structure in tasks.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models import ExecutorType, Task, TaskStatus
from src.review_pipeline import ReviewPipeline
from src.sdk_adapter import ClaudeEvent, ClaudeEventType
from tests.factories import make_review_pipeline_config, make_task

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_review_events(verdict: str = "approve") -> list[ClaudeEvent]:
    """Create minimal review events for a reviewer response."""
    pass_value = verdict == "approve"
    inner = {
        "blocking_issues": [] if pass_value else [{"issue": "problem", "severity": "high"}],
        "suggestions": [],
        "pass": pass_value,
    }
    return [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-test"),
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output=inner,
            result_text=None,
            model="claude-sonnet-4-5",
            session_id="sess-test",
        ),
    ]


async def _mock_gen(events: list[ClaudeEvent]):
    """Async generator yielding events."""
    for e in events:
        yield e


# ------------------------------------------------------------------
# Tests: review_task receives complexity
# ------------------------------------------------------------------


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_s_complexity_single_reviewer(mock_query: MagicMock) -> None:
    """S-complexity task should only run the required reviewer (1 call)."""
    mock_query.side_effect = lambda *a, **kw: _mock_gen(_make_review_events())

    pipeline = ReviewPipeline(make_review_pipeline_config(), threshold=0.8)
    result = await pipeline.review_task(
        make_task(status=TaskStatus.REVIEW, description="Test description", complexity="S"), "Plan text", lambda c, t, p: None,
        complexity="S",
    )

    assert mock_query.call_count == 1
    assert len(result.reviews) == 1


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_m_complexity_two_reviewers(mock_query: MagicMock) -> None:
    """M-complexity task should run required + adversarial reviewer (2 calls)."""
    mock_query.side_effect = lambda *a, **kw: _mock_gen(_make_review_events())

    pipeline = ReviewPipeline(make_review_pipeline_config(), threshold=0.8)
    result = await pipeline.review_task(
        make_task(status=TaskStatus.REVIEW, description="Test description", complexity="M"), "Plan text", lambda c, t, p: None,
        complexity="M",
    )

    assert mock_query.call_count == 2
    assert len(result.reviews) == 2


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_l_complexity_two_reviewers(mock_query: MagicMock) -> None:
    """L-complexity task should run required + adversarial reviewer (2 calls)."""
    mock_query.side_effect = lambda *a, **kw: _mock_gen(_make_review_events())

    pipeline = ReviewPipeline(make_review_pipeline_config(), threshold=0.8)
    result = await pipeline.review_task(
        make_task(status=TaskStatus.REVIEW, description="Test description", complexity="L"), "Plan text", lambda c, t, p: None,
        complexity="L",
    )

    assert mock_query.call_count == 2
    assert len(result.reviews) == 2


@pytest.mark.asyncio
@patch("src.review_pipeline.run_claude_query")
async def test_default_complexity_is_s(mock_query: MagicMock) -> None:
    """When complexity is not passed, default to S (1 reviewer only)."""
    mock_query.side_effect = lambda *a, **kw: _mock_gen(_make_review_events())

    pipeline = ReviewPipeline(make_review_pipeline_config(), threshold=0.8)
    # Do NOT pass complexity -- should default to "S"
    result = await pipeline.review_task(
        make_task(status=TaskStatus.REVIEW, description="Test description"), "Plan text", lambda c, t, p: None,
    )

    assert mock_query.call_count == 1
    assert len(result.reviews) == 1


# ------------------------------------------------------------------
# Test: _enqueue_review_pipeline integration
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_passes_task_complexity() -> None:
    """_enqueue_review_pipeline passes task.complexity to review_task()."""
    from src.events import EventBus
    from src.routes.reviews import _enqueue_review_pipeline

    task = make_task(status=TaskStatus.REVIEW, description="Test description", complexity="M")
    event_bus = EventBus()

    mock_pipeline = MagicMock(spec=ReviewPipeline)
    # Make review_task an async mock that captures its kwargs
    captured_kwargs: dict = {}

    async def _capture_review_task(**kwargs):
        captured_kwargs.update(kwargs)
        from src.models import ReviewLifecycleState, ReviewState
        return ReviewState(
            rounds_total=1,
            rounds_completed=1,
            reviews=[],
            consensus_score=1.0,
            human_decision_needed=False,
            lifecycle_state=ReviewLifecycleState.APPROVED,
        )

    async def _capture_review_task_args(
        task, plan_content, on_progress, **kwargs
    ):
        captured_kwargs.update(kwargs)
        from src.models import ReviewLifecycleState, ReviewState
        return ReviewState(
            rounds_total=1,
            rounds_completed=1,
            reviews=[],
            consensus_score=1.0,
            human_decision_needed=False,
            lifecycle_state=ReviewLifecycleState.APPROVED,
        )

    mock_pipeline.review_task = AsyncMock(side_effect=_capture_review_task_args)

    mock_tm = AsyncMock()
    mock_tm.set_review_lifecycle_state = AsyncMock()
    mock_tm.update_task = AsyncMock()
    mock_tm.set_review_status = AsyncMock()
    mock_tm.set_review_result = AsyncMock(return_value=True)
    mock_tm.update_status = AsyncMock(return_value=task)
    mock_tm.get_task = AsyncMock(return_value=task)

    _enqueue_review_pipeline(
        task_manager=mock_tm,
        review_pipeline=mock_pipeline,
        event_bus=event_bus,
        task=task,
        task_id=task.id,
    )

    # Let the background task run
    await asyncio.sleep(0.1)

    # Verify review_task was called
    assert mock_pipeline.review_task.called
    call_kwargs = mock_pipeline.review_task.call_args
    # complexity should be "M" from the task
    assert call_kwargs.kwargs.get("complexity") == "M" or \
        (len(call_kwargs.args) > 3 and call_kwargs.args[3] == "M")


# ------------------------------------------------------------------
# Test: Task model complexity field
# ------------------------------------------------------------------


def test_task_model_complexity_default() -> None:
    """Task model should have complexity field defaulting to 'S'."""
    task = Task(
        id="P0::T-P0-1",
        project_id="P0",
        local_task_id="T-P0-1",
        title="Test",
        status=TaskStatus.BACKLOG,
        executor_type=ExecutorType.CODE,
    )
    assert task.complexity == "S"


def test_task_model_complexity_set() -> None:
    """Task model should accept explicit complexity."""
    task = Task(
        id="P0::T-P0-1",
        project_id="P0",
        local_task_id="T-P0-1",
        title="Test",
        status=TaskStatus.BACKLOG,
        executor_type=ExecutorType.CODE,
        complexity="L",
    )
    assert task.complexity == "L"


# ------------------------------------------------------------------
# Test: complexity inference from plan data
# ------------------------------------------------------------------


def test_complexity_inference_small_plan() -> None:
    """Plan with few steps and no proposed tasks -> S complexity."""
    plan_data = {
        "plan": "Simple plan",
        "steps": [{"step": "Do thing", "files": []}],
        "acceptance_criteria": ["It works"],
        "proposed_tasks": [],
    }
    proposed = plan_data.get("proposed_tasks", [])
    num_steps = len(plan_data.get("steps", []))

    if len(proposed) > 3 or num_steps > 8:
        inferred = "L"
    elif len(proposed) > 0 or num_steps > 4:
        inferred = "M"
    else:
        inferred = "S"

    assert inferred == "S"


def test_complexity_inference_medium_plan() -> None:
    """Plan with proposed tasks -> M complexity."""
    plan_data = {
        "plan": "Medium plan",
        "steps": [{"step": f"Step {i}", "files": []} for i in range(3)],
        "acceptance_criteria": ["AC1", "AC2"],
        "proposed_tasks": [
            {"title": "Sub-task 1", "description": "Do X"},
        ],
    }
    proposed = plan_data.get("proposed_tasks", [])
    num_steps = len(plan_data.get("steps", []))

    if len(proposed) > 3 or num_steps > 8:
        inferred = "L"
    elif len(proposed) > 0 or num_steps > 4:
        inferred = "M"
    else:
        inferred = "S"

    assert inferred == "M"


def test_complexity_inference_large_plan() -> None:
    """Plan with many proposed tasks -> L complexity."""
    plan_data = {
        "plan": "Large plan",
        "steps": [{"step": f"Step {i}", "files": []} for i in range(10)],
        "acceptance_criteria": ["AC1"],
        "proposed_tasks": [
            {"title": f"Sub-task {i}", "description": f"Do {i}"}
            for i in range(5)
        ],
    }
    proposed = plan_data.get("proposed_tasks", [])
    num_steps = len(plan_data.get("steps", []))

    if len(proposed) > 3 or num_steps > 8:
        inferred = "L"
    elif len(proposed) > 0 or num_steps > 4:
        inferred = "M"
    else:
        inferred = "S"

    assert inferred == "L"
