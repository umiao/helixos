"""Tests for src/config.py -- YAML config loader and ProjectRegistry."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.config import (
    DependencyConfig,
    GitConfig,
    OrchestratorConfig,
    OrchestratorSettings,
    PortRange,
    ProjectConfig,
    ProjectRegistry,
    ReviewerConfig,
    StagedSafetyCheck,
    load_config,
)
from src.models import ExecutorType

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

MINIMAL_YAML = """\
orchestrator:
  global_concurrency_limit: 2
  unified_env_path: "~/.helixos/.env"
  state_db_path: "~/.helixos/state.db"

projects:
  P0:
    name: "TestProject"
    repo_path: "~/projects/test"
    executor_type: "code"
"""

FULL_YAML = """\
orchestrator:
  global_concurrency_limit: 3
  per_project_concurrency: 1
  review_consensus_threshold: 0.8
  session_timeout_minutes: 60
  subprocess_terminate_grace_seconds: 5
  unified_env_path: "~/.helixos/.env"
  state_db_path: "~/.helixos/state.db"

projects:
  P0:
    name: "HelixOS"
    repo_path: "~/projects/helixos"
    executor_type: "code"
    tasks_file: "TASKS.md"
    max_concurrency: 1
  P1:
    name: "Job Hunter"
    repo_path: "~/projects/job-hunter"
    executor_type: "code"
    tasks_file: "TASKS.md"
    claude_md_path: "~/projects/job-hunter/claude.md"
    max_concurrency: 1
  P2:
    name: "Blog Reorg"
    workspace_path: "~/projects/blog/workspace"
    executor_type: "agent"
    tasks_file: "TASKS.md"
    env_keys:
      - BLOG_API_KEY
      - BLOG_SECRET

git:
  auto_commit: true
  commit_message_template: "[helixos] {project}: {task_id} {task_title}"
  staged_safety_check:
    max_files: 50
    max_total_size_mb: 10

review_pipeline:
  reviewers:
    - model: "claude-sonnet-4-5"
      focus: "feasibility_and_edge_cases"
      api: "claude_cli"
      required: true
    - model: "claude-sonnet-4-5"
      focus: "adversarial_red_team"
      api: "claude_cli"
      required: false

dependencies:
  - upstream: "P2:T-structured-output"
    downstream: "P3:T-import-corpus"
    contract: "contracts/blog_corpus_v1.json"
"""


P2_FIELDS_YAML = """\
orchestrator:
  global_concurrency_limit: 3
  port_ranges:
    frontend:
      min_port: 4000
      max_port: 4999
    backend:
      min_port: 9000
      max_port: 9999
  max_total_subprocesses: 8

projects:
  P0:
    name: "Frontend App"
    repo_path: "~/projects/frontend"
    executor_type: "code"
    launch_command: "npm run dev"
    project_type: "frontend"
    preferred_port: 4200
  P1:
    name: "Backend API"
    repo_path: "~/projects/backend"
    executor_type: "code"
    launch_command: "python -m uvicorn main:app"
    project_type: "backend"
    preferred_port: 9100
  P2:
    name: "Legacy Tool"
    repo_path: "~/projects/legacy"
    executor_type: "code"
"""


def _write_yaml(tmp_path: Path, content: str) -> Path:
    """Write YAML content to a temp file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ------------------------------------------------------------------
# PortRange tests
# ------------------------------------------------------------------


class TestPortRange:
    """Tests for the PortRange model."""

    def test_valid_range(self) -> None:
        """Valid port range is accepted."""
        pr = PortRange(min_port=3000, max_port=4000)
        assert pr.min_port == 3000
        assert pr.max_port == 4000

    def test_equal_ports(self) -> None:
        """min_port == max_port is valid (single-port range)."""
        pr = PortRange(min_port=8080, max_port=8080)
        assert pr.min_port == pr.max_port

    def test_min_greater_than_max(self) -> None:
        """min_port > max_port raises ValidationError."""
        with pytest.raises(ValidationError, match="min_port"):
            PortRange(min_port=5000, max_port=3000)

    def test_port_below_1024(self) -> None:
        """Port below 1024 raises ValidationError."""
        with pytest.raises(ValidationError):
            PortRange(min_port=80, max_port=3000)

    def test_port_above_65535(self) -> None:
        """Port above 65535 raises ValidationError."""
        with pytest.raises(ValidationError):
            PortRange(min_port=3000, max_port=70000)


# ------------------------------------------------------------------
# OrchestratorSettings tests
# ------------------------------------------------------------------


class TestOrchestratorSettings:
    """Tests for the orchestrator section model."""

    def test_defaults(self) -> None:
        """Default values match PRD Section 6.2."""
        s = OrchestratorSettings()
        assert s.global_concurrency_limit == 3
        assert s.per_project_concurrency == 1
        assert s.review_consensus_threshold == 0.8
        assert s.session_timeout_minutes == 60
        assert s.subprocess_terminate_grace_seconds == 5

    def test_path_expansion(self) -> None:
        """Tilde in paths is expanded."""
        s = OrchestratorSettings(
            unified_env_path=Path("~/.helixos/.env"),
            state_db_path=Path("~/data/state.db"),
        )
        assert "~" not in str(s.unified_env_path)
        assert "~" not in str(s.state_db_path)
        assert s.unified_env_path == Path("~/.helixos/.env").expanduser()
        assert s.state_db_path == Path("~/data/state.db").expanduser()

    def test_port_ranges_defaults(self) -> None:
        """Default port_ranges contain frontend and backend."""
        s = OrchestratorSettings()
        assert "frontend" in s.port_ranges
        assert "backend" in s.port_ranges
        assert s.port_ranges["frontend"].min_port == 3100
        assert s.port_ranges["frontend"].max_port == 3999
        assert s.port_ranges["backend"].min_port == 8100
        assert s.port_ranges["backend"].max_port == 8999

    def test_port_ranges_custom(self) -> None:
        """Custom port_ranges override defaults."""
        s = OrchestratorSettings(
            port_ranges={
                "frontend": PortRange(min_port=4000, max_port=4999),
            },
        )
        assert s.port_ranges["frontend"].min_port == 4000
        assert "backend" not in s.port_ranges

    def test_max_total_subprocesses_default(self) -> None:
        """Default max_total_subprocesses is 5."""
        s = OrchestratorSettings()
        assert s.max_total_subprocesses == 5

    def test_max_total_subprocesses_custom(self) -> None:
        """Custom max_total_subprocesses is accepted."""
        s = OrchestratorSettings(max_total_subprocesses=10)
        assert s.max_total_subprocesses == 10

    def test_max_total_subprocesses_zero(self) -> None:
        """max_total_subprocesses=0 raises ValidationError (ge=1)."""
        with pytest.raises(ValidationError):
            OrchestratorSettings(max_total_subprocesses=0)


# ------------------------------------------------------------------
# ProjectConfig tests
# ------------------------------------------------------------------


class TestProjectConfig:
    """Tests for project config model."""

    def test_minimal(self) -> None:
        """Minimum required fields: name only (defaults for the rest)."""
        pc = ProjectConfig(name="Test")
        assert pc.name == "Test"
        assert pc.repo_path is None
        assert pc.executor_type == ExecutorType.CODE
        assert pc.tasks_file == "TASKS.md"
        assert pc.max_concurrency == 1
        assert pc.env_keys == []

    def test_path_expansion(self) -> None:
        """Tilde in repo_path, workspace_path, claude_md_path is expanded."""
        pc = ProjectConfig(
            name="Test",
            repo_path=Path("~/projects/test"),
            workspace_path=Path("~/ws"),
            claude_md_path=Path("~/claude.md"),
        )
        assert "~" not in str(pc.repo_path)
        assert "~" not in str(pc.workspace_path)
        assert "~" not in str(pc.claude_md_path)

    def test_executor_type_from_string(self) -> None:
        """Executor type is validated from string value."""
        pc = ProjectConfig(name="Test", executor_type="agent")
        assert pc.executor_type == ExecutorType.AGENT

    def test_invalid_executor_type(self) -> None:
        """Invalid executor_type raises ValidationError."""
        with pytest.raises(ValidationError):
            ProjectConfig(name="Test", executor_type="invalid")

    def test_env_keys(self) -> None:
        """env_keys list is preserved."""
        pc = ProjectConfig(name="Test", env_keys=["KEY_A", "KEY_B"])
        assert pc.env_keys == ["KEY_A", "KEY_B"]

    def test_launch_command_default(self) -> None:
        """launch_command defaults to None."""
        pc = ProjectConfig(name="Test")
        assert pc.launch_command is None

    def test_launch_command_set(self) -> None:
        """launch_command can be set."""
        pc = ProjectConfig(name="Test", launch_command="npm run dev")
        assert pc.launch_command == "npm run dev"

    def test_project_type_default(self) -> None:
        """project_type defaults to 'other'."""
        pc = ProjectConfig(name="Test")
        assert pc.project_type == "other"

    def test_project_type_frontend(self) -> None:
        """project_type accepts 'frontend'."""
        pc = ProjectConfig(name="Test", project_type="frontend")
        assert pc.project_type == "frontend"

    def test_project_type_backend(self) -> None:
        """project_type accepts 'backend'."""
        pc = ProjectConfig(name="Test", project_type="backend")
        assert pc.project_type == "backend"

    def test_project_type_invalid(self) -> None:
        """Invalid project_type raises ValidationError."""
        with pytest.raises(ValidationError):
            ProjectConfig(name="Test", project_type="database")

    def test_preferred_port_default(self) -> None:
        """preferred_port defaults to None."""
        pc = ProjectConfig(name="Test")
        assert pc.preferred_port is None

    def test_preferred_port_set(self) -> None:
        """preferred_port can be set to a valid port."""
        pc = ProjectConfig(name="Test", preferred_port=3000)
        assert pc.preferred_port == 3000

    def test_preferred_port_below_1024(self) -> None:
        """preferred_port below 1024 raises ValidationError."""
        with pytest.raises(ValidationError):
            ProjectConfig(name="Test", preferred_port=80)

    def test_preferred_port_above_65535(self) -> None:
        """preferred_port above 65535 raises ValidationError."""
        with pytest.raises(ValidationError):
            ProjectConfig(name="Test", preferred_port=70000)

    def test_new_fields_backward_compatible(self) -> None:
        """Existing config without new fields still loads correctly."""
        pc = ProjectConfig(
            name="Legacy",
            repo_path=Path("/tmp/legacy"),
            executor_type="code",
        )
        assert pc.launch_command is None
        assert pc.project_type == "other"
        assert pc.preferred_port is None


# ------------------------------------------------------------------
# GitConfig tests
# ------------------------------------------------------------------


class TestGitConfig:
    """Tests for git config model."""

    def test_defaults(self) -> None:
        """Default git config values."""
        gc = GitConfig()
        assert gc.auto_commit is True
        assert "{task_id}" in gc.commit_message_template
        assert gc.staged_safety_check.max_files == 50
        assert gc.staged_safety_check.max_total_size_mb == 10

    def test_custom_safety(self) -> None:
        """Custom staged safety check values."""
        gc = GitConfig(
            staged_safety_check=StagedSafetyCheck(max_files=20, max_total_size_mb=5),
        )
        assert gc.staged_safety_check.max_files == 20


# ------------------------------------------------------------------
# ReviewerConfig tests
# ------------------------------------------------------------------


class TestReviewerConfig:
    """Tests for reviewer config model."""

    def test_required_fields(self) -> None:
        """Model and focus are required."""
        rc = ReviewerConfig(model="claude-sonnet-4-5", focus="feasibility")
        assert rc.model == "claude-sonnet-4-5"
        assert rc.focus == "feasibility"
        assert rc.api == "claude_cli"
        assert rc.required is True

    def test_optional_reviewer(self) -> None:
        """Required can be set to false."""
        rc = ReviewerConfig(
            model="claude-sonnet-4-5",
            focus="adversarial",
            required=False,
        )
        assert rc.required is False


# ------------------------------------------------------------------
# DependencyConfig tests
# ------------------------------------------------------------------


class TestDependencyConfig:
    """Tests for dependency config model."""

    def test_with_contract(self) -> None:
        """Dependency with contract path."""
        dc = DependencyConfig(
            upstream="P0:T-1",
            downstream="P1:T-2",
            contract="contracts/schema.json",
        )
        assert dc.upstream == "P0:T-1"
        assert dc.contract == "contracts/schema.json"

    def test_without_contract(self) -> None:
        """Dependency without contract defaults to None."""
        dc = DependencyConfig(upstream="P0:T-1", downstream="P1:T-2")
        assert dc.contract is None


# ------------------------------------------------------------------
# OrchestratorConfig (top-level) tests
# ------------------------------------------------------------------


class TestOrchestratorConfig:
    """Tests for the top-level config model."""

    def test_empty_yaml_uses_defaults(self) -> None:
        """Empty dict yields all defaults."""
        cfg = OrchestratorConfig.model_validate({})
        assert cfg.orchestrator.global_concurrency_limit == 3
        assert cfg.projects == {}
        assert cfg.git.auto_commit is True
        assert cfg.review_pipeline.reviewers == []
        assert cfg.dependencies == []

    def test_full_config(self) -> None:
        """Full YAML round-trips correctly."""
        raw = yaml.safe_load(FULL_YAML)
        cfg = OrchestratorConfig.model_validate(raw)
        assert cfg.orchestrator.global_concurrency_limit == 3
        assert len(cfg.projects) == 3
        assert "P0" in cfg.projects
        assert cfg.projects["P2"].executor_type == ExecutorType.AGENT
        assert cfg.projects["P2"].env_keys == ["BLOG_API_KEY", "BLOG_SECRET"]
        assert cfg.git.auto_commit is True
        assert len(cfg.review_pipeline.reviewers) == 2
        assert cfg.review_pipeline.reviewers[0].required is True
        assert cfg.review_pipeline.reviewers[1].required is False
        assert len(cfg.dependencies) == 1
        assert cfg.dependencies[0].upstream == "P2:T-structured-output"

    def test_p2_fields_config(self) -> None:
        """P2 fields (port_ranges, max_total_subprocesses, launch_command, project_type, preferred_port) round-trip."""
        raw = yaml.safe_load(P2_FIELDS_YAML)
        cfg = OrchestratorConfig.model_validate(raw)
        # Orchestrator P2 fields
        assert cfg.orchestrator.max_total_subprocesses == 8
        assert cfg.orchestrator.port_ranges["frontend"].min_port == 4000
        assert cfg.orchestrator.port_ranges["frontend"].max_port == 4999
        assert cfg.orchestrator.port_ranges["backend"].min_port == 9000
        # Project P2 fields
        assert cfg.projects["P0"].launch_command == "npm run dev"
        assert cfg.projects["P0"].project_type == "frontend"
        assert cfg.projects["P0"].preferred_port == 4200
        assert cfg.projects["P1"].launch_command == "python -m uvicorn main:app"
        assert cfg.projects["P1"].project_type == "backend"
        assert cfg.projects["P1"].preferred_port == 9100
        # Legacy project has defaults for new fields
        assert cfg.projects["P2"].launch_command is None
        assert cfg.projects["P2"].project_type == "other"
        assert cfg.projects["P2"].preferred_port is None


# ------------------------------------------------------------------
# load_config tests
# ------------------------------------------------------------------


class TestLoadConfig:
    """Tests for the YAML file loader."""

    def test_load_minimal(self, tmp_path: Path) -> None:
        """Load a minimal valid config file."""
        p = _write_yaml(tmp_path, MINIMAL_YAML)
        cfg = load_config(p)
        assert cfg.orchestrator.global_concurrency_limit == 2
        assert "P0" in cfg.projects
        assert cfg.projects["P0"].name == "TestProject"

    def test_load_full(self, tmp_path: Path) -> None:
        """Load the full sample config."""
        p = _write_yaml(tmp_path, FULL_YAML)
        cfg = load_config(p)
        assert len(cfg.projects) == 3
        assert len(cfg.dependencies) == 1

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_bad_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML raises yaml.YAMLError."""
        p = tmp_path / "bad.yaml"
        p.write_text("key: [\n  unclosed", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            load_config(p)

    def test_invalid_schema(self, tmp_path: Path) -> None:
        """Valid YAML but invalid schema raises ValidationError."""
        bad_schema = """\
orchestrator:
  global_concurrency_limit: "not_a_number"
"""
        p = _write_yaml(tmp_path, bad_schema)
        with pytest.raises(ValidationError):
            load_config(p)

    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty YAML file returns default config."""
        p = _write_yaml(tmp_path, "")
        cfg = load_config(p)
        assert cfg.orchestrator.global_concurrency_limit == 3
        assert cfg.projects == {}

    def test_path_expansion_through_load(self, tmp_path: Path) -> None:
        """Paths loaded from YAML have tilde expanded."""
        p = _write_yaml(tmp_path, MINIMAL_YAML)
        cfg = load_config(p)
        assert "~" not in str(cfg.orchestrator.unified_env_path)
        assert "~" not in str(cfg.projects["P0"].repo_path)

    def test_load_p2_fields(self, tmp_path: Path) -> None:
        """P2 fields load correctly from YAML file."""
        p = _write_yaml(tmp_path, P2_FIELDS_YAML)
        cfg = load_config(p)
        assert cfg.orchestrator.max_total_subprocesses == 8
        assert cfg.projects["P0"].launch_command == "npm run dev"
        assert cfg.projects["P0"].project_type == "frontend"
        assert cfg.projects["P0"].preferred_port == 4200

    def test_load_existing_config_unchanged(self, tmp_path: Path) -> None:
        """Existing config without P2 fields loads correctly (backward compat)."""
        p = _write_yaml(tmp_path, FULL_YAML)
        cfg = load_config(p)
        # New fields should have defaults
        assert cfg.orchestrator.max_total_subprocesses == 5
        assert "frontend" in cfg.orchestrator.port_ranges
        for pc in cfg.projects.values():
            assert pc.launch_command is None
            assert pc.project_type == "other"
            assert pc.preferred_port is None


# ------------------------------------------------------------------
# ProjectRegistry tests
# ------------------------------------------------------------------


class TestProjectRegistry:
    """Tests for the project registry."""

    @pytest.fixture()
    def full_config(self) -> OrchestratorConfig:
        """Return a fully loaded config from FULL_YAML."""
        raw = yaml.safe_load(FULL_YAML)
        return OrchestratorConfig.model_validate(raw)

    @pytest.fixture()
    def registry(self, full_config: OrchestratorConfig) -> ProjectRegistry:
        """Return a registry built from the full config."""
        return ProjectRegistry(full_config)

    def test_list_projects(self, registry: ProjectRegistry) -> None:
        """list_projects returns all projects."""
        projects = registry.list_projects()
        assert len(projects) == 3
        ids = {p.id for p in projects}
        assert ids == {"P0", "P1", "P2"}

    def test_get_project(self, registry: ProjectRegistry) -> None:
        """get_project returns the correct project model."""
        p = registry.get_project("P0")
        assert p.name == "HelixOS"
        assert p.executor_type == ExecutorType.CODE

    def test_get_project_unknown(self, registry: ProjectRegistry) -> None:
        """get_project raises KeyError for unknown project."""
        with pytest.raises(KeyError, match="Unknown project"):
            registry.get_project("P99")

    def test_get_project_config(self, registry: ProjectRegistry) -> None:
        """get_project_config returns the raw ProjectConfig."""
        pc = registry.get_project_config("P2")
        assert isinstance(pc, ProjectConfig)
        assert pc.name == "Blog Reorg"
        assert pc.executor_type == ExecutorType.AGENT

    def test_get_project_config_unknown(self, registry: ProjectRegistry) -> None:
        """get_project_config raises KeyError for unknown project."""
        with pytest.raises(KeyError, match="Unknown project"):
            registry.get_project_config("P99")

    def test_project_conversion_fields(self, registry: ProjectRegistry) -> None:
        """Converted Project has all expected fields from ProjectConfig."""
        p = registry.get_project("P1")
        assert p.id == "P1"
        assert p.name == "Job Hunter"
        assert p.tasks_file == "TASKS.md"
        assert p.max_concurrency == 1
        # claude_md_path should be expanded
        assert p.claude_md_path is not None
        assert "~" not in str(p.claude_md_path)

    def test_project_env_keys(self, registry: ProjectRegistry) -> None:
        """env_keys are carried through to the Project model."""
        p = registry.get_project("P2")
        assert p.env_keys == ["BLOG_API_KEY", "BLOG_SECRET"]

    def test_project_paths_expanded(self, registry: ProjectRegistry) -> None:
        """All project paths have tilde expanded."""
        p = registry.get_project("P0")
        assert p.repo_path is not None
        assert "~" not in str(p.repo_path)

    def test_missing_repo_path_warning(
        self, full_config: OrchestratorConfig, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Warning is logged when repo_path does not exist on disk."""
        with caplog.at_level(logging.WARNING):
            ProjectRegistry(full_config)
        # All projects in full_config have non-existent repo_paths (expanded ~/projects/...)
        # so we should see warnings
        assert any("repo_path does not exist" in msg for msg in caplog.messages)

    def test_none_repo_path_no_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No warning when repo_path is None (agent-only project)."""
        cfg = OrchestratorConfig.model_validate(
            {
                "projects": {
                    "A1": {
                        "name": "Agent Only",
                        "executor_type": "agent",
                        "workspace_path": "/tmp/ws",
                    },
                },
            }
        )
        with caplog.at_level(logging.WARNING):
            registry = ProjectRegistry(cfg)
        assert not any("repo_path" in msg for msg in caplog.messages)
        assert registry.get_project("A1").repo_path is None

    def test_auto_detect_claude_md(self, tmp_path: Path) -> None:
        """Auto-detects CLAUDE.md when file exists at repo_path."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")

        cfg = OrchestratorConfig.model_validate(
            {
                "projects": {
                    "P0": {
                        "name": "My Project",
                        "repo_path": str(project_dir),
                    },
                },
            }
        )
        registry = ProjectRegistry(cfg)
        p = registry.get_project("P0")
        assert p.claude_md_path is not None
        assert p.claude_md_path == project_dir / "CLAUDE.md"

    def test_no_auto_detect_when_claude_md_missing(self, tmp_path: Path) -> None:
        """claude_md_path stays None when CLAUDE.md does not exist."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()

        cfg = OrchestratorConfig.model_validate(
            {
                "projects": {
                    "P0": {
                        "name": "My Project",
                        "repo_path": str(project_dir),
                    },
                },
            }
        )
        registry = ProjectRegistry(cfg)
        p = registry.get_project("P0")
        assert p.claude_md_path is None

    def test_explicit_claude_md_path_not_overridden(self, tmp_path: Path) -> None:
        """Explicit claude_md_path in config is not overridden by auto-detect."""
        project_dir = tmp_path / "myproject"
        project_dir.mkdir()
        (project_dir / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
        custom_path = tmp_path / "custom_claude.md"
        custom_path.write_text("# Custom\n", encoding="utf-8")

        cfg = OrchestratorConfig.model_validate(
            {
                "projects": {
                    "P0": {
                        "name": "My Project",
                        "repo_path": str(project_dir),
                        "claude_md_path": str(custom_path),
                    },
                },
            }
        )
        registry = ProjectRegistry(cfg)
        p = registry.get_project("P0")
        assert p.claude_md_path == custom_path

    def test_empty_config(self) -> None:
        """Registry from empty config has no projects."""
        cfg = OrchestratorConfig.model_validate({})
        registry = ProjectRegistry(cfg)
        assert registry.list_projects() == []
