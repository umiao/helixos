"""Project registry and YAML config loader for HelixOS orchestrator.

Loads ``orchestrator_config.yaml`` into validated Pydantic models matching
PRD Section 6.2 and provides a ``ProjectRegistry`` for looking up projects.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from src.models import ExecutorType, Project

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Config sub-models
# ------------------------------------------------------------------


class OrchestratorSettings(BaseModel):
    """The ``orchestrator:`` section -- core runtime settings."""

    global_concurrency_limit: int = 3
    per_project_concurrency: int = 1
    review_consensus_threshold: float = 0.8
    session_timeout_minutes: int = 60
    subprocess_terminate_grace_seconds: int = 5
    unified_env_path: Path = Path("~/.helixos/.env")
    state_db_path: Path = Path("~/.helixos/state.db")

    @model_validator(mode="after")
    def _expand_paths(self) -> OrchestratorSettings:
        """Expand ``~`` in all path fields."""
        self.unified_env_path = self.unified_env_path.expanduser()
        self.state_db_path = self.state_db_path.expanduser()
        return self


class ProjectConfig(BaseModel):
    """A single project entry from the ``projects:`` section."""

    name: str
    repo_path: Path | None = None
    workspace_path: Path | None = None
    executor_type: ExecutorType = ExecutorType.CODE
    tasks_file: str = "TASKS.md"
    max_concurrency: int = 1
    env_keys: list[str] = Field(default_factory=list)
    claude_md_path: Path | None = None
    status_sections: dict[str, str] | None = None

    @model_validator(mode="after")
    def _expand_paths(self) -> ProjectConfig:
        """Expand ``~`` in all path fields."""
        if self.repo_path is not None:
            self.repo_path = self.repo_path.expanduser()
        if self.workspace_path is not None:
            self.workspace_path = self.workspace_path.expanduser()
        if self.claude_md_path is not None:
            self.claude_md_path = self.claude_md_path.expanduser()
        return self


class StagedSafetyCheck(BaseModel):
    """Git staged-file safety limits."""

    max_files: int = 50
    max_total_size_mb: int = 10


class GitConfig(BaseModel):
    """The ``git:`` section -- auto-commit settings."""

    auto_commit: bool = True
    commit_message_template: str = "[helixos] {project}: {task_id} {task_title}"
    staged_safety_check: StagedSafetyCheck = Field(
        default_factory=StagedSafetyCheck,
    )


class ReviewerConfig(BaseModel):
    """A single reviewer entry in the review pipeline."""

    model: str
    focus: str
    api: str = "anthropic"
    required: bool = True


class ReviewPipelineConfig(BaseModel):
    """The ``review_pipeline:`` section."""

    reviewers: list[ReviewerConfig] = Field(default_factory=list)


class DependencyConfig(BaseModel):
    """A cross-project dependency entry."""

    upstream: str
    downstream: str
    contract: str | None = None


# ------------------------------------------------------------------
# Top-level config
# ------------------------------------------------------------------


class OrchestratorConfig(BaseModel):
    """Top-level YAML config matching ``orchestrator_config.yaml``."""

    orchestrator: OrchestratorSettings = Field(
        default_factory=OrchestratorSettings,
    )
    projects: dict[str, ProjectConfig] = Field(default_factory=dict)
    git: GitConfig = Field(default_factory=GitConfig)
    review_pipeline: ReviewPipelineConfig = Field(
        default_factory=ReviewPipelineConfig,
    )
    dependencies: list[DependencyConfig] = Field(default_factory=list)


# ------------------------------------------------------------------
# YAML loader
# ------------------------------------------------------------------


def load_config(path: Path) -> OrchestratorConfig:
    """Parse and validate ``orchestrator_config.yaml`` at *path*.

    Returns a fully validated ``OrchestratorConfig`` with all paths
    expanded via ``Path.expanduser()``.

    Raises ``FileNotFoundError`` if the file does not exist.
    Raises ``yaml.YAMLError`` for malformed YAML.
    Raises ``pydantic.ValidationError`` for schema violations.
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raw = {}

    return OrchestratorConfig.model_validate(raw)


# ------------------------------------------------------------------
# Project registry
# ------------------------------------------------------------------


class ProjectRegistry:
    """In-memory registry that converts YAML project configs to ``Project`` models.

    Provides lookup by project ID and listing of all registered projects.
    """

    def __init__(self, config: OrchestratorConfig) -> None:
        """Build the registry from a loaded config.

        Logs warnings for projects whose ``repo_path`` does not exist on disk
        (the project is still registered -- the repo may not be cloned yet).
        """
        self._config = config
        self._projects: dict[str, Project] = {}
        self._build()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_project(self, project_id: str) -> Project:
        """Return the ``Project`` model for *project_id*.

        Raises ``KeyError`` if the project is not registered.
        """
        if project_id not in self._projects:
            raise KeyError(f"Unknown project: {project_id!r}")
        return self._projects[project_id]

    def list_projects(self) -> list[Project]:
        """Return all registered projects (order matches config)."""
        return list(self._projects.values())

    def get_project_config(self, project_id: str) -> ProjectConfig:
        """Return the raw ``ProjectConfig`` for *project_id*.

        Raises ``KeyError`` if the project is not registered.
        """
        if project_id not in self._config.projects:
            raise KeyError(f"Unknown project: {project_id!r}")
        return self._config.projects[project_id]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build(self) -> None:
        """Convert each ``ProjectConfig`` to a ``Project`` model."""
        for project_id, pc in self._config.projects.items():
            project = Project(
                id=project_id,
                name=pc.name,
                repo_path=pc.repo_path,
                workspace_path=pc.workspace_path,
                tasks_file=pc.tasks_file,
                executor_type=pc.executor_type,
                max_concurrency=pc.max_concurrency,
                env_keys=pc.env_keys,
                claude_md_path=pc.claude_md_path,
            )
            self._projects[project_id] = project

            # Warn if repo_path is set but doesn't exist on disk
            if pc.repo_path is not None and not pc.repo_path.is_dir():
                logger.warning(
                    "Project %s: repo_path does not exist: %s",
                    project_id,
                    pc.repo_path,
                )
