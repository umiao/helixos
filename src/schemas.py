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
    claude_md_path: str | None = None
    execution_paused: bool = False
    review_gate_enabled: bool = True


class ProjectDetailResponse(BaseModel):
    """Response schema for a project with its tasks."""

    id: str
    name: str
    repo_path: str | None = None
    tasks_file: str = "TASKS.md"
    executor_type: ExecutorType
    max_concurrency: int = 1
    claude_md_path: str | None = None
    execution_paused: bool = False
    review_gate_enabled: bool = True
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
    review_status: str = "idle"


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
    error_type: str | None = None


# ------------------------------------------------------------------
# Request schemas
# ------------------------------------------------------------------


class StatusTransitionRequest(BaseModel):
    """Request to transition a task to a new status.

    Optional fields support backward transitions and optimistic locking:
    - *reason*: human note for why the task is being moved backwards.
    - *expected_updated_at*: if provided, the server checks the task's
      ``updated_at`` matches before applying.  On mismatch the server
      returns 409 with ``{"conflict": true}``.
    """

    status: TaskStatus
    reason: str = ""
    expected_updated_at: str | None = None


class UpdateTaskRequest(BaseModel):
    """Request to update task fields (title, description)."""

    title: str | None = Field(default=None, description="Updated task title")
    description: str | None = Field(
        default=None, description="Updated task description",
    )


class ReviewDecisionRequest(BaseModel):
    """Request to submit a human review decision."""

    decision: str = Field(
        ...,
        description="Human decision: 'approve', 'reject', or 'request_changes'",
    )
    reason: str = ""


# ------------------------------------------------------------------
# Dashboard schemas
# ------------------------------------------------------------------


class ProjectProcessStatus(BaseModel):
    """Per-project process status for the dashboard summary."""

    running: bool = False
    pid: int | None = None
    port: int | None = None
    uptime_seconds: float | None = None


class DashboardSummary(BaseModel):
    """Aggregate stats for the dashboard."""

    total_tasks: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    running_count: int = 0
    project_count: int = 0
    process_status: dict[str, ProjectProcessStatus] = Field(default_factory=dict)


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


class BrowseEntry(BaseModel):
    """A single entry in a directory listing."""

    name: str
    path: str
    is_dir: bool
    has_git: bool = False
    has_tasks_md: bool = False
    has_claude_md: bool = False


class BrowseResponse(BaseModel):
    """Response from browsing a directory."""

    path: str
    parent: str | None = None
    entries: list[BrowseEntry] = Field(default_factory=list)


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
# Task enrichment schemas
# ------------------------------------------------------------------


class EnrichTaskRequest(BaseModel):
    """Request to enrich a task title with AI-suggested description and priority."""

    title: str = Field(..., min_length=1, description="Task title to enrich")


class EnrichTaskResponse(BaseModel):
    """AI-generated enrichment suggestions."""

    description: str = Field(..., description="AI-suggested description")
    priority: str = Field(
        ...,
        pattern=r"^P\d+$",
        description="AI-suggested priority (P0, P1, P2)",
    )


# ------------------------------------------------------------------
# Task creation schemas
# ------------------------------------------------------------------


class CreateTaskRequest(BaseModel):
    """Request to create a new task in a project's TASKS.md."""

    title: str = Field(..., min_length=1, description="Task title")
    description: str = Field(default="", description="Task description")
    priority: str = Field(
        default="P0",
        pattern=r"^P\d+$",
        description="Priority level (e.g. P0, P1, P2)",
    )


class CreateTaskResponse(BaseModel):
    """Result of creating a task in TASKS.md."""

    task_id: str
    success: bool
    backup_path: str | None = None
    synced: bool = False
    sync_result: SyncResponse | None = None
    error: str | None = None


# ------------------------------------------------------------------
# Process management schemas
# ------------------------------------------------------------------


class ProcessStatusResponse(BaseModel):
    """Status of a project's dev server process."""

    running: bool
    pid: int | None = None
    port: int | None = None
    uptime_seconds: float | None = None


# ------------------------------------------------------------------
# Execution log + review history schemas
# ------------------------------------------------------------------


class ExecutionLogEntry(BaseModel):
    """A single execution log entry."""

    id: int
    task_id: str
    timestamp: str
    level: str
    message: str
    source: str


class ExecutionLogsResponse(BaseModel):
    """Paginated execution logs for a task."""

    task_id: str
    total: int
    offset: int
    limit: int
    entries: list[ExecutionLogEntry] = Field(default_factory=list)


class ReviewHistoryEntry(BaseModel):
    """A single review history entry."""

    id: int
    task_id: str
    round_number: int
    reviewer_model: str
    reviewer_focus: str
    verdict: str
    summary: str
    suggestions: list[str] = Field(default_factory=list)
    consensus_score: float | None = None
    human_decision: str | None = None
    human_reason: str | None = None
    raw_response: str = ""
    cost_usd: float | None = None
    review_attempt: int = 1
    timestamp: str


class ReviewHistoryResponse(BaseModel):
    """Paginated review history for a task."""

    task_id: str
    total: int
    offset: int
    limit: int
    entries: list[ReviewHistoryEntry] = Field(default_factory=list)


# ------------------------------------------------------------------
# Error schemas
# ------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


# Fix forward references for ProjectDetailResponse
ProjectDetailResponse.model_rebuild()
