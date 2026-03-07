"""Tests for src/session_context_loader.py -- session context for agent prompts."""

from __future__ import annotations

import json
from pathlib import Path

from src.session_context_loader import (
    _get_active_tasks_summary,
    _get_session_state,
    get_session_context,
)


class TestGetActiveTasksSummary:
    """Tests for _get_active_tasks_summary."""

    def test_no_tasks_file(self, tmp_path: Path) -> None:
        """Returns fallback when TASKS.md does not exist."""
        result = _get_active_tasks_summary(tmp_path)
        assert result == "No TASKS.md found."

    def test_empty_active_section(self, tmp_path: Path) -> None:
        """Returns 'No active tasks.' when section is empty."""
        tasks = tmp_path / "TASKS.md"
        tasks.write_text(
            "## Active Tasks\n\n<!-- nothing -->\n\n## Completed Tasks\n",
            encoding="utf-8",
        )
        result = _get_active_tasks_summary(tmp_path)
        assert result == "No active tasks."

    def test_extracts_task_titles(self, tmp_path: Path) -> None:
        """Extracts #### task headers from Active Tasks."""
        tasks = tmp_path / "TASKS.md"
        tasks.write_text(
            "## Active Tasks\n\n"
            "#### T-P1-10: First task\n- **Priority**: P1\n\n"
            "#### T-P2-20: Second task\n- **Priority**: P2\n\n"
            "## Completed Tasks\n",
            encoding="utf-8",
        )
        result = _get_active_tasks_summary(tmp_path)
        assert "T-P1-10: First task" in result
        assert "T-P2-20: Second task" in result


class TestGetSessionState:
    """Tests for _get_session_state."""

    def test_no_state_file(self, tmp_path: Path) -> None:
        """Returns empty string when session_state.json does not exist."""
        result = _get_session_state(tmp_path)
        assert result == ""

    def test_reads_current_task(self, tmp_path: Path) -> None:
        """Extracts current task and mode from session_state.json."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        state = {"current_task": "T-P0-42", "mode": "autonomous"}
        (claude_dir / "session_state.json").write_text(
            json.dumps(state), encoding="utf-8",
        )
        result = _get_session_state(tmp_path)
        assert "T-P0-42" in result
        assert "autonomous" in result


class TestGetSessionContext:
    """Tests for get_session_context."""

    def test_returns_context_markers(self, tmp_path: Path) -> None:
        """Output contains session context delimiter markers."""
        result = get_session_context(tmp_path)
        assert "--- Session Context ---" in result
        assert "--- End Session Context ---" in result

    def test_includes_active_tasks(self, tmp_path: Path) -> None:
        """Context includes active tasks when TASKS.md exists."""
        tasks = tmp_path / "TASKS.md"
        tasks.write_text(
            "## Active Tasks\n\n"
            "#### T-P0-5: Important task\n- **Priority**: P0\n\n"
            "## Completed Tasks\n",
            encoding="utf-8",
        )
        result = get_session_context(tmp_path)
        assert "T-P0-5: Important task" in result
