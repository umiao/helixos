"""Tests for the unified .env loader."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from src.env_loader import EnvLoader


@dataclass
class FakeProject:
    """Minimal stand-in for a Project with ``env_keys``."""

    env_keys: list[str] = field(default_factory=list)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    """Create a temporary .env file with sample keys."""
    p = tmp_path / ".env"
    p.write_text(
        "API_KEY=sk-test-123\n"
        "DATABASE_URL=sqlite:///test.db\n"
        "SECRET_TOKEN=abc-xyz\n",
        encoding="utf-8",
    )
    return p


# ------------------------------------------------------------------
# Tests: loading
# ------------------------------------------------------------------


class TestLoading:
    """Tests for basic .env file loading behaviour."""

    def test_load_existing_file(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        all_vars = loader.get_all()
        assert all_vars == {
            "API_KEY": "sk-test-123",
            "DATABASE_URL": "sqlite:///test.db",
            "SECRET_TOKEN": "abc-xyz",
        }

    def test_missing_file_returns_empty(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        missing = tmp_path / "nonexistent.env"
        with caplog.at_level(logging.WARNING):
            loader = EnvLoader(missing)
        assert loader.get_all() == {}
        assert "not found" in caplog.text

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("", encoding="utf-8")
        loader = EnvLoader(p)
        assert loader.get_all() == {}

    def test_keys_without_values_are_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        # Lines like "KEY_ONLY" (no '=') yield None in dotenv_values
        p.write_text("KEY_ONLY\nGOOD_KEY=value\n", encoding="utf-8")
        loader = EnvLoader(p)
        assert "KEY_ONLY" not in loader.get_all()
        assert loader.get_all()["GOOD_KEY"] == "value"


# ------------------------------------------------------------------
# Tests: get_project_env
# ------------------------------------------------------------------


class TestGetProjectEnv:
    """Tests for per-project env filtering."""

    def test_returns_only_declared_keys(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        project = FakeProject(env_keys=["DATABASE_URL"])
        result = loader.get_project_env(project)
        assert result == {"DATABASE_URL": "sqlite:///test.db"}

    def test_missing_key_omitted(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        project = FakeProject(env_keys=["DATABASE_URL", "NONEXISTENT_KEY"])
        result = loader.get_project_env(project)
        assert result == {"DATABASE_URL": "sqlite:///test.db"}

    def test_empty_env_keys(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        project = FakeProject(env_keys=[])
        assert loader.get_project_env(project) == {}

    def test_all_keys_requested(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        project = FakeProject(env_keys=["API_KEY", "DATABASE_URL", "SECRET_TOKEN"])
        result = loader.get_project_env(project)
        assert len(result) == 3


# ------------------------------------------------------------------
# Tests: validate_project_keys
# ------------------------------------------------------------------


class TestValidateProjectKeys:
    """Tests for missing-key validation."""

    def test_all_present(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        project = FakeProject(env_keys=["API_KEY", "DATABASE_URL"])
        assert loader.validate_project_keys(project) == []

    def test_some_missing(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        project = FakeProject(env_keys=["API_KEY", "MISSING_1", "MISSING_2"])
        missing = loader.validate_project_keys(project)
        assert missing == ["MISSING_1", "MISSING_2"]

    def test_all_missing(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("", encoding="utf-8")
        loader = EnvLoader(p)
        project = FakeProject(env_keys=["A", "B"])
        assert loader.validate_project_keys(project) == ["A", "B"]

    def test_empty_env_keys_no_missing(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        project = FakeProject(env_keys=[])
        assert loader.validate_project_keys(project) == []


# ------------------------------------------------------------------
# Tests: get_all returns a copy
# ------------------------------------------------------------------


class TestGetAllCopy:
    """Ensure get_all returns a copy, not the internal dict."""

    def test_mutation_does_not_affect_loader(self, env_file: Path) -> None:
        loader = EnvLoader(env_file)
        copy = loader.get_all()
        copy["NEW_KEY"] = "hacked"
        assert "NEW_KEY" not in loader.get_all()
