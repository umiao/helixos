"""Unified .env loader with per-project env injection.

Loads a single .env file (the orchestrator's unified credentials store)
and provides filtered access per project based on each project's declared
env_keys list.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from dotenv import dotenv_values

logger = logging.getLogger(__name__)


@runtime_checkable
class HasEnvKeys(Protocol):
    """Minimal interface for objects that declare required env keys."""

    env_keys: list[str]


class EnvLoader:
    """Loads environment variables from a unified .env file.

    The orchestrator keeps a single .env file with all credentials.
    Each project declares which keys it needs via ``env_keys``.
    This class provides filtered access and validation.
    """

    def __init__(self, env_path: Path) -> None:
        """Initialize the loader, reading the .env file at *env_path*.

        If the file does not exist, a warning is logged and the loader
        operates with an empty variable set (no crash).
        """
        self._env_path = env_path
        self._vars: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_project_env(self, project: HasEnvKeys) -> dict[str, str]:
        """Return only the env vars that *project* declares in its ``env_keys``.

        Keys listed in ``project.env_keys`` but absent from the .env file
        are silently omitted from the result.
        """
        return {k: self._vars[k] for k in project.env_keys if k in self._vars}

    def get_all(self) -> dict[str, str]:
        """Return a copy of all loaded environment variables."""
        return dict(self._vars)

    def validate_project_keys(self, project: HasEnvKeys) -> list[str]:
        """Return the names of keys required by *project* but missing from .env."""
        return [k for k in project.env_keys if k not in self._vars]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read the .env file and populate ``self._vars``."""
        if not self._env_path.is_file():
            logger.warning("Env file not found: %s -- operating with empty env", self._env_path)
            return

        raw = dotenv_values(self._env_path, encoding="utf-8")
        # dotenv_values can return None values for keys without '=';
        # we only keep string values.
        self._vars = {k: v for k, v in raw.items() if v is not None}
