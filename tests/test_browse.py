"""Tests for the GET /api/filesystem/browse endpoint.

Verifies $HOME sandbox, directory listing with project indicators,
hidden directory filtering, and error handling.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import api_router  # noqa: I001

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def browse_app():
    """Minimal FastAPI app with only the api_router (browse needs no state)."""
    from fastapi import FastAPI

    app = FastAPI(title="HelixOS Browse Test")
    app.include_router(api_router)

    # Provide minimal app.state to avoid AttributeError on other endpoints
    app.state.config = MagicMock()
    app.state.task_manager = MagicMock()
    app.state.registry = MagicMock()
    app.state.env_loader = MagicMock()
    app.state.event_bus = MagicMock()
    app.state.scheduler = MagicMock()
    app.state.review_pipeline = None
    app.state.engine = None
    app.state._config_path = Path("orchestrator_config.yaml")
    app.state.port_registry = MagicMock()
    app.state.subprocess_registry = MagicMock()
    app.state.process_manager = MagicMock()

    return app


@pytest.fixture
async def client(browse_app):
    """Async HTTP client against the browse test app."""
    transport = ASGITransport(app=browse_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_default_returns_home(client, tmp_path):
    """GET /api/filesystem/browse with no path returns home directory entries."""
    # Create a fake home with subdirectories
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "projects").mkdir()
    (fake_home / "documents").mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get("/api/filesystem/browse")

    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == str(fake_home)
    assert data["parent"] is None  # At home, no parent
    names = [e["name"] for e in data["entries"]]
    assert "projects" in names
    assert "documents" in names


@pytest.mark.asyncio
async def test_browse_subdirectory(client, tmp_path):
    """Browse into a subdirectory within home."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    projects = fake_home / "projects"
    projects.mkdir()
    (projects / "app-a").mkdir()
    (projects / "app-b").mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get(
            "/api/filesystem/browse",
            params={"path": str(projects)},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == str(projects)
    assert data["parent"] == str(fake_home)
    names = [e["name"] for e in data["entries"]]
    assert "app-a" in names
    assert "app-b" in names


@pytest.mark.asyncio
async def test_browse_project_indicators(client, tmp_path):
    """Entries show has_git, has_tasks_md, has_claude_md flags."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    # A project with all indicators
    full = fake_home / "full-project"
    full.mkdir()
    (full / ".git").mkdir()
    (full / "TASKS.md").write_text("# Tasks", encoding="utf-8")
    (full / "CLAUDE.md").write_text("# Claude", encoding="utf-8")

    # A project with no indicators
    bare = fake_home / "bare-project"
    bare.mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get("/api/filesystem/browse")

    assert resp.status_code == 200
    entries = {e["name"]: e for e in resp.json()["entries"]}

    assert entries["full-project"]["has_git"] is True
    assert entries["full-project"]["has_tasks_md"] is True
    assert entries["full-project"]["has_claude_md"] is True

    assert entries["bare-project"]["has_git"] is False
    assert entries["bare-project"]["has_tasks_md"] is False
    assert entries["bare-project"]["has_claude_md"] is False


@pytest.mark.asyncio
async def test_browse_hides_dotdirs(client, tmp_path):
    """Hidden directories (starting with '.') are excluded from results."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / ".hidden").mkdir()
    (fake_home / "visible").mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get("/api/filesystem/browse")

    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["entries"]]
    assert "visible" in names
    assert ".hidden" not in names


@pytest.mark.asyncio
async def test_browse_excludes_files(client, tmp_path):
    """Only directories are returned, not files."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "subdir").mkdir()
    (fake_home / "readme.txt").write_text("hello", encoding="utf-8")

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get("/api/filesystem/browse")

    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["entries"]]
    assert "subdir" in names
    assert "readme.txt" not in names


@pytest.mark.asyncio
async def test_browse_sandbox_rejects_outside_home(client, tmp_path):
    """Paths outside $HOME are rejected with 400."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get(
            "/api/filesystem/browse",
            params={"path": str(outside)},
        )

    assert resp.status_code == 400
    assert "outside the home directory" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_browse_nonexistent_path(client, tmp_path):
    """Non-existent path returns 400."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    nonexistent = fake_home / "does-not-exist"

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get(
            "/api/filesystem/browse",
            params={"path": str(nonexistent)},
        )

    assert resp.status_code == 400
    assert "Not a directory" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_browse_empty_directory(client, tmp_path):
    """Empty directory returns empty entries list."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    empty = fake_home / "empty"
    empty.mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get(
            "/api/filesystem/browse",
            params={"path": str(empty)},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["entries"] == []
    assert data["parent"] == str(fake_home)


@pytest.mark.asyncio
async def test_browse_entries_sorted_case_insensitive(client, tmp_path):
    """Entries are sorted case-insensitively by name."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "Zebra").mkdir()
    (fake_home / "apple").mkdir()
    (fake_home / "Banana").mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get("/api/filesystem/browse")

    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()["entries"]]
    assert names == ["apple", "Banana", "Zebra"]


@pytest.mark.asyncio
async def test_browse_tilde_path_expansion(client, tmp_path):
    """Path with ~ expands to home directory."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "projects").mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        # ~ should expand to fake_home
        resp = await client.get(
            "/api/filesystem/browse",
            params={"path": str(fake_home)},
        )

    assert resp.status_code == 200
    assert resp.json()["path"] == str(fake_home)


@pytest.mark.asyncio
async def test_browse_all_entries_are_dirs(client, tmp_path):
    """Every entry has is_dir=True."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "dir1").mkdir()
    (fake_home / "dir2").mkdir()

    with patch("src.routes.projects.Path.home", return_value=fake_home):
        resp = await client.get("/api/filesystem/browse")

    assert resp.status_code == 200
    for entry in resp.json()["entries"]:
        assert entry["is_dir"] is True
