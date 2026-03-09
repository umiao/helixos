"""Integration test: BACKLOG -> review -> REVIEW_NEEDS_HUMAN -> decide -> QUEUED.

Tests the review pipeline flow with SDK adapter mocking for Claude Agent SDK.
Migrated from subprocess mocking to SDK mocking in T-P1-89.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config import (
    ReviewerConfig,
)
from src.events import EventBus
from src.models import ExecutorType, ReviewState, Task, TaskStatus
from src.review_pipeline import ReviewPipeline
from src.sdk_adapter import ClaudeEvent, ClaudeEventType
from src.task_manager import TaskManager


def _make_review_events(
    verdict: str,
    summary: str,
    suggestions: list[str] | None = None,
) -> list[ClaudeEvent]:
    """Create ClaudeEvent objects simulating a review response.

    LLM returns {blocking_issues, suggestions, pass} schema.
    """
    pass_value = verdict == "approve"
    blocking_issues = [{"issue": summary, "severity": "high"}] if verdict == "reject" else []
    inner = {
        "blocking_issues": blocking_issues,
        "suggestions": suggestions or [],
        "pass": pass_value,
    }
    return [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-integ"),
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output=inner,
            result_text=None,
            model="claude-sonnet-4-5",
        ),
    ]


def _make_synthesis_events(
    score: float,
    disagreements: list[str] | None = None,
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
            model="claude-sonnet-4-5",
        ),
    ]


async def _mock_run_claude_query_from_events(events: list[ClaudeEvent]):
    """Create an async generator that yields events from a list."""
    for event in events:
        yield event


def _setup_mock_query(
    mock_query: MagicMock,
    event_sequences: list[list[ClaudeEvent]],
) -> None:
    """Configure mock run_claude_query to yield events from sequences."""
    call_count = 0

    def _side_effect(prompt: str, options: Any = None):
        nonlocal call_count
        idx = min(call_count, len(event_sequences) - 1)
        call_count += 1
        return _mock_run_claude_query_from_events(event_sequences[idx])

    mock_query.side_effect = _side_effect


@pytest.mark.integration
@patch("src.review_pipeline.run_claude_query")
async def test_review_approve_auto(
    mock_query: MagicMock,
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
) -> None:
    """A single reviewer that approves should auto-approve the task."""
    config = make_config(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                required=True,
            ),
        ],
    )

    # Create a task in BACKLOG
    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Test feature",
        description="Build a widget",
        status=TaskStatus.BACKLOG,
        executor_type=ExecutorType.CODE,
    )
    await task_manager.create_task(task)

    # Transition BACKLOG -> REVIEW
    await task_manager.update_status(task.id, TaskStatus.REVIEW)

    # Mock SDK that approves
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "Looks good"),
    ])

    pipeline = ReviewPipeline(
        config=config.review_pipeline,
        threshold=0.8,
    )

    progress_calls: list[tuple[int, int, str]] = []

    def on_progress(completed: int, total: int, phase: str) -> None:
        """Track progress callbacks."""
        progress_calls.append((completed, total, phase))

    review_state = await pipeline.review_task(
        task=task,
        plan_content=task.description,
        on_progress=on_progress,
    )

    assert review_state.consensus_score == 1.0
    assert review_state.human_decision_needed is False
    # 2 calls: "Starting..." + "Completed..." for single reviewer
    assert len(progress_calls) == 2

    # Transition to REVIEW_AUTO_APPROVED -> QUEUED
    await task_manager.update_status(task.id, TaskStatus.REVIEW_AUTO_APPROVED)
    await task_manager.update_status(task.id, TaskStatus.QUEUED)

    final = await task_manager.get_task(task.id)
    assert final is not None
    assert final.status == TaskStatus.QUEUED


@pytest.mark.integration
@patch("src.review_pipeline.run_claude_query")
async def test_review_reject_needs_human(
    mock_query: MagicMock,
    task_manager: TaskManager,
    event_bus: EventBus,
    make_config,
) -> None:
    """A reviewer that rejects should flag for human decision."""
    config = make_config(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                required=True,
            ),
        ],
    )

    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Risky feature",
        description="Refactor core module",
        status=TaskStatus.BACKLOG,
        executor_type=ExecutorType.CODE,
    )
    await task_manager.create_task(task)
    await task_manager.update_status(task.id, TaskStatus.REVIEW)

    # Mock SDK that rejects
    _setup_mock_query(mock_query, [
        _make_review_events("reject", "Too risky", ["Add tests"]),
    ])

    pipeline = ReviewPipeline(
        config=config.review_pipeline,
        threshold=0.8,
    )

    review_state = await pipeline.review_task(
        task=task,
        plan_content=task.description,
        on_progress=lambda c, t, p: None,
    )

    assert review_state.consensus_score == 0.0
    assert review_state.human_decision_needed is True
    assert review_state.lifecycle_state == "rejected_single"
    assert len(review_state.decision_points) > 0

    # Persist review state
    updated_task = task.model_copy(
        update={"review": review_state, "status": TaskStatus.REVIEW},
    )
    await task_manager.update_task(updated_task)
    await task_manager.update_status(task.id, TaskStatus.REVIEW_NEEDS_HUMAN)

    # Human decides to approve -> QUEUED
    current = await task_manager.get_task(task.id)
    assert current is not None
    assert current.status == TaskStatus.REVIEW_NEEDS_HUMAN

    review = current.review if current.review is not None else ReviewState()
    review = review.model_copy(update={"human_choice": "approve"})
    updated = current.model_copy(update={"review": review})
    await task_manager.update_task(updated)
    await task_manager.update_status(task.id, TaskStatus.QUEUED)

    final = await task_manager.get_task(task.id)
    assert final is not None
    assert final.status == TaskStatus.QUEUED
    assert final.review is not None
    assert final.review.human_choice == "approve"


@pytest.mark.integration
@patch("src.review_pipeline.run_claude_query")
async def test_review_reject_then_human_rejects(
    mock_query: MagicMock,
    task_manager: TaskManager,
    make_config,
) -> None:
    """Human rejects after review -> task goes back to BACKLOG."""
    config = make_config(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                required=True,
            ),
        ],
    )

    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Bad feature",
        description="Remove all tests",
        status=TaskStatus.BACKLOG,
        executor_type=ExecutorType.CODE,
    )
    await task_manager.create_task(task)
    await task_manager.update_status(task.id, TaskStatus.REVIEW)

    _setup_mock_query(mock_query, [
        _make_review_events("reject", "Bad idea", ["Dont do this"]),
    ])

    pipeline = ReviewPipeline(
        config=config.review_pipeline,
        threshold=0.8,
    )

    await pipeline.review_task(
        task=task,
        plan_content=task.description,
        on_progress=lambda c, t, p: None,
    )

    await task_manager.update_status(task.id, TaskStatus.REVIEW_NEEDS_HUMAN)

    # Human rejects -> BACKLOG
    await task_manager.update_status(task.id, TaskStatus.BACKLOG)

    final = await task_manager.get_task(task.id)
    assert final is not None
    assert final.status == TaskStatus.BACKLOG


@pytest.mark.integration
@patch("src.review_pipeline.run_claude_query")
async def test_multi_reviewer_synthesis(
    mock_query: MagicMock,
    task_manager: TaskManager,
    make_config,
) -> None:
    """Multiple reviewers trigger synthesis for consensus score."""
    config = make_config(
        reviewers=[
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="feasibility_and_edge_cases",
                required=True,
            ),
            ReviewerConfig(
                model="claude-sonnet-4-5",
                focus="adversarial_red_team",
                required=False,
            ),
        ],
    )

    task = Task(
        id="proj_a:T-P0-1",
        project_id="proj_a",
        local_task_id="T-P0-1",
        title="Medium task",
        description="Complex refactor",
        status=TaskStatus.BACKLOG,
        executor_type=ExecutorType.CODE,
    )
    await task_manager.create_task(task)

    # First reviewer approves, second (adversarial) rejects -> deterministic merge
    _setup_mock_query(mock_query, [
        _make_review_events("approve", "OK"),
        _make_review_events("reject", "Risky", ["Watch out"]),
    ])

    pipeline = ReviewPipeline(
        config=config.review_pipeline,
        threshold=0.8,
    )

    review_state = await pipeline.review_task(
        task=task,
        plan_content=task.description,
        on_progress=lambda c, t, p: None,
        complexity="M",  # M triggers adversarial reviewer
    )

    assert review_state.consensus_score == 0.5  # 1 approve / 2 total
    assert review_state.human_decision_needed is True
    assert "Risky" in review_state.decision_points
