"""Tests for .claude/hooks/archive_check.py -- PROGRESS.md and TASKS.md archival."""
import sys
from pathlib import Path

# Add hooks directory to path so we can import archive_check
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".claude" / "hooks"))
import archive_check  # noqa: E402


def _make_progress_entries(n: int) -> str:
    """Generate n fake PROGRESS.md entries."""
    header = (
        "# Progress Log\n\n"
        "> Append-only session log.\n"
        "> **Size invariant**: Keep under ~300 lines.\n"
        "> 0 session entries archived as of 2026-01-01.\n\n"
    )
    entries = []
    for i in range(1, n + 1):
        day = f"2026-01-{i:02d}"
        entries.append(
            f"## {day} -- [T-P0-{i}] Task {i}\n"
            f"- **What I did**: Did thing {i}\n"
            f"- **Status**: [DONE]\n"
        )
    return header + "\n".join(entries) + "\n"


def _make_tasks_with_completed(n_completed: int) -> str:
    """Generate TASKS.md with n completed entries."""
    header = (
        "# Task Backlog\n\n"
        "## In Progress\n\n"
        "## Active Tasks\n\n"
        "## Completed Tasks\n\n"
        "> 0 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).\n\n"
    )
    entries = []
    for i in range(1, n_completed + 1):
        entries.append(f"- T-P0-{i}: Task {i} -- 2026-01-{i:02d}")
    return header + "\n".join(entries) + "\n"


class TestArchiveProgress:
    """Tests for archive_progress()."""

    def test_no_archive_under_threshold(self, tmp_path: Path) -> None:
        """Should not archive when entries <= max_entries."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()
        (tmp_path / "PROGRESS.md").write_text(
            _make_progress_entries(40), encoding="utf-8"
        )

        result = archive_check.archive_progress(tmp_path, max_entries=80, keep_entries=40)
        assert result == 0
        # Archive file should not exist
        assert not (tmp_path / "archive" / "progress_log.md").exists()

    def test_archive_triggers_above_threshold(self, tmp_path: Path) -> None:
        """Should archive when entries > max_entries."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()
        (tmp_path / "PROGRESS.md").write_text(
            _make_progress_entries(85), encoding="utf-8"
        )

        result = archive_check.archive_progress(tmp_path, max_entries=80, keep_entries=40)
        assert result == 45  # 85 - 40 = 45 archived

        # Check archive file exists and has entries
        archive = (tmp_path / "archive" / "progress_log.md").read_text(encoding="utf-8")
        assert "45 session entries archived" in archive
        assert "## 2026-01-01" in archive  # oldest entry archived
        assert "## 2026-01-45" in archive  # last archived entry

        # Check PROGRESS.md was trimmed
        progress = (tmp_path / "PROGRESS.md").read_text(encoding="utf-8")
        assert "## 2026-01-46" in progress  # first kept entry
        assert "## 2026-01-85" in progress  # last entry still there
        assert "## 2026-01-01" not in progress  # archived entry gone

    def test_idempotent_after_archive(self, tmp_path: Path) -> None:
        """Second run should not archive anything (hysteresis)."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()
        (tmp_path / "PROGRESS.md").write_text(
            _make_progress_entries(85), encoding="utf-8"
        )

        # First archive
        archive_check.archive_progress(tmp_path, max_entries=80, keep_entries=40)

        # Second run -- should be under threshold now
        result = archive_check.archive_progress(tmp_path, max_entries=80, keep_entries=40)
        assert result == 0

    def test_counter_updates_cumulatively(self, tmp_path: Path) -> None:
        """Archive counter should accumulate across multiple archivals."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()

        # First archival
        (tmp_path / "PROGRESS.md").write_text(
            _make_progress_entries(85), encoding="utf-8"
        )
        archive_check.archive_progress(tmp_path, max_entries=80, keep_entries=40)

        # Add more entries to trigger second archival
        progress = (tmp_path / "PROGRESS.md").read_text(encoding="utf-8")
        extra_entries = ""
        for i in range(86, 135):
            day = f"2026-02-{i - 85:02d}"
            extra_entries += (
                f"## {day} -- [T-P0-{i}] Task {i}\n"
                f"- **What I did**: Did thing {i}\n"
                f"- **Status**: [DONE]\n\n"
            )
        (tmp_path / "PROGRESS.md").write_text(
            progress + extra_entries, encoding="utf-8"
        )

        result2 = archive_check.archive_progress(tmp_path, max_entries=80, keep_entries=40)
        assert result2 > 0

        archive = (tmp_path / "archive" / "progress_log.md").read_text(encoding="utf-8")
        # Counter should reflect cumulative total
        count_match = archive_check._parse_archive_counter(archive)
        assert count_match > 45  # More than first archival

    def test_chronological_order_in_archive(self, tmp_path: Path) -> None:
        """Archived entries should be in chronological order (oldest first)."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()
        (tmp_path / "PROGRESS.md").write_text(
            _make_progress_entries(85), encoding="utf-8"
        )

        archive_check.archive_progress(tmp_path, max_entries=80, keep_entries=40)

        archive = (tmp_path / "archive" / "progress_log.md").read_text(encoding="utf-8")
        # First entry should appear before last archived entry
        pos_first = archive.find("## 2026-01-01")
        pos_last = archive.find("## 2026-01-45")
        assert pos_first < pos_last

    def test_no_progress_file(self, tmp_path: Path) -> None:
        """Should return 0 when PROGRESS.md doesn't exist."""
        result = archive_check.archive_progress(tmp_path)
        assert result == 0


class TestArchiveCompletedTasks:
    """Tests for archive_completed_tasks()."""

    def test_no_archive_under_threshold(self, tmp_path: Path) -> None:
        """Should not archive when completed entries <= max_completed."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()
        (tmp_path / "TASKS.md").write_text(
            _make_tasks_with_completed(10), encoding="utf-8"
        )

        result = archive_check.archive_completed_tasks(tmp_path, max_completed=20, keep_completed=5)
        assert result == 0

    def test_archive_triggers_above_threshold(self, tmp_path: Path) -> None:
        """Should archive when completed entries > max_completed."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()
        (tmp_path / "TASKS.md").write_text(
            _make_tasks_with_completed(25), encoding="utf-8"
        )

        result = archive_check.archive_completed_tasks(tmp_path, max_completed=20, keep_completed=5)
        assert result == 20  # 25 - 5 = 20 archived

        # Check archive exists
        archive = (tmp_path / "archive" / "completed_tasks.md").read_text(encoding="utf-8")
        assert "20 completed tasks archived" in archive
        assert "T-P0-1:" in archive

        # Check TASKS.md trimmed
        tasks = (tmp_path / "TASKS.md").read_text(encoding="utf-8")
        assert "T-P0-25:" in tasks  # last entry kept
        assert "T-P0-1:" not in tasks  # archived entry gone

    def test_idempotent(self, tmp_path: Path) -> None:
        """Second run should not archive anything."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()
        (tmp_path / "TASKS.md").write_text(
            _make_tasks_with_completed(25), encoding="utf-8"
        )

        archive_check.archive_completed_tasks(tmp_path, max_completed=20, keep_completed=5)
        result = archive_check.archive_completed_tasks(tmp_path, max_completed=20, keep_completed=5)
        assert result == 0

    def test_no_tasks_file(self, tmp_path: Path) -> None:
        """Should return 0 when TASKS.md doesn't exist."""
        result = archive_check.archive_completed_tasks(tmp_path)
        assert result == 0

    def test_handles_block_entries(self, tmp_path: Path) -> None:
        """Should handle #### [x] block-style completed entries."""
        (tmp_path / "CLAUDE.md").touch()
        (tmp_path / "archive").mkdir()
        tasks_content = (
            "# Task Backlog\n\n"
            "## Completed Tasks\n\n"
            "> 0 completed tasks archived.\n\n"
        )
        for i in range(1, 26):
            tasks_content += (
                f"#### [x] T-P0-{i}: Task {i} -- 2026-01-{i:02d}\n"
                f"- Did stuff for task {i}.\n\n"
            )
        (tmp_path / "TASKS.md").write_text(tasks_content, encoding="utf-8")

        result = archive_check.archive_completed_tasks(tmp_path, max_completed=20, keep_completed=5)
        assert result == 20


class TestParseArchiveCounter:
    """Tests for _parse_archive_counter()."""

    def test_parses_session_counter(self) -> None:
        assert archive_check._parse_archive_counter("> 147 session entries archived as of 2026-03-09.") == 147

    def test_parses_tasks_counter(self) -> None:
        assert archive_check._parse_archive_counter("> 120 completed tasks archived to archive.") == 120

    def test_returns_zero_for_no_match(self) -> None:
        assert archive_check._parse_archive_counter("no counter here") == 0
