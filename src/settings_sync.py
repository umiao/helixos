"""Sync Claude Code additionalDirectories from orchestrator_config.yaml.

Reads all non-primary project repo_paths and writes them to
``.claude/settings.local.json`` so helixos-centric Claude Code sessions
can access external project files via Read/Glob/Grep.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Defaults relative to the helixos project root
_DEFAULT_CONFIG_PATH = Path("orchestrator_config.yaml")
_DEFAULT_SETTINGS_PATH = Path(".claude/settings.local.json")


def sync_additional_directories(
    config_path: Path | None = None,
    settings_path: Path | None = None,
) -> list[str]:
    """Sync project repo_paths to Claude Code additionalDirectories.

    Reads orchestrator_config.yaml, extracts all non-primary project
    repo_paths, and writes them to .claude/settings.local.json under
    ``permissions.additionalDirectories``.

    This is a full-replacement operation: the list in settings.local.json
    is overwritten each time. orchestrator_config.yaml is the single
    source of truth.

    Args:
        config_path: Path to orchestrator_config.yaml.
            Defaults to ``orchestrator_config.yaml`` in cwd.
        settings_path: Path to .claude/settings.local.json.
            Defaults to ``.claude/settings.local.json`` in cwd.

    Returns:
        List of directory paths written to settings.local.json.
        Empty list if config is missing/malformed (no write performed).
    """
    if config_path is None:
        config_path = _DEFAULT_CONFIG_PATH
    if settings_path is None:
        settings_path = _DEFAULT_SETTINGS_PATH

    # Step 1: Read orchestrator config
    try:
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        logger.error(
            "orchestrator_config.yaml not found at %s -- skipping sync",
            config_path,
        )
        return []
    except (yaml.YAMLError, OSError) as exc:
        logger.error(
            "Failed to read orchestrator_config.yaml: %s -- skipping sync",
            exc,
        )
        return []

    if not isinstance(raw, dict):
        logger.error("orchestrator_config.yaml is not a mapping -- skipping sync")
        return []

    projects = raw.get("projects")
    if not isinstance(projects, dict):
        logger.warning("No projects section in orchestrator_config.yaml")
        return []

    # Step 2: Collect non-primary repo_paths
    dirs: list[str] = []
    seen: set[str] = set()
    for project_id, project_cfg in projects.items():
        if not isinstance(project_cfg, dict):
            continue
        # Skip primary project (it's the cwd)
        if project_cfg.get("is_primary", False):
            continue
        repo_path_raw = project_cfg.get("repo_path")
        if not repo_path_raw:
            continue

        resolved = Path(repo_path_raw).expanduser().resolve()
        if not resolved.is_dir():
            logger.warning(
                "Project %s: repo_path does not exist, skipping: %s",
                project_id,
                resolved,
            )
            continue

        path_str = str(resolved)
        if path_str not in seen:
            seen.add(path_str)
            dirs.append(path_str)

    # Step 3: Read existing settings.local.json
    settings: dict = {}
    if settings_path.is_file():
        try:
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Failed to read existing settings.local.json: %s -- starting fresh",
                exc,
            )
            settings = {}

    if not isinstance(settings, dict):
        settings = {}

    # Step 4: Update additionalDirectories under permissions
    if "permissions" not in settings or not isinstance(settings["permissions"], dict):
        settings["permissions"] = {}
    settings["permissions"]["additionalDirectories"] = dirs

    # Step 5: Validate JSON before write
    try:
        serialized = json.dumps(settings, indent=2, ensure_ascii=False)
        # Round-trip validation
        json.loads(serialized)
    except (TypeError, ValueError) as exc:
        logger.error(
            "JSON serialization/validation failed: %s -- aborting write", exc,
        )
        return []

    # Step 6: Backup existing settings file
    if settings_path.is_file():
        backup_path = settings_path.with_suffix(".json.bak")
        try:
            shutil.copy2(str(settings_path), str(backup_path))
        except OSError as exc:
            logger.warning("Failed to create backup: %s", exc)

    # Step 7: Atomic write (temp file + os.replace)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(settings_path.parent),
            suffix=".tmp",
            prefix="settings_local_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.write("\n")
            os.replace(tmp_path, str(settings_path))
        except BaseException:
            # Clean up temp file on failure
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
    except OSError as exc:
        logger.error("Failed to write settings.local.json: %s", exc)
        return []

    logger.info(
        "Synced %d additional directories to %s", len(dirs), settings_path,
    )
    return dirs
