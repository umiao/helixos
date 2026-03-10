"""Shared API conversion helpers and constants.

Extracted from src/api.py to avoid circular imports between
src/api.py and src/routes/*.py modules.
"""

from __future__ import annotations

from pathlib import Path

from src.models import Project, Task
from src.schemas import (
    ExecutionStateResponse,
    ProjectResponse,
    ReviewStateResponse,
    TaskResponse,
)

CONFIG_PATH = Path("orchestrator_config.yaml")


def _project_to_response(
    project: Project,
    *,
    execution_paused: bool = False,
    review_gate_enabled: bool = True,
) -> ProjectResponse:
    """Convert a domain Project to an API response."""
    return ProjectResponse(
        id=project.id,
        name=project.name,
        repo_path=str(project.repo_path) if project.repo_path else None,
        tasks_file=project.tasks_file,
        executor_type=project.executor_type,
        max_concurrency=project.max_concurrency,
        claude_md_path=str(project.claude_md_path) if project.claude_md_path else None,
        execution_paused=execution_paused,
        review_gate_enabled=review_gate_enabled,
        is_primary=project.is_primary,
    )


def _task_to_response(task: Task) -> TaskResponse:
    """Convert a domain Task to an API response."""
    review_resp = None
    if task.review is not None:
        review_resp = ReviewStateResponse(
            rounds_total=task.review.rounds_total,
            rounds_completed=task.review.rounds_completed,
            consensus_score=task.review.consensus_score,
            human_decision_needed=task.review.human_decision_needed,
            decision_points=task.review.decision_points,
            human_choice=task.review.human_choice,
        )

    execution_resp = None
    if task.execution is not None:
        execution_resp = ExecutionStateResponse(
            started_at=task.execution.started_at,
            finished_at=task.execution.finished_at,
            retry_count=task.execution.retry_count,
            max_retries=task.execution.max_retries,
            exit_code=task.execution.exit_code,
            log_tail=task.execution.log_tail,
            result=task.execution.result,
            error_summary=task.execution.error_summary,
        )

    return TaskResponse(
        id=task.id,
        project_id=task.project_id,
        local_task_id=task.local_task_id,
        title=task.title,
        original_title=task.original_title,
        description=task.description,
        status=task.status,
        executor_type=task.executor_type,
        depends_on=task.depends_on,
        review=review_resp,
        execution=execution_resp,
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
        review_status=task.review_status,
        review_lifecycle_state=task.review_lifecycle_state,
        plan_status=task.plan_status,
        plan_json=task.plan_json,
        plan_generation_id=task.plan_generation_id,
        has_proposed_tasks=task.has_proposed_tasks,
        replan_attempt=task.replan_attempt,
    )
