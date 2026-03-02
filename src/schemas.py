"""Pydantic request/response schemas for the HelixOS REST API.

Defines typed schemas for all API endpoints per PRD Section 10.
These schemas are separate from the internal domain models in models.py
to allow the API surface to evolve independently.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from src.models import ExecutorType, TaskStatus

# ------------------------------------------------------------------
# Project schemas
# ------------------------------------------------------------------


class ProjectResponse(BaseModel):
    """Response schema for a project."""

    id: str
    name: str
    repo_path: str | None = None
    tasks_file: str = "TASKS.md"
    executor_type: ExecutorType
    max_concurrency: int = 1


class ProjectDetailResponse(BaseModel):
    """Response schema for a project with its tasks."""

    id: str
    name: str
    repo_path: str | None = None
    tasks_file: str = "TASKS.md"
    executor_type: ExecutorType
    max_concurrency: int = 1
    tasks: list[TaskResponse] = Field(default_factory=list)


# ------------------------------------------------------------------
# Task schemas
# ------------------------------------------------------------------


class TaskResponse(BaseModel):
    """Response schema for a task."""

    id: str
    project_id: str
    local_task_id: str
    title: str
    description: str = ""
    status: TaskStatus
    executor_type: ExecutorType
    depends_on: list[str] = Field(default_factory=list)
    review: ReviewStateResponse | None = None
    execution: ExecutionStateResponse | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class ReviewStateResponse(BaseModel):
    """Response schema for review state."""

    rounds_total: int
    rounds_completed: int
    consensus_score: float | None = None
    human_decision_needed: bool = False
    decision_points: list[str] = Field(default_factory=list)
    human_choice: str | None = None


class ExecutionStateResponse(BaseModel):
    """Response schema for execution state."""

    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 3
    exit_code: int | None = None
    log_tail: list[str] = Field(default_factory=list)
    result: str = "pending"
    error_summary: str | None = None


# ------------------------------------------------------------------
# Request schemas
# ------------------------------------------------------------------


class StatusTransitionRequest(BaseModel):
    """Request to transition a task to a new status."""

    status: TaskStatus


class ReviewDecisionRequest(BaseModel):
    """Request to submit a human review decision."""

    decision: str = Field(
        ..., description="Human decision: 'approve' or 'reject'",
    )
    reason: str = ""


# ------------------------------------------------------------------
# Dashboard schemas
# ------------------------------------------------------------------


class DashboardSummary(BaseModel):
    """Aggregate stats for the dashboard."""

    total_tasks: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    running_count: int = 0
    project_count: int = 0


# ------------------------------------------------------------------
# Sync schemas
# ------------------------------------------------------------------


class SyncResponse(BaseModel):
    """Response from a sync operation."""

    project_id: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    warnings: list[str] = Field(default_factory=list)


class SyncAllResponse(BaseModel):
    """Response from syncing all projects."""

    results: list[SyncResponse] = Field(default_factory=list)


# ------------------------------------------------------------------
# Project onboarding schemas
# ------------------------------------------------------------------


class ValidateProjectRequest(BaseModel):
    """Request to validate a directory for import."""

    path: str = Field(..., description="Path to the project directory")


class ValidateProjectResponse(BaseModel):
    """Result of validating a project directory."""

    valid: bool
    name: str
    path: str
    has_git: bool
    has_tasks_md: bool
    has_claude_config: bool
    suggested_id: str
    warnings: list[str] = Field(default_factory=list)
    limited_mode_reasons: list[str] = Field(default_factory=list)


class ImportProjectRequest(BaseModel):
    """Request to import a project into the orchestrator."""

    path: str = Field(..., description="Path to the project directory")
    project_id: str | None = Field(
        default=None,
        description="Project ID override (auto-generated if omitted)",
    )
    name: str | None = Field(
        default=None,
        description="Display name override (defaults to directory name)",
    )
    project_type: str = Field(
        default="other",
        description="Project type: frontend, backend, or other",
    )
    launch_command: str | None = Field(
        default=None,
        description="Command to launch the project dev server",
    )
    preferred_port: int | None = Field(
        default=None,
        ge=1024,
        le=65535,
        description="Preferred port number",
    )


class ImportProjectResponse(BaseModel):
    """Result of importing a project."""

    project_id: str
    name: str
    repo_path: str
    port: int | None = None
    synced: bool = False
    sync_result: SyncResponse | None = None
    warnings: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------
# Error schemas
# ------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


# Fix forward references for ProjectDetailResponse
ProjectDetailResponse.model_rebuild()
