"""Tests for project onboarding: config_writer, project_validator, and API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.config_writer import add_project_to_config, suggest_next_project_id
from src.project_validator import validate_project_directory

# ===================================================================
# config_writer tests
# ===================================================================


class TestSuggestNextProjectId:
    """Tests for suggest_next_project_id."""

    def test_no_config_file(self, tmp_path: Path) -> None:
        """Returns P0 when config file does not exist."""
        result = suggest_next_project_id(tmp_path / "missing.yaml")
        assert result == "P0"

    def test_empty_config(self, tmp_path: Path) -> None:
        """Returns P0 when config file has no projects section."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("orchestrator:\n  global_concurrency_limit: 3\n", encoding="utf-8")
        result = suggest_next_project_id(cfg)
        assert result == "P0"

    def test_config_with_p0(self, tmp_path: Path) -> None:
        """Returns P1 when P0 already exists."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "projects:\n  P0:\n    name: test\n",
            encoding="utf-8",
        )
        result = suggest_next_project_id(cfg)
        assert result == "P1"

    def test_config_with_gap(self, tmp_path: Path) -> None:
        """Returns P3 when P0 and P2 exist (max-based, not gap-filling)."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "projects:\n  P0:\n    name: a\n  P2:\n    name: b\n",
            encoding="utf-8",
        )
        result = suggest_next_project_id(cfg)
        assert result == "P3"

    def test_non_numeric_ids_ignored(self, tmp_path: Path) -> None:
        """Non-P\\d+ project IDs are ignored in suggestion."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "projects:\n  myproject:\n    name: a\n",
            encoding="utf-8",
        )
        result = suggest_next_project_id(cfg)
        assert result == "P0"

    def test_null_projects_section(self, tmp_path: Path) -> None:
        """Returns P0 when projects section is explicitly null."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("projects:\n", encoding="utf-8")
        result = suggest_next_project_id(cfg)
        assert result == "P0"


class TestAddProjectToConfig:
    """Tests for add_project_to_config."""

    def test_add_new_project(self, tmp_path: Path) -> None:
        """Successfully adds a project to the YAML config."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "# A comment\nprojects:\n  P0:\n    name: existing\n",
            encoding="utf-8",
        )
        add_project_to_config(cfg, "P1", {"name": "New Project", "repo_path": "/tmp/proj"})

        content = cfg.read_text(encoding="utf-8")
        assert "P1:" in content
        assert "New Project" in content
        # Comment should be preserved
        assert "# A comment" in content

    def test_add_project_duplicate_raises(self, tmp_path: Path) -> None:
        """Raises ValueError when project ID already exists."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "projects:\n  P0:\n    name: existing\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="already exists"):
            add_project_to_config(cfg, "P0", {"name": "Duplicate"})

    def test_add_project_file_not_found(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError for missing config."""
        with pytest.raises(FileNotFoundError):
            add_project_to_config(
                tmp_path / "missing.yaml", "P0", {"name": "test"},
            )

    def test_add_project_creates_projects_section(self, tmp_path: Path) -> None:
        """Creates the projects section if it doesn't exist."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "orchestrator:\n  global_concurrency_limit: 3\n",
            encoding="utf-8",
        )
        add_project_to_config(cfg, "P0", {"name": "First"})
        content = cfg.read_text(encoding="utf-8")
        assert "P0:" in content
        assert "First" in content

    def test_atomic_write_no_partial(self, tmp_path: Path) -> None:
        """No .tmp file remains after successful write."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("projects:\n", encoding="utf-8")
        add_project_to_config(cfg, "P0", {"name": "test"})
        assert not (tmp_path / "config.yaml.tmp").exists()


# ===================================================================
# project_validator tests
# ===================================================================


class TestValidateProjectDirectory:
    """Tests for validate_project_directory."""

    def test_valid_directory_with_all_files(self, tmp_path: Path) -> None:
        """Directory with .git, TASKS.md, CLAUDE.md is fully valid."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")

        result = validate_project_directory(tmp_path, "P0")
        assert result.valid is True
        assert result.has_git is True
        assert result.has_tasks_md is True
        assert result.has_claude_config is True
        assert result.warnings == []
        assert result.limited_mode_reasons == []

    def test_missing_git(self, tmp_path: Path) -> None:
        """Missing .git produces a warning."""
        (tmp_path / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")

        result = validate_project_directory(tmp_path, "P0")
        assert result.valid is True  # Still valid
        assert result.has_git is False
        assert any(".git" in w for w in result.warnings)

    def test_missing_tasks_md(self, tmp_path: Path) -> None:
        """Missing TASKS.md triggers limited mode."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")

        result = validate_project_directory(tmp_path, "P0")
        assert result.valid is True
        assert result.has_tasks_md is False
        assert len(result.limited_mode_reasons) >= 1
        assert any("TASKS.md" in r for r in result.limited_mode_reasons)

    def test_missing_claude_md(self, tmp_path: Path) -> None:
        """Missing CLAUDE.md triggers limited mode."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")

        result = validate_project_directory(tmp_path, "P0")
        assert result.valid is True
        assert result.has_claude_config is False
        assert any("CLAUDE.md" in r for r in result.limited_mode_reasons)

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Non-existent directory is invalid."""
        result = validate_project_directory(tmp_path / "nope", "P0")
        assert result.valid is False
        assert any("does not exist" in w for w in result.warnings)

    def test_path_is_a_file(self, tmp_path: Path) -> None:
        """Path pointing to a file is invalid."""
        f = tmp_path / "somefile.txt"
        f.write_text("data", encoding="utf-8")
        result = validate_project_directory(f, "P0")
        assert result.valid is False
        assert any("not a directory" in w for w in result.warnings)

    def test_suggested_id_passthrough(self, tmp_path: Path) -> None:
        """Suggested ID is passed through in the result."""
        (tmp_path / ".git").mkdir()
        result = validate_project_directory(tmp_path, "P42")
        assert result.suggested_id == "P42"

    def test_name_is_directory_name(self, tmp_path: Path) -> None:
        """Name field is set to the directory basename."""
        subdir = tmp_path / "myproject"
        subdir.mkdir()
        (subdir / ".git").mkdir()
        result = validate_project_directory(subdir, "P0")
        assert result.name == "myproject"

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory: valid but with all warnings."""
        result = validate_project_directory(tmp_path, "P0")
        assert result.valid is True
        assert result.has_git is False
        assert result.has_tasks_md is False
        assert result.has_claude_config is False
        assert len(result.warnings) == 3


# ===================================================================
# API endpoint tests
# ===================================================================


def _write_config_yaml(tmp_path: Path) -> Path:
    """Write a minimal orchestrator_config.yaml and return the path."""
    cfg_path = tmp_path / "orchestrator_config.yaml"
    repo_path = tmp_path / "existing_repo"
    repo_path.mkdir(exist_ok=True)
    (repo_path / ".git").mkdir(exist_ok=True)
    (repo_path / "TASKS.md").write_text("# Tasks\n", encoding="utf-8")

    cfg_path.write_text(
        "# test config\n"
        "orchestrator:\n"
        "  state_db_path: '"
        + str(tmp_path / "state.db").replace("\\", "/")
        + "'\n"
        "  unified_env_path: '"
        + str(tmp_path / ".env").replace("\\", "/")
        + "'\n"
        "projects:\n"
        "  P0:\n"
        "    name: existing\n"
        "    repo_path: '"
        + str(repo_path).replace("\\", "/")
        + "'\n",
        encoding="utf-8",
    )
    return cfg_path


@pytest.fixture()
async def onboarding_app(tmp_path: Path):
    """Test FastAPI app wired for onboarding endpoint tests."""
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from src.api import api_router
    from src.config import ProjectRegistry, load_config
    from src.db import Base
    from src.events import EventBus, sse_router
    from src.port_registry import PortRegistry
    from src.task_manager import TaskManager

    cfg_path = _write_config_yaml(tmp_path)
    config = load_config(cfg_path)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    task_manager = TaskManager(session_factory)
    registry = ProjectRegistry(config)
    event_bus = EventBus()
    port_registry = PortRegistry(
        config.orchestrator.port_ranges,
        tmp_path / "ports.json",
    )

    app = FastAPI(title="HelixOS Test", version="0.1.0")
    app.include_router(sse_router)
    app.include_router(api_router)

    app.state._config_path = cfg_path
    app.state.config = config
    app.state.task_manager = task_manager
    app.state.registry = registry
    app.state.env_loader = None
    app.state.event_bus = event_bus
    app.state.scheduler = None
    app.state.review_pipeline = None
    app.state.port_registry = port_registry
    app.state.engine = engine

    yield app
    await engine.dispose()


@pytest.fixture()
async def onboarding_client(onboarding_app):
    """httpx AsyncClient for the onboarding test app."""
    transport = ASGITransport(app=onboarding_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture()
def sample_project_dir(tmp_path: Path) -> Path:
    """Create a sample project directory for import tests."""
    proj = tmp_path / "sample_project"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "TASKS.md").write_text(
        "## Active Tasks\n\n#### T-P0-1: Sample task\n- A task\n",
        encoding="utf-8",
    )
    (proj / "CLAUDE.md").write_text("# Claude config\n", encoding="utf-8")
    return proj


class TestValidateEndpoint:
    """Tests for POST /api/projects/validate."""

    @pytest.mark.asyncio
    async def test_valid_dir(
        self,
        onboarding_client: AsyncClient,
        sample_project_dir: Path,
    ) -> None:
        """Returns valid for a directory with .git, TASKS.md, CLAUDE.md."""
        resp = await onboarding_client.post(
            "/api/projects/validate",
            json={"path": str(sample_project_dir)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["has_git"] is True
        assert data["has_tasks_md"] is True
        assert data["has_claude_config"] is True
        assert data["suggested_id"] == "P1"

    @pytest.mark.asyncio
    async def test_nonexistent_dir(
        self,
        onboarding_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Returns invalid for a non-existent path."""
        resp = await onboarding_client.post(
            "/api/projects/validate",
            json={"path": str(tmp_path / "no_such_dir")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False


class TestImportEndpoint:
    """Tests for POST /api/projects/import."""

    @pytest.mark.asyncio
    async def test_import_success(
        self,
        onboarding_client: AsyncClient,
        sample_project_dir: Path,
    ) -> None:
        """Imports a project and writes to YAML config."""
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={"path": str(sample_project_dir)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == "P1"
        assert data["name"] == "sample_project"
        assert data["synced"] is True

    @pytest.mark.asyncio
    async def test_import_duplicate_409(
        self,
        onboarding_client: AsyncClient,
        onboarding_app,
        tmp_path: Path,
    ) -> None:
        """Returns 409 when project ID already exists."""
        proj = tmp_path / "another_repo"
        proj.mkdir()
        (proj / ".git").mkdir()
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={"path": str(proj), "project_id": "P0"},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_import_invalid_path_400(
        self,
        onboarding_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Returns 400 when path does not exist."""
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={"path": str(tmp_path / "no_such_dir")},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_import_custom_name(
        self,
        onboarding_client: AsyncClient,
        sample_project_dir: Path,
    ) -> None:
        """Custom name overrides directory basename."""
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={
                "path": str(sample_project_dir),
                "name": "My Custom Name",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "My Custom Name"

    @pytest.mark.asyncio
    async def test_import_no_tasks_md_skips_sync(
        self,
        onboarding_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Import without TASKS.md skips sync."""
        proj = tmp_path / "no_tasks_repo"
        proj.mkdir()
        (proj / ".git").mkdir()
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={"path": str(proj)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["synced"] is False
        assert any("TASKS.md" in w for w in data["warnings"])

    @pytest.mark.asyncio
    async def test_import_assigns_port(
        self,
        onboarding_client: AsyncClient,
        sample_project_dir: Path,
    ) -> None:
        """Import auto-assigns a port from the configured range."""
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={
                "path": str(sample_project_dir),
                "project_type": "frontend",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Port should be from the frontend range (3100-3999 default)
        assert data["port"] is not None
        assert 3100 <= data["port"] <= 3999

    @pytest.mark.asyncio
    async def test_import_reloads_registry(
        self,
        onboarding_client: AsyncClient,
        onboarding_app,
        sample_project_dir: Path,
    ) -> None:
        """Import reloads the ProjectRegistry with the new project."""
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={"path": str(sample_project_dir)},
        )
        assert resp.status_code == 200
        # New project should be visible via list_projects
        registry = onboarding_app.state.registry
        project_ids = [p.id for p in registry.list_projects()]
        assert "P1" in project_ids

    @pytest.mark.asyncio
    async def test_import_auto_sets_claude_md_path(
        self,
        onboarding_client: AsyncClient,
        onboarding_app,
        sample_project_dir: Path,
    ) -> None:
        """Import auto-sets claude_md_path when CLAUDE.md exists."""
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={"path": str(sample_project_dir)},
        )
        assert resp.status_code == 200
        # Registry should have claude_md_path set via auto-detect
        registry = onboarding_app.state.registry
        project = registry.get_project("P1")
        assert project.claude_md_path is not None
        assert str(project.claude_md_path).endswith("CLAUDE.md")

    @pytest.mark.asyncio
    async def test_import_no_claude_md_warning(
        self,
        onboarding_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """Import without CLAUDE.md includes warning."""
        proj = tmp_path / "no_claude_repo"
        proj.mkdir()
        (proj / ".git").mkdir()
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={"path": str(proj)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any("CLAUDE.md" in w for w in data["warnings"])

    @pytest.mark.asyncio
    async def test_import_with_claude_md_no_warning(
        self,
        onboarding_client: AsyncClient,
        sample_project_dir: Path,
    ) -> None:
        """Import with CLAUDE.md present does not emit CLAUDE.md warning."""
        resp = await onboarding_client.post(
            "/api/projects/import",
            json={"path": str(sample_project_dir)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert not any("CLAUDE.md" in w for w in data["warnings"])
