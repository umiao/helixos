"""Abstract executor interface and result model.

Defines the ``ExecutorResult`` data model and the ``BaseExecutor`` ABC
that all concrete executors (Code, Agent, Scheduled) must implement.
Per PRD Section 7.1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from pydantic import BaseModel, Field

from src.models import Project, Task


class ExecutorResult(BaseModel):
    """Result of a task execution."""

    success: bool
    exit_code: int
    log_lines: list[str] = Field(default_factory=list)
    error_summary: str | None = None
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
    ) -> ExecutorResult:
        """Execute a task and return the result."""
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel a running execution."""
        ...
