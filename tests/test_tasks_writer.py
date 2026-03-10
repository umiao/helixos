"""Tests for TasksWriter: filelock, ID generation, backup, validation, and API endpoint."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.tasks_writer import (
    NewTask,
    TasksWriter,
    WriteResult,
    _find_active_section_end,
    generate_next_task_id,
)

# ===================================================================
# generate_next_task_id tests
# ===================================================================


class TestGenerateNextTaskId:
    """Tests for generate_next_task_id."""

    def test_empty_content(self) -> None:
        """Returns T-P0-0 for empty content."""
        result = generate_next_task_id("", "P0")
        assert result == "T-P0-0"

    def test_one_existing_id(self) -> None:
        """Returns T-P0-1 when T-P0-0 already exists."""
        content = "#### T-P0-0: First task\n"
        result = generate_next_task_id(content, "P0")
        assert result == "T-P0-1"

    def test_multiple_existing_ids(self) -> None:
        """Returns next after the highest number."""
        content = "#### T-P0-0: A\n#### T-P0-1: B\n#### T-P0-2: C\n"
        result = generate_next_task_id(content, "P0")
        assert result == "T-P0-3"

    def test_different_priority(self) -> None:
        """Generates ID for the specified priority, not others."""
        content = "#### T-P0-0: A\n#### T-P0-1: B\n#### T-P1-0: C\n"
        result = generate_next_task_id(content, "P1")
        assert result == "T-P1-1"

    def test_gap_in_ids(self) -> None:
        """Uses max-based logic, not gap-filling."""
        content = "#### T-P0-0: A\n#### T-P0-5: B\n"
        result = generate_next_task_id(content, "P0")
        assert result == "T-P0-6"

    def test_no_matching_priority(self) -> None:
        """Returns T-P2-0 when no P2 tasks exist."""
        content = "#### T-P0-0: A\n#### T-P1-0: B\n"
        result = generate_next_task_id(content, "P2")
        assert result == "T-P2-0"


# ===================================================================
# _find_active_section_end tests
# ===================================================================


class TestFindActiveSectionEnd:
    """Tests for _find_active_section_end."""

    def test_standard_active_section(self) -> None:
        """Finds end of Active Tasks section before Completed."""
        content = "## Active Tasks\n\n#### T-P0-0: A\n\n## Completed Tasks\n"
        result = _find_active_section_end(content)
        assert result is not None
        lines = content.split("\n")
        # Should point to the "## Completed Tasks" line
        assert "## Completed Tasks" in lines[result]

    def test_no_active_section(self) -> None:
        """Returns None when no Active section exists."""
        content = "## Completed Tasks\n\n## Blocked\n"
        result = _find_active_section_end(content)
        assert result is None

    def test_active_at_end_of_file(self) -> None:
        """Returns end of file when Active is the last section."""
        content = "## Active Tasks\n\n#### T-P0-0: A\n"
        result = _find_active_section_end(content)
        assert result is not None
        assert result == len(content.split("\n"))

    def test_active_section_alias(self) -> None:
        """Recognizes '## Active' as well as '## Active Tasks'."""
        content = "## Active\n\n#### T-P0-0: A\n\n## Done\n"
        result = _find_active_section_end(content)
        assert result is not None


# ===================================================================
# TasksWriter tests
# ===================================================================


class TestTasksWriter:
    """Tests for TasksWriter.append_task."""

    def test_append_to_existing_file(self, tmp_path: Path) -> None:
        """Appends a task to an existing TASKS.md."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.append_task(NewTask(title="Build auth", priority="P0"))

        assert result.success is True
        assert result.task_id == "T-P0-0"
        content = tasks_md.read_text(encoding="utf-8")
        assert "T-P0-0: Build auth" in content

    def test_append_creates_file(self, tmp_path: Path) -> None:
        """Creates TASKS.md with minimal structure if it does not exist."""
        tasks_md = tmp_path / "TASKS.md"
        writer = TasksWriter(tasks_md)
        result = writer.append_task(NewTask(title="First task", priority="P0"))

        assert result.success is True
        assert tasks_md.is_file()
        content = tasks_md.read_text(encoding="utf-8")
        assert "T-P0-0: First task" in content
        assert "## Active Tasks" in content

    def test_append_empty_file(self, tmp_path: Path) -> None:
        """Handles empty TASKS.md by creating minimal structure."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text("", encoding="utf-8")
        writer = TasksWriter(tasks_md)
        result = writer.append_task(NewTask(title="New task", priority="P0"))

        assert result.success is True
        content = tasks_md.read_text(encoding="utf-8")
        assert "T-P0-0: New task" in content

    def test_sequential_ids(self, tmp_path: Path) -> None:
        """Sequential appends produce sequential IDs."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)

        r1 = writer.append_task(NewTask(title="Task A", priority="P0"))
        r2 = writer.append_task(NewTask(title="Task B", priority="P0"))
        r3 = writer.append_task(NewTask(title="Task C", priority="P0"))

        assert r1.task_id == "T-P0-0"
        assert r2.task_id == "T-P0-1"
        assert r3.task_id == "T-P0-2"

    def test_backup_created(self, tmp_path: Path) -> None:
        """Creates a .bak file before writing."""
        tasks_md = tmp_path / "TASKS.md"
        original_content = "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n"
        tasks_md.write_text(original_content, encoding="utf-8")

        writer = TasksWriter(tasks_md)
        result = writer.append_task(NewTask(title="New task", priority="P0"))

        assert result.success is True
        assert result.backup_path is not None
        bak_path = Path(result.backup_path)
        assert bak_path.is_file()
        bak_content = bak_path.read_text(encoding="utf-8")
        assert bak_content == original_content

    def test_no_backup_when_file_missing(self, tmp_path: Path) -> None:
        """No backup when TASKS.md did not exist before."""
        tasks_md = tmp_path / "TASKS.md"
        writer = TasksWriter(tasks_md)
        result = writer.append_task(NewTask(title="First", priority="P0"))

        assert result.success is True
        assert result.backup_path is None

    def test_with_description(self, tmp_path: Path) -> None:
        """Task description is included in the output."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.append_task(
            NewTask(title="Auth flow", description="Implement OAuth2 login", priority="P0"),
        )

        assert result.success is True
        content = tasks_md.read_text(encoding="utf-8")
        assert "Implement OAuth2 login" in content

    def test_different_priority(self, tmp_path: Path) -> None:
        """Tasks with different priorities get different ID series."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)

        r1 = writer.append_task(NewTask(title="P0 task", priority="P0"))
        r2 = writer.append_task(NewTask(title="P1 task", priority="P1"))

        assert r1.task_id == "T-P0-0"
        assert r2.task_id == "T-P1-0"

    def test_preserves_existing_tasks(self, tmp_path: Path) -> None:
        """Existing tasks in the file are preserved."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n"
            "## Active Tasks\n\n"
            "#### T-P0-0: Existing task\n"
            "- Already here\n\n"
            "## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.append_task(NewTask(title="New task", priority="P0"))

        assert result.success is True
        assert result.task_id == "T-P0-1"
        content = tasks_md.read_text(encoding="utf-8")
        assert "T-P0-0: Existing task" in content
        assert "T-P0-1: New task" in content

    def test_no_active_section_adds_one(self, tmp_path: Path) -> None:
        """If no Active section exists, one is created."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.append_task(NewTask(title="Orphan task", priority="P0"))

        assert result.success is True
        content = tasks_md.read_text(encoding="utf-8")
        assert "## Active Tasks" in content
        assert "T-P0-0: Orphan task" in content

    def test_concurrent_writes(self, tmp_path: Path) -> None:
        """File lock prevents concurrent write corruption."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        results: list[WriteResult] = []
        errors: list[Exception] = []

        def write_task(i: int) -> None:
            try:
                r = writer.append_task(NewTask(title=f"Concurrent task {i}", priority="P0"))
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_task, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 5
        assert all(r.success for r in results)

        # All IDs should be unique
        ids = {r.task_id for r in results}
        assert len(ids) == 5

        # All tasks should be in the file
        content = tasks_md.read_text(encoding="utf-8")
        for i in range(5):
            assert f"Concurrent task {i}" in content

    def test_update_task_title(self, tmp_path: Path) -> None:
        """update_task_title replaces the title in the heading line."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n"
            "## Active Tasks\n\n"
            "#### T-P0-0: Original title\n"
            "- Some description\n\n"
            "## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.update_task_title("T-P0-0", "Updated title")

        assert result is True
        content = tasks_md.read_text(encoding="utf-8")
        assert "#### T-P0-0: Updated title" in content
        assert "Original title" not in content
        # Description preserved
        assert "Some description" in content

    def test_update_task_title_special_chars(self, tmp_path: Path) -> None:
        """update_task_title handles special characters in new title."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n"
            "## Active Tasks\n\n"
            "#### T-P1-5: Old title\n\n"
            "## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.update_task_title("T-P1-5", "Title with (parens) & [brackets]")

        assert result is True
        content = tasks_md.read_text(encoding="utf-8")
        assert "#### T-P1-5: Title with (parens) & [brackets]" in content

    def test_update_task_title_not_found(self, tmp_path: Path) -> None:
        """update_task_title returns False when task is not found."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.update_task_title("T-P0-99", "New title")

        assert result is False

    def test_update_task_title_creates_backup(self, tmp_path: Path) -> None:
        """update_task_title creates a .bak backup before writing."""
        tasks_md = tmp_path / "TASKS.md"
        original = (
            "# Task Backlog\n\n"
            "## Active Tasks\n\n"
            "#### T-P0-0: Original\n\n"
            "## Completed Tasks\n"
        )
        tasks_md.write_text(original, encoding="utf-8")
        writer = TasksWriter(tasks_md)
        writer.update_task_title("T-P0-0", "New title")

        bak_path = tasks_md.with_suffix(".md.bak")
        assert bak_path.is_file()
        assert bak_path.read_text(encoding="utf-8") == original

    def test_update_task_title_file_missing(self, tmp_path: Path) -> None:
        """update_task_title returns False when TASKS.md does not exist."""
        tasks_md = tmp_path / "TASKS.md"
        writer = TasksWriter(tasks_md)
        result = writer.update_task_title("T-P0-0", "New title")
        assert result is False

    def test_id_format_variations(self, tmp_path: Path) -> None:
        """Correctly handles existing IDs with various formats."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "# Task Backlog\n\n"
            "## Active Tasks\n\n"
            "#### T-P0-0: First\n\n"
            "## Completed Tasks\n\n"
            "#### [x] T-P0-10: Old task -- 2026-01-01\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.append_task(NewTask(title="After ten", priority="P0"))

        assert result.success is True
        assert result.task_id == "T-P0-11"


# ===================================================================
# API endpoint tests
# ===================================================================


def _write_config_yaml_with_tasks(tmp_path: Path) -> tuple[Path, Path]:
    """Write a config with a project that has a TASKS.md. Returns (cfg_path, repo_path)."""
    cfg_path = tmp_path / "orchestrator_config.yaml"
    repo_path = tmp_path / "test_repo"
    repo_path.mkdir(exist_ok=True)
    (repo_path / ".git").mkdir(exist_ok=True)
    (repo_path / "TASKS.md").write_text(
        "# Task Backlog\n\n## Active Tasks\n\n## Completed Tasks\n",
        encoding="utf-8",
    )

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
        "    name: test_project\n"
        "    repo_path: '"
        + str(repo_path).replace("\\", "/")
        + "'\n",
        encoding="utf-8",
    )
    return cfg_path, repo_path


@pytest.fixture()
async def tasks_app(tmp_path: Path):
    """Test FastAPI app wired for task creation endpoint tests."""
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from src.api import api_router
    from src.config import ProjectRegistry, load_config
    from src.db import Base
    from src.events import EventBus, sse_router
    from src.port_registry import PortRegistry
    from src.task_manager import TaskManager

    cfg_path, _repo_path = _write_config_yaml_with_tasks(tmp_path)
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
async def tasks_client(tasks_app):
    """httpx AsyncClient for the task creation test app."""
    transport = ASGITransport(app=tasks_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestCreateTaskEndpoint:
    """Tests for POST /api/projects/{id}/tasks."""

    @pytest.mark.asyncio
    async def test_create_task_success(
        self,
        tasks_client: AsyncClient,
    ) -> None:
        """Creates a task and returns success response."""
        resp = await tasks_client.post(
            "/api/projects/P0/tasks",
            json={"title": "Build auth", "description": "Add OAuth2", "priority": "P0"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["task_id"] == "T-P0-0"
        assert data["synced"] is True

    @pytest.mark.asyncio
    async def test_create_task_syncs_to_db(
        self,
        tasks_client: AsyncClient,
        tasks_app,
    ) -> None:
        """Created task appears in the database after sync."""
        resp = await tasks_client.post(
            "/api/projects/P0/tasks",
            json={"title": "DB task", "priority": "P0"},
        )
        assert resp.status_code == 200

        # Query the task list
        list_resp = await tasks_client.get("/api/tasks?project_id=P0")
        assert list_resp.status_code == 200
        tasks = list_resp.json()
        task_ids = [t["local_task_id"] for t in tasks]
        assert "T-P0-0" in task_ids

    @pytest.mark.asyncio
    async def test_create_task_404_unknown_project(
        self,
        tasks_client: AsyncClient,
    ) -> None:
        """Returns 404 for an unknown project."""
        resp = await tasks_client.post(
            "/api/projects/NONEXISTENT/tasks",
            json={"title": "Test", "priority": "P0"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_task_sequential_ids(
        self,
        tasks_client: AsyncClient,
    ) -> None:
        """Multiple creates produce sequential IDs."""
        r1 = await tasks_client.post(
            "/api/projects/P0/tasks",
            json={"title": "First", "priority": "P0"},
        )
        r2 = await tasks_client.post(
            "/api/projects/P0/tasks",
            json={"title": "Second", "priority": "P0"},
        )
        assert r1.json()["task_id"] == "T-P0-0"
        assert r2.json()["task_id"] == "T-P0-1"

    @pytest.mark.asyncio
    async def test_create_task_default_priority(
        self,
        tasks_client: AsyncClient,
    ) -> None:
        """Priority defaults to P0 when not specified."""
        resp = await tasks_client.post(
            "/api/projects/P0/tasks",
            json={"title": "Default priority"},
        )
        assert resp.status_code == 200
        assert resp.json()["task_id"].startswith("T-P0-")

    @pytest.mark.asyncio
    async def test_create_task_empty_title_rejected(
        self,
        tasks_client: AsyncClient,
    ) -> None:
        """Empty title is rejected by validation."""
        resp = await tasks_client.post(
            "/api/projects/P0/tasks",
            json={"title": "", "priority": "P0"},
        )
        assert resp.status_code == 422
