"""Pydantic data models for HelixOS orchestrator.

Defines the core entities from PRD Section 6.1: TaskStatus, ExecutorType,
Project, Task, ReviewState, LLMReview, ExecutionState, and Dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    """Task lifecycle states per PRD Section 5.3."""

    BACKLOG = "backlog"
    REVIEW = "review"
    REVIEW_AUTO_APPROVED = "review_auto_approved"
    REVIEW_NEEDS_HUMAN = "review_needs_human"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


class ReviewLifecycleState(StrEnum):
    """Canonical review lifecycle states.

    The backend is the single source of truth for lifecycle state.
    The frontend renders this value directly -- no field-guessing.

    State machine diagram::

        NOT_STARTED ──> RUNNING ──> APPROVED
                           │
                           ├──> PARTIAL ──> RUNNING  (retry / re-review)
                           │
                           ├──> FAILED  ──> RUNNING  (retry)
                           │
                           ├──> REJECTED_SINGLE       (single reviewer reject)
                           │       └──> RUNNING       (re-review after feedback)
                           │
                           └──> REJECTED_CONSENSUS    (multi-reviewer reject)
                                   └──> RUNNING       (re-review after feedback)

    Transitions:
        NOT_STARTED -> RUNNING       : pipeline starts
        RUNNING     -> APPROVED      : consensus >= threshold
        RUNNING     -> PARTIAL       : pipeline interrupted (some reviewers done)
        RUNNING     -> FAILED        : pipeline error / timeout
        RUNNING     -> REJECTED_SINGLE    : single reviewer rejects
        RUNNING     -> REJECTED_CONSENSUS : multi-reviewer consensus < threshold
        PARTIAL     -> RUNNING       : pipeline retried
        FAILED      -> RUNNING       : pipeline retried
        REJECTED_SINGLE    -> RUNNING : re-review after human feedback
        REJECTED_CONSENSUS -> RUNNING : re-review after human feedback
        any state   -> NOT_STARTED   : task moved backward (e.g., REVIEW -> BACKLOG)

    Invariants:
        - When state is NOT_STARTED: consensus_score, verdict, and cost_usd
          MUST NOT be exposed to the frontend.
        - Only terminal states (APPROVED, REJECTED_*) carry meaningful
          consensus/verdict data.
        - RUNNING is transient -- the pipeline is actively executing.
    """

    NOT_STARTED = "not_started"
    RUNNING = "running"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED_SINGLE = "rejected_single"
    REJECTED_CONSENSUS = "rejected_consensus"
    APPROVED = "approved"


# Valid lifecycle state transitions.
# Maps current state -> set of allowed next states.
REVIEW_LIFECYCLE_TRANSITIONS: dict[ReviewLifecycleState, set[ReviewLifecycleState]] = {
    ReviewLifecycleState.NOT_STARTED: {ReviewLifecycleState.RUNNING},
    ReviewLifecycleState.RUNNING: {
        ReviewLifecycleState.APPROVED,
        ReviewLifecycleState.PARTIAL,
        ReviewLifecycleState.FAILED,
        ReviewLifecycleState.REJECTED_SINGLE,
        ReviewLifecycleState.REJECTED_CONSENSUS,
    },
    ReviewLifecycleState.PARTIAL: {ReviewLifecycleState.RUNNING, ReviewLifecycleState.NOT_STARTED},
    ReviewLifecycleState.FAILED: {ReviewLifecycleState.RUNNING, ReviewLifecycleState.NOT_STARTED},
    ReviewLifecycleState.REJECTED_SINGLE: {
        ReviewLifecycleState.RUNNING,
        ReviewLifecycleState.NOT_STARTED,
    },
    ReviewLifecycleState.REJECTED_CONSENSUS: {
        ReviewLifecycleState.RUNNING,
        ReviewLifecycleState.NOT_STARTED,
    },
    ReviewLifecycleState.APPROVED: {ReviewLifecycleState.RUNNING, ReviewLifecycleState.NOT_STARTED},
}


class ExecutorType(StrEnum):
    """Executor classification per PRD Section 4.2."""

    CODE = "code"
    AGENT = "agent"
    SCHEDULED = "scheduled"


class Project(BaseModel):
    """A managed project in the orchestrator portfolio."""

    model_config = {"from_attributes": True}

    id: str
    name: str
    repo_path: Path | None = None
    workspace_path: Path | None = None
    tasks_file: str = "TASKS.md"
    executor_type: ExecutorType
    max_concurrency: int = 1
    env_keys: list[str] = Field(default_factory=list)
    claude_md_path: Path | None = None
    is_primary: bool = False


class LLMReview(BaseModel):
    """A single reviewer's verdict."""

    model_config = {"from_attributes": True}

    model: str
    focus: str
    verdict: str
    summary: str
    suggestions: list[str] = Field(default_factory=list)
    raw_response: str = ""
    cost_usd: float | None = None
    timestamp: datetime


class ReviewState(BaseModel):
    """Aggregate review state for a task."""

    model_config = {"from_attributes": True}

    rounds_total: int = 3
    rounds_completed: int = 0
    reviews: list[LLMReview] = Field(default_factory=list)
    consensus_score: float | None = None
    human_decision_needed: bool = False
    decision_points: list[str] = Field(default_factory=list)
    human_choice: str | None = None
    lifecycle_state: str = ReviewLifecycleState.NOT_STARTED


class ExecutionState(BaseModel):
    """Runtime execution state for a task."""

    model_config = {"from_attributes": True}

    started_at: datetime | None = None
    finished_at: datetime | None = None
    retry_count: int = 0
    max_retries: int = 3
    exit_code: int | None = None
    log_tail: list[str] = Field(default_factory=list)
    result: str = "pending"
    error_summary: str | None = None
    error_type: str | None = None


class Task(BaseModel):
    """A single orchestrated task."""

    model_config = {"from_attributes": True}

    id: str
    project_id: str
    local_task_id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.BACKLOG
    executor_type: ExecutorType
    depends_on: list[str] = Field(default_factory=list)
    review: ReviewState | None = None
    execution: ExecutionState | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    review_status: str = "idle"
    review_lifecycle_state: str = ReviewLifecycleState.NOT_STARTED


class Dependency(BaseModel):
    """Cross-project dependency link."""

    model_config = {"from_attributes": True}

    upstream_task: str
    downstream_task: str
    contract_path: str | None = None
    fulfilled: bool = False
