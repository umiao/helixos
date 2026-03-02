"""Project directory validation for HelixOS orchestrator.

Validates a local directory for import as a managed project.
Checks for .git, TASKS.md, and CLAUDE.md presence and reports
warnings and limited-mode reasons.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
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


def validate_project_directory(
    directory: Path,
    suggested_id: str,
) -> ValidationResult:
    """Validate a directory for import as a HelixOS project.

    Checks:
      - Directory exists and is a directory
      - ``.git/`` is present (required -- invalid without it)
      - ``TASKS.md`` is present (optional -- limited mode if missing)
      - ``CLAUDE.md`` is present (optional -- limited mode if missing)

    Args:
        directory: Absolute or relative path to the project directory.
        suggested_id: Pre-computed suggested project ID (e.g. ``"P1"``).

    Returns:
        A ``ValidationResult`` with validity, presence flags,
        warnings, and limited-mode reasons.
    """
    directory = directory.expanduser().resolve()
    name = directory.name

    warnings: list[str] = []
    limited_mode_reasons: list[str] = []

    if not directory.exists():
        return ValidationResult(
            valid=False,
            name=name,
            path=str(directory),
            has_git=False,
            has_tasks_md=False,
            has_claude_config=False,
            suggested_id=suggested_id,
            warnings=["Directory does not exist"],
        )

    if not directory.is_dir():
        return ValidationResult(
            valid=False,
            name=name,
            path=str(directory),
            has_git=False,
            has_tasks_md=False,
            has_claude_config=False,
            suggested_id=suggested_id,
            warnings=["Path is not a directory"],
        )

    has_git = (directory / ".git").is_dir()
    has_tasks_md = (directory / "TASKS.md").is_file()
    has_claude_config = (directory / "CLAUDE.md").is_file()

    if not has_git:
        warnings.append("No .git directory found -- git operations will not work")

    if not has_tasks_md:
        limited_mode_reasons.append("No TASKS.md -- task sync disabled")
        warnings.append("TASKS.md not found -- project will run in limited mode")

    if not has_claude_config:
        limited_mode_reasons.append("No CLAUDE.md -- Claude context unavailable")
        warnings.append("CLAUDE.md not found -- project will run in limited mode")

    # Valid if the directory exists (git is a warning, not a blocker per AC)
    valid = True

    return ValidationResult(
        valid=valid,
        name=name,
        path=str(directory),
        has_git=has_git,
        has_tasks_md=has_tasks_md,
        has_claude_config=has_claude_config,
        suggested_id=suggested_id,
        warnings=warnings,
        limited_mode_reasons=limited_mode_reasons,
    )
