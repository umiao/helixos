"""Tests for T-P0-45: Generic default project selection via is_primary field."""

from __future__ import annotations

from pathlib import Path

from src.config import (
    OrchestratorConfig,
    ProjectConfig,
    ProjectRegistry,
    load_config,
)
from src.models import ExecutorType, Project
from src.schemas import ProjectDetailResponse, ProjectResponse

# ------------------------------------------------------------------
# ProjectConfig model tests
# ------------------------------------------------------------------


class TestProjectConfigIsPrimary:
    """Test is_primary field on ProjectConfig."""

    def test_default_is_false(self) -> None:
        """is_primary defaults to False when not specified."""
        pc = ProjectConfig(name="Test", executor_type=ExecutorType.CODE)
        assert pc.is_primary is False

    def test_explicit_true(self) -> None:
        """is_primary can be set to True."""
        pc = ProjectConfig(name="Test", executor_type=ExecutorType.CODE, is_primary=True)
        assert pc.is_primary is True

    def test_explicit_false(self) -> None:
        """is_primary can be explicitly set to False."""
        pc = ProjectConfig(name="Test", executor_type=ExecutorType.CODE, is_primary=False)
        assert pc.is_primary is False


# ------------------------------------------------------------------
# Project model tests
# ------------------------------------------------------------------


class TestProjectModelIsPrimary:
    """Test is_primary field on Project domain model."""

    def test_default_is_false(self) -> None:
        """Project.is_primary defaults to False."""
        p = Project(
            id="p1", name="Test", executor_type=ExecutorType.CODE,
        )
        assert p.is_primary is False

    def test_explicit_true(self) -> None:
        """Project.is_primary can be True."""
        p = Project(
            id="p1", name="Test", executor_type=ExecutorType.CODE, is_primary=True,
        )
        assert p.is_primary is True


# ------------------------------------------------------------------
# YAML config loader tests
# ------------------------------------------------------------------


class TestYamlIsPrimary:
    """Test is_primary flows through YAML -> config -> registry."""

    def test_yaml_with_is_primary(self, tmp_path: Path) -> None:
        """is_primary: true in YAML is parsed correctly."""
        config_yaml = """\
orchestrator:
  unified_env_path: "~/.helixos/.env"
  state_db_path: "~/.helixos/state.db"

projects:
  proj_a:
    name: "ProjectA"
    repo_path: "~/projects/a"
    executor_type: "code"
    is_primary: true
  proj_b:
    name: "ProjectB"
    repo_path: "~/projects/b"
    executor_type: "code"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_yaml, encoding="utf-8")

        config = load_config(config_file)
        assert config.projects["proj_a"].is_primary is True
        assert config.projects["proj_b"].is_primary is False

    def test_yaml_without_is_primary(self, tmp_path: Path) -> None:
        """Config without is_primary defaults to False (backward compat)."""
        config_yaml = """\
orchestrator:
  unified_env_path: "~/.helixos/.env"
  state_db_path: "~/.helixos/state.db"

projects:
  proj_a:
    name: "ProjectA"
    executor_type: "code"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_yaml, encoding="utf-8")

        config = load_config(config_file)
        assert config.projects["proj_a"].is_primary is False


# ------------------------------------------------------------------
# ProjectRegistry tests
# ------------------------------------------------------------------


class TestRegistryIsPrimary:
    """Test is_primary propagates through ProjectRegistry."""

    def _make_registry(self, projects: dict[str, ProjectConfig]) -> ProjectRegistry:
        """Helper to create a registry from project configs."""
        config = OrchestratorConfig(projects=projects)
        return ProjectRegistry(config)

    def test_is_primary_propagates(self) -> None:
        """is_primary flows from ProjectConfig to Project via registry."""
        registry = self._make_registry({
            "p1": ProjectConfig(name="Primary", executor_type=ExecutorType.CODE, is_primary=True),
            "p2": ProjectConfig(name="Secondary", executor_type=ExecutorType.CODE, is_primary=False),
        })
        projects = registry.list_projects()
        primary = [p for p in projects if p.id == "p1"][0]
        secondary = [p for p in projects if p.id == "p2"][0]
        assert primary.is_primary is True
        assert secondary.is_primary is False

    def test_no_primary_defaults_all_false(self) -> None:
        """When no project has is_primary, all are False."""
        registry = self._make_registry({
            "p1": ProjectConfig(name="A", executor_type=ExecutorType.CODE),
            "p2": ProjectConfig(name="B", executor_type=ExecutorType.CODE),
        })
        for p in registry.list_projects():
            assert p.is_primary is False


# ------------------------------------------------------------------
# API schema tests
# ------------------------------------------------------------------


class TestApiSchemaIsPrimary:
    """Test is_primary on API response schemas."""

    def test_project_response_default(self) -> None:
        """ProjectResponse.is_primary defaults to False."""
        resp = ProjectResponse(
            id="p1", name="Test", executor_type=ExecutorType.CODE,
        )
        assert resp.is_primary is False

    def test_project_response_true(self) -> None:
        """ProjectResponse.is_primary can be True."""
        resp = ProjectResponse(
            id="p1", name="Test", executor_type=ExecutorType.CODE, is_primary=True,
        )
        assert resp.is_primary is True

    def test_project_detail_response_default(self) -> None:
        """ProjectDetailResponse.is_primary defaults to False."""
        resp = ProjectDetailResponse(
            id="p1", name="Test", executor_type=ExecutorType.CODE,
        )
        assert resp.is_primary is False

    def test_project_detail_response_true(self) -> None:
        """ProjectDetailResponse.is_primary can be True."""
        resp = ProjectDetailResponse(
            id="p1", name="Test", executor_type=ExecutorType.CODE, is_primary=True,
        )
        assert resp.is_primary is True

    def test_serialization_includes_is_primary(self) -> None:
        """is_primary appears in serialized JSON output."""
        resp = ProjectResponse(
            id="p1", name="Test", executor_type=ExecutorType.CODE, is_primary=True,
        )
        data = resp.model_dump()
        assert "is_primary" in data
        assert data["is_primary"] is True
