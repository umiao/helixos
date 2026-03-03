"""YAML config writer using ruamel.yaml for comment-preserving edits.

Reads ``orchestrator_config.yaml`` with ruamel.yaml (round-trip mode),
modifies the ``projects`` section, and atomically writes back via
tmp + os.replace.  Comments and formatting are preserved.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ruamel.yaml import YAML  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


def add_project_to_config(
    config_path: Path,
    project_id: str,
    project_data: dict[str, object],
) -> None:
    """Add a project entry to ``orchestrator_config.yaml``.

    Uses ruamel.yaml to preserve comments and formatting.  Writes
    atomically via a temporary file + ``os.replace``.

    Args:
        config_path: Path to the orchestrator_config.yaml file.
        project_id: The project ID key (e.g. ``"P1"``).
        project_data: Dict of project config fields (name, repo_path, etc.).

    Raises:
        FileNotFoundError: If *config_path* does not exist.
        ValueError: If a project with *project_id* already exists.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    yaml = YAML()
    yaml.preserve_quotes = True  # type: ignore[assignment]

    with open(config_path, encoding="utf-8") as f:
        doc = yaml.load(f)

    if doc is None:
        doc = {}

    # Ensure projects section exists
    if "projects" not in doc or doc["projects"] is None:
        doc["projects"] = {}

    if project_id in doc["projects"]:
        raise ValueError(f"Project already exists in config: {project_id}")

    doc["projects"][project_id] = project_data

    # Atomic write: tmp + os.replace
    tmp_path = config_path.with_suffix(".yaml.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.dump(doc, f)

    os.replace(str(tmp_path), str(config_path))

    logger.info("Added project %s to %s", project_id, config_path)


def _slugify(name: str) -> str:
    """Convert a project name to a URL-safe slug.

    Lowercases, replaces non-alphanumeric runs with hyphens,
    and strips leading/trailing hyphens.

    Args:
        name: Human-readable project name.

    Returns:
        A slug like ``"my-app"`` or ``"hello-world"``.
    """
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def suggest_next_project_id(
    config_path: Path,
    project_name: str = "",
) -> str:
    """Suggest a project ID slug derived from *project_name*.

    If *project_name* is given, slugifies it (e.g. ``"My App"`` ->
    ``"my-app"``).  If the slug already exists, appends ``-2``,
    ``-3``, etc.

    If *project_name* is empty, falls back to ``"project-1"``,
    ``"project-2"``, etc.

    Args:
        config_path: Path to the orchestrator_config.yaml file.
        project_name: Optional human-readable project name.

    Returns:
        A suggested project ID slug string.
    """
    yaml = YAML()
    existing: set[str] = set()

    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            doc = yaml.load(f)
        if doc and "projects" in doc and doc["projects"]:
            existing = set(doc["projects"].keys())

    base = _slugify(project_name) if project_name else "project"

    # If base slug is not taken, use it directly
    if base not in existing:
        return base

    # Append -2, -3, ... until unique
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"
