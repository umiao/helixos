"""Abstract executor interface and result model.

Defines the ``ExecutorResult`` data model and the ``BaseExecutor`` ABC
that all concrete executors (Code, Agent, Scheduled) must implement.
Per PRD Section 7.1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum

from pydantic import BaseModel, Field

from src.models import Project, Task


class ErrorType(StrEnum):
    """Structured error classification for executor results."""

    INFRA = "infra"
    CLI_NOT_FOUND = "cli_not_found"
    REPO_NOT_FOUND = "repo_not_found"
    NON_ZERO_EXIT = "non_zero_exit"
    TIMEOUT = "timeout"
    INACTIVITY_TIMEOUT = "inactivity_timeout"
    UNKNOWN = "unknown"


class ExecutorResult(BaseModel):
    """Result of a task execution."""

    success: bool
    exit_code: int
    log_lines: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    error_type: ErrorType | None = None
    stderr_output: str | None = None
    outputs: list[str] = Field(default_factory=list)
    duration_seconds: float


class BaseExecutor(ABC):
    """Abstract base class for all task executors.

    Subclasses implement ``execute()`` to run a task and ``cancel()``
    to abort a running execution.
    """

    @abstractmethod
    async def execute(
        self,
        task: Task,
        project: Project,
        env: dict[str, str],
        on_log: Callable[[str], None],
        on_stream_event: Callable[[dict], None] | None = None,
        review_feedback: str | None = None,
    ) -> ExecutorResult:
        """Execute a task and return the result."""
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel a running execution."""
        ...
