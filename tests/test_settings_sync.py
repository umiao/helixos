"""Tests for src.settings_sync -- Claude Code additionalDirectories sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.settings_sync import sync_additional_directories


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace with config and .claude dir."""
    # Create two fake project dirs
    (tmp_path / "proj_a").mkdir()
    (tmp_path / "proj_b").mkdir()

    config = {
        "projects": {
            "helixos": {
                "name": "HelixOS",
                "repo_path": str(tmp_path / "nonexistent"),
                "is_primary": True,
            },
            "proj_a": {
                "name": "Project A",
                "repo_path": str(tmp_path / "proj_a"),
            },
            "proj_b": {
                "name": "Project B",
                "repo_path": str(tmp_path / "proj_b"),
            },
        },
    }
    config_path = tmp_path / "orchestrator_config.yaml"
    import yaml
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f)

    (tmp_path / ".claude").mkdir()
    return tmp_path


def _config_path(ws: Path) -> Path:
    """Return config path for workspace."""
    return ws / "orchestrator_config.yaml"


def _settings_path(ws: Path) -> Path:
    """Return settings path for workspace."""
    return ws / ".claude" / "settings.local.json"


def test_sync_creates_settings_if_missing(workspace: Path) -> None:
    """Settings.local.json is created when it doesn't exist."""
    result = sync_additional_directories(
        config_path=_config_path(workspace),
        settings_path=_settings_path(workspace),
    )
    assert len(result) == 2
    settings = json.loads(_settings_path(workspace).read_text(encoding="utf-8"))
    assert "permissions" in settings
    assert len(settings["permissions"]["additionalDirectories"]) == 2


def test_sync_preserves_existing_allow_rules(workspace: Path) -> None:
    """Existing permissions.allow entries are preserved."""
    existing = {
        "permissions": {
            "allow": ["Bash(*)", "WebSearch"],
            "additionalDirectories": ["/old/path"],
        },
    }
    settings_path = _settings_path(workspace)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(existing, f)

    sync_additional_directories(
        config_path=_config_path(workspace),
        settings_path=settings_path,
    )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == ["Bash(*)", "WebSearch"]
    # Old additionalDirectories entry should be replaced
    assert "/old/path" not in settings["permissions"]["additionalDirectories"]


def test_sync_skips_primary_project(workspace: Path) -> None:
    """Primary project's repo_path is not included."""
    result = sync_additional_directories(
        config_path=_config_path(workspace),
        settings_path=_settings_path(workspace),
    )
    # helixos is primary and its path doesn't exist anyway, but
    # even if it did, it should be skipped
    for d in result:
        assert "nonexistent" not in d


def test_sync_skips_nonexistent_paths(workspace: Path) -> None:
    """Non-existent directories are excluded with a warning."""
    import yaml

    config = {
        "projects": {
            "good": {
                "name": "Good",
                "repo_path": str(workspace / "proj_a"),
            },
            "bad": {
                "name": "Bad",
                "repo_path": str(workspace / "does_not_exist"),
            },
        },
    }
    config_path = _config_path(workspace)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f)

    result = sync_additional_directories(
        config_path=config_path,
        settings_path=_settings_path(workspace),
    )
    assert len(result) == 1
    assert "proj_a" in result[0]


def test_sync_deduplicates(workspace: Path) -> None:
    """Duplicate repo_paths produce a single entry."""
    import yaml

    config = {
        "projects": {
            "proj_a1": {
                "name": "A alias 1",
                "repo_path": str(workspace / "proj_a"),
            },
            "proj_a2": {
                "name": "A alias 2",
                "repo_path": str(workspace / "proj_a"),
            },
        },
    }
    config_path = _config_path(workspace)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f)

    result = sync_additional_directories(
        config_path=config_path,
        settings_path=_settings_path(workspace),
    )
    assert len(result) == 1


def test_sync_overwrites_manual_entries(workspace: Path) -> None:
    """Manually added additionalDirectories entries are replaced."""
    existing = {
        "permissions": {
            "additionalDirectories": ["/manual/entry1", "/manual/entry2"],
        },
    }
    settings_path = _settings_path(workspace)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(existing, f)

    result = sync_additional_directories(
        config_path=_config_path(workspace),
        settings_path=settings_path,
    )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    dirs = settings["permissions"]["additionalDirectories"]
    assert "/manual/entry1" not in dirs
    assert "/manual/entry2" not in dirs
    assert len(dirs) == len(result)


def test_sync_noop_on_missing_config(workspace: Path) -> None:
    """No crash and no write when orchestrator_config.yaml is missing."""
    missing_config = workspace / "nonexistent_config.yaml"
    settings_path = _settings_path(workspace)

    result = sync_additional_directories(
        config_path=missing_config,
        settings_path=settings_path,
    )
    assert result == []
    assert not settings_path.exists()


def test_sync_creates_backup(workspace: Path) -> None:
    """.bak file is created before overwrite."""
    settings_path = _settings_path(workspace)
    original = {"permissions": {"allow": ["Bash(*)"]}}
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(original, f)

    sync_additional_directories(
        config_path=_config_path(workspace),
        settings_path=settings_path,
    )

    backup_path = settings_path.with_suffix(".json.bak")
    assert backup_path.exists()
    backup_content = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup_content == original


def test_sync_validates_json_before_write(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If JSON validation fails, no write occurs."""
    settings_path = _settings_path(workspace)
    original = {"permissions": {"allow": ["Bash(*)"]}}
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(original, f)

    # Monkey-patch json.dumps to raise on the first call (serialization)
    real_dumps = json.dumps
    call_count = 0

    def bad_dumps(*args: object, **kwargs: object) -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TypeError("simulated serialization failure")
        return real_dumps(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("src.settings_sync.json.dumps", bad_dumps)

    result = sync_additional_directories(
        config_path=_config_path(workspace),
        settings_path=settings_path,
    )
    assert result == []
    # Original file should be unchanged
    current = json.loads(settings_path.read_text(encoding="utf-8"))
    assert current == original
