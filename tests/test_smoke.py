"""Smoke tests to verify basic project setup."""

import importlib.util
from pathlib import Path


def test_project_imports() -> None:
    """Verify the src package is importable."""
    import src  # noqa: F401


def test_core_dependencies_importable() -> None:
    """Verify core dependencies are installed and importable."""
    import aiosqlite  # noqa: F401
    import dotenv  # noqa: F401
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
    import sqlalchemy  # noqa: F401
    import uvicorn  # noqa: F401
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
    assert (root / "scripts" / "run_server.py").is_file()


def test_run_server_script_importable() -> None:
    """run_server.py must exist and its main() must be callable."""
    root = Path(__file__).parent.parent
    script = root / "scripts" / "run_server.py"
    assert script.is_file(), "scripts/run_server.py not found"

    spec = importlib.util.spec_from_file_location("run_server", script)
    assert spec is not None, f"Could not load spec from {script}"
    assert spec.loader is not None, f"No loader for {script}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main), "run_server.main must be callable"
    assert callable(module.parse_args), "run_server.parse_args must be callable"
