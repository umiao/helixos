"""Smoke tests to verify basic project setup."""

from pathlib import Path


def test_project_imports() -> None:
    """Verify the src package is importable."""
    import src  # noqa: F401


def test_core_dependencies_importable() -> None:
    """Verify core dependencies are installed and importable."""
    import fastapi  # noqa: F401
    import uvicorn  # noqa: F401
    import sqlalchemy  # noqa: F401
    import aiosqlite  # noqa: F401
    import pydantic  # noqa: F401
    import dotenv  # noqa: F401
    import yaml  # noqa: F401


def test_subpackages_importable() -> None:
    """Verify src subpackages exist and are importable."""
    import src.executors  # noqa: F401
    import src.sync  # noqa: F401


def test_project_structure() -> None:
    """Verify expected directories and files exist."""
    root = Path(__file__).parent.parent
    assert (root / "pyproject.toml").is_file()
    assert (root / "requirements.txt").is_file()
    assert (root / "orchestrator_config.yaml").is_file()
    assert (root / "contracts").is_dir()
    assert (root / "frontend").is_dir()
    assert (root / "frontend" / "vite.config.ts").is_file()
    assert (root / "frontend" / "package.json").is_file()
    assert (root / "src" / "executors" / "__init__.py").is_file()
    assert (root / "src" / "sync" / "__init__.py").is_file()
    assert (root / "scripts" / "start.ps1").is_file()
