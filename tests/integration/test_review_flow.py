"""Integration test: BACKLOG -> review -> REVIEW_NEEDS_HUMAN -> decide -> QUEUED.

Tests the review pipeline flow with a mock Anthropic client.
"""

from __future__ import annotations

import pytest

from src.config import (
    ReviewerConfig,
)
from src.events import EventBus
from src.models import ExecutorType, ReviewState, Task, TaskStatus
from src.review_pipeline import ReviewPipeline
from src.task_manager import TaskManager

from .conftest import MockAnthropicClient


@pytest.mark.integration
async def test_review_approve_auto(
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

    # Create mock Anthropic client that approves
    mock_client = MockAnthropicClient([
        '{"verdict": "approve", "summary": "Looks good", "suggestions": []}',
    ])

    pipeline = ReviewPipeline(
        config=config.review_pipeline,
        anthropic_client=mock_client,
        threshold=0.8,
    )

    progress_calls: list[tuple[int, int]] = []

    def on_progress(completed: int, total: int) -> None:
        """Track progress callbacks."""
        progress_calls.append((completed, total))

    review_state = await pipeline.review_task(
        task=task,
        plan_content=task.description,
        on_progress=on_progress,
    )

    assert review_state.consensus_score == 1.0
    assert review_state.human_decision_needed is False
    assert len(progress_calls) == 1

    # Transition to REVIEW_AUTO_APPROVED -> QUEUED
    await task_manager.update_status(task.id, TaskStatus.REVIEW_AUTO_APPROVED)
    await task_manager.update_status(task.id, TaskStatus.QUEUED)

    final = await task_manager.get_task(task.id)
    assert final is not None
    assert final.status == TaskStatus.QUEUED


@pytest.mark.integration
async def test_review_reject_needs_human(
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

    # Mock client returns reject
    mock_client = MockAnthropicClient([
        '{"verdict": "reject", "summary": "Too risky", "suggestions": ["Add tests"]}',
    ])

    pipeline = ReviewPipeline(
        config=config.review_pipeline,
        anthropic_client=mock_client,
        threshold=0.8,
    )

    review_state = await pipeline.review_task(
        task=task,
        plan_content=task.description,
        on_progress=lambda c, t: None,
    )

    assert review_state.consensus_score == 0.3
    assert review_state.human_decision_needed is True
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
async def test_review_reject_then_human_rejects(
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

    mock_client = MockAnthropicClient([
        '{"verdict": "reject", "summary": "Bad idea", "suggestions": ["Dont do this"]}',
    ])

    pipeline = ReviewPipeline(
        config=config.review_pipeline,
        anthropic_client=mock_client,
        threshold=0.8,
    )

    await pipeline.review_task(
        task=task,
        plan_content=task.description,
        on_progress=lambda c, t: None,
    )

    await task_manager.update_status(task.id, TaskStatus.REVIEW_NEEDS_HUMAN)

    # Human rejects -> BACKLOG
    await task_manager.update_status(task.id, TaskStatus.BACKLOG)

    final = await task_manager.get_task(task.id)
    assert final is not None
    assert final.status == TaskStatus.BACKLOG


@pytest.mark.integration
async def test_multi_reviewer_synthesis(
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

    # First reviewer approves, second (adversarial) rejects, synthesis returns 0.6
    mock_client = MockAnthropicClient([
        '{"verdict": "approve", "summary": "OK", "suggestions": []}',
        '{"verdict": "reject", "summary": "Risky", "suggestions": ["Watch out"]}',
        '{"score": 0.6, "disagreements": ["Security concern"]}',
    ])

    pipeline = ReviewPipeline(
        config=config.review_pipeline,
        anthropic_client=mock_client,
        threshold=0.8,
    )

    review_state = await pipeline.review_task(
        task=task,
        plan_content=task.description,
        on_progress=lambda c, t: None,
        complexity="M",  # M triggers adversarial reviewer
    )

    assert review_state.consensus_score == 0.6
    assert review_state.human_decision_needed is True
    assert "Security concern" in review_state.decision_points
