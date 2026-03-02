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


def suggest_next_project_id(config_path: Path) -> str:
    """Suggest the next available P-number project ID.

    Scans existing project IDs matching ``P\\d+`` and returns
    the next sequential one (e.g. if P0 and P2 exist, returns P3).

    Args:
        config_path: Path to the orchestrator_config.yaml file.

    Returns:
        A suggested project ID string like ``"P1"``.
    """
    if not config_path.exists():
        return "P0"

    yaml = YAML()
    with open(config_path, encoding="utf-8") as f:
        doc = yaml.load(f)

    if doc is None or "projects" not in doc or doc["projects"] is None:
        return "P0"

    max_num = -1
    for key in doc["projects"]:
        if isinstance(key, str) and key.startswith("P") and key[1:].isdigit():
            num = int(key[1:])
            if num > max_num:
                max_num = num

    return f"P{max_num + 1}"
