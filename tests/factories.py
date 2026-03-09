"""Shared test factories for task, config, and SDK event creation.

Centralizes helpers that were previously duplicated across 18+ test files.
Import from here instead of defining local ``_make_task`` / ``_make_config``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from src.config import (
    GitConfig,
    OrchestratorConfig,
    OrchestratorSettings,
    ProjectConfig,
    ReviewerConfig,
    ReviewPipelineConfig,
)
from src.models import ExecutorType, Task, TaskStatus
from src.sdk_adapter import ClaudeEvent, ClaudeEventType

# ---------------------------------------------------------------------------
# Task factory
# ---------------------------------------------------------------------------


def make_task(
    task_id: str = "P0:T-P0-1",
    project_id: str = "P0",
    local_task_id: str = "T-P0-1",
    title: str = "Test task",
    status: TaskStatus = TaskStatus.BACKLOG,
    **kwargs: Any,
) -> Task:
    """Create a Task with sensible defaults.

    All Task fields can be overridden via ``**kwargs``.
    ``executor_type`` defaults to CODE if not provided.
    """
    return Task(
        id=task_id,
        project_id=project_id,
        local_task_id=local_task_id,
        title=title,
        status=status,
        executor_type=kwargs.pop("executor_type", ExecutorType.CODE),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Config factory
# ---------------------------------------------------------------------------


def make_config(tmp_path: Path) -> OrchestratorConfig:
    """Create a minimal OrchestratorConfig with one test project.

    Creates a temporary repo directory with a minimal TASKS.md file.
    """
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir(exist_ok=True)
    tasks_md = repo_path / "TASKS.md"
    tasks_md.write_text(
        "# Task Backlog\n\n## Active Tasks\n\n"
        "#### T-P0-1: Test task\n- Description\n\n"
        "## Completed Tasks\n",
        encoding="utf-8",
    )
    return OrchestratorConfig(
        orchestrator=OrchestratorSettings(
            state_db_path=tmp_path / "test.db",
            unified_env_path=tmp_path / ".env",
            global_concurrency_limit=3,
        ),
        projects={
            "proj-a": ProjectConfig(
                name="Project A",
                repo_path=repo_path,
                executor_type=ExecutorType.CODE,
                max_concurrency=1,
            ),
        },
        git=GitConfig(),
        review_pipeline=ReviewPipelineConfig(),
    )


def make_review_pipeline_config() -> ReviewPipelineConfig:
    """Create a ReviewPipelineConfig with 1 required + 1 optional reviewer."""
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


# ---------------------------------------------------------------------------
# SDK event builders
# ---------------------------------------------------------------------------


async def mock_sdk_events(
    *events: ClaudeEvent,
) -> AsyncIterator[ClaudeEvent]:
    """Create a mock async iterator yielding ClaudeEvent objects."""
    for event in events:
        yield event


def make_enrichment_events(
    description: str,
    priority: str,
) -> list[ClaudeEvent]:
    """Create ClaudeEvent list simulating enrichment result."""
    return [
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output={"description": description, "priority": priority},
        ),
    ]


def make_plan_events(
    plan: str,
    steps: list[dict],
    acceptance_criteria: list[str],
) -> list[ClaudeEvent]:
    """Create ClaudeEvent list simulating plan generation result."""
    return [
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output={
                "plan": plan,
                "steps": steps,
                "acceptance_criteria": acceptance_criteria,
            },
        ),
    ]


def make_error_event(message: str) -> ClaudeEvent:
    """Create a ClaudeEvent for an SDK error."""
    return ClaudeEvent(
        type=ClaudeEventType.ERROR,
        error_message=message,
    )


def make_review_events(
    verdict: str,
    summary: str,
    suggestions: list[str] | None = None,
    model: str = "claude-sonnet-4-5",
    usage: dict[str, int] | None = None,
    session_id: str | None = None,
    cost_usd: float | None = None,
    blocking_issues: list[dict] | None = None,
) -> list[ClaudeEvent]:
    """Create ClaudeEvent list simulating a review response.

    The LLM returns {blocking_issues, suggestions, pass} schema.
    ``verdict`` param maps to ``pass``: "approve" -> true, "reject" -> false.
    """
    pass_value = verdict == "approve"
    if blocking_issues is None:
        blocking_issues = (
            [{"issue": summary, "severity": "high"}] if verdict == "reject" else []
        )
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


def make_synthesis_events(
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


def make_error_events(error_message: str = "SDK error") -> list[ClaudeEvent]:
    """Create ClaudeEvent objects simulating an SDK error."""
    return [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-err"),
        ClaudeEvent(
            type=ClaudeEventType.ERROR,
            error_message=error_message,
        ),
    ]


async def mock_run_claude_query_from_events(
    events: list[ClaudeEvent],
) -> AsyncIterator[ClaudeEvent]:
    """Create an async generator that yields events from a list."""
    for event in events:
        yield event


def setup_mock_query(
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
        return mock_run_claude_query_from_events(event_sequences[idx])

    mock_query.side_effect = _side_effect
