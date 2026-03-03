"""Tests for TASKS.md parser and sync functionality."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import OrchestratorConfig, ProjectConfig, ProjectRegistry
from src.models import ExecutorType, TaskStatus
from src.sync.tasks_parser import (
    ParsedTask,
    SyncResult,
    TasksParser,
    sync_project_tasks,
)
from src.task_manager import TaskManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_fixture(name: str) -> str:
    """Read a fixture file from tests/fixtures/."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _make_registry(
    project_id: str,
    repo_path: Path,
    tasks_file: str = "TASKS.md",
    status_sections: dict[str, str] | None = None,
) -> ProjectRegistry:
    """Create a minimal ProjectRegistry for testing."""
    config = OrchestratorConfig(
        projects={
            project_id: ProjectConfig(
                name=project_id,
                repo_path=repo_path,
                executor_type=ExecutorType.CODE,
                tasks_file=tasks_file,
                status_sections=status_sections,
            ),
        },
    )
    return ProjectRegistry(config)


# ===================================================================
# ParsedTask and SyncResult dataclass tests
# ===================================================================


class TestDataClasses:
    """Verify ParsedTask and SyncResult data classes."""

    def test_parsed_task_fields(self) -> None:
        """ParsedTask holds all expected fields."""
        pt = ParsedTask(
            local_task_id="T-P0-1",
            title="My task",
            status=TaskStatus.BACKLOG,
            description="some desc",
        )
        assert pt.local_task_id == "T-P0-1"
        assert pt.title == "My task"
        assert pt.status == TaskStatus.BACKLOG
        assert pt.description == "some desc"

    def test_sync_result_defaults(self) -> None:
        """SyncResult defaults to zeroes and empty warnings."""
        sr = SyncResult()
        assert sr.added == 0
        assert sr.updated == 0
        assert sr.unchanged == 0
        assert sr.warnings == []


# ===================================================================
# TasksParser.parse -- basic parsing
# ===================================================================


class TestParserBasics:
    """Core parsing of well-formed TASKS.md."""

    def test_parse_sample_tasks(self) -> None:
        """Parse sample_tasks.md and verify task count and IDs."""
        content = _read_fixture("sample_tasks.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        task_ids = [t.local_task_id for t in tasks]
        assert len(tasks) == 7
        assert "T-P0-5" in task_ids
        assert "T-P0-4" in task_ids
        assert "T-P0-7" in task_ids
        assert "T-P0-8" in task_ids
        assert "T-P0-6" in task_ids
        assert "T-P0-1" in task_ids
        assert "T-P0-2" in task_ids

    def test_parse_returns_ordered_list(self) -> None:
        """Tasks appear in document order."""
        content = _read_fixture("sample_tasks.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        ids = [t.local_task_id for t in tasks]
        # In Progress first, then Active, then Blocked, then Completed
        assert ids.index("T-P0-5") < ids.index("T-P0-4")
        assert ids.index("T-P0-4") < ids.index("T-P0-6")
        assert ids.index("T-P0-6") < ids.index("T-P0-1")


# ===================================================================
# Section-to-status mapping
# ===================================================================


class TestSectionMapping:
    """Verify section headers map to correct TaskStatus values."""

    def test_in_progress_maps_to_running(self) -> None:
        """Tasks under '## In Progress' get RUNNING status."""
        content = _read_fixture("sample_tasks.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        task_map = {t.local_task_id: t for t in tasks}
        assert task_map["T-P0-5"].status == TaskStatus.RUNNING

    def test_active_tasks_maps_to_backlog(self) -> None:
        """Tasks under '## Active Tasks' get BACKLOG status."""
        content = _read_fixture("sample_tasks.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        task_map = {t.local_task_id: t for t in tasks}
        assert task_map["T-P0-4"].status == TaskStatus.BACKLOG
        assert task_map["T-P0-7"].status == TaskStatus.BACKLOG
        assert task_map["T-P0-8"].status == TaskStatus.BACKLOG

    def test_blocked_maps_to_blocked(self) -> None:
        """Tasks under '## Blocked' get BLOCKED status."""
        content = _read_fixture("sample_tasks.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        task_map = {t.local_task_id: t for t in tasks}
        assert task_map["T-P0-6"].status == TaskStatus.BLOCKED

    def test_completed_maps_to_done(self) -> None:
        """Tasks under '## Completed Tasks' get DONE status."""
        content = _read_fixture("sample_tasks.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        task_map = {t.local_task_id: t for t in tasks}
        assert task_map["T-P0-1"].status == TaskStatus.DONE
        assert task_map["T-P0-2"].status == TaskStatus.DONE

    def test_unrecognized_section_resets_status(self) -> None:
        """Tasks under unrecognized sections are not captured."""
        content = "## Random Section\n\n#### T-P0-1: Orphan task\n- desc\n"
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert len(tasks) == 0

    def test_case_insensitive_section_match(self) -> None:
        """Section matching is case-insensitive."""
        content = "## ACTIVE TASKS\n\n#### T-P0-1: My task\n- desc\n"
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.BACKLOG


# ===================================================================
# Custom status sections
# ===================================================================


class TestCustomSections:
    """Verify custom status_sections override defaults."""

    def test_custom_mapping(self) -> None:
        """Custom section names map to the specified statuses."""
        content = "## Todo\n\n#### T-P0-1: Task one\n- desc\n"
        custom = {"Todo": TaskStatus.BACKLOG}
        parser = TasksParser(status_sections=custom)
        tasks = parser.parse(content, "test")

        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.BACKLOG

    def test_custom_overrides_default(self) -> None:
        """Custom mapping replaces (not extends) the default mapping."""
        content = "## Active Tasks\n\n#### T-P0-1: Task one\n- desc\n"
        # Custom mapping has no "Active Tasks" key
        custom = {"Todo": TaskStatus.BACKLOG}
        parser = TasksParser(status_sections=custom)
        tasks = parser.parse(content, "test")

        # "Active Tasks" is not recognized -> task outside section
        assert len(tasks) == 0


# ===================================================================
# Title extraction
# ===================================================================


class TestTitleExtraction:
    """Verify title parsing from various heading formats."""

    def test_simple_heading(self) -> None:
        """Title from '#### T-P0-1: Simple title'."""
        content = "## Active Tasks\n\n#### T-P0-1: Simple title\n"
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].title == "Simple title"

    def test_checkbox_heading(self) -> None:
        """Title from '#### [x] T-P0-1: Done task -- 2026-03-01'."""
        content = (
            "## Completed Tasks\n\n"
            "#### [x] T-P0-1: Done task -- 2026-03-01\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].title == "Done task"

    def test_parenthetical_title(self) -> None:
        """Title preserves parenthetical content."""
        content = (
            "## Active Tasks\n\n"
            "#### T-P0-1: Parser (one-way sync)\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].title == "Parser (one-way sync)"

    def test_title_fallback_to_id(self) -> None:
        """When no title text follows the ID, use the ID as title."""
        content = "## Active Tasks\n\n#### T-P0-1\n"
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].title == "T-P0-1"


# ===================================================================
# Description extraction
# ===================================================================


class TestDescriptionExtraction:
    """Verify opaque description blob is captured correctly."""

    def test_description_captured(self) -> None:
        """Description includes all lines between task headings."""
        content = _read_fixture("sample_tasks.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        task_map = {t.local_task_id: t for t in tasks}
        desc = task_map["T-P0-4"].description
        assert "Priority" in desc
        assert "Complexity" in desc
        assert "Depends on" in desc

    def test_trailing_hr_stripped(self) -> None:
        """Trailing '---' horizontal rules are stripped from descriptions."""
        content = "## Active Tasks\n\n#### T-P0-1: Task\n- line\n\n---\n"
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert not tasks[0].description.endswith("---")

    def test_empty_description(self) -> None:
        """Task with no content lines gets empty description."""
        content = "## Active Tasks\n\n#### T-P0-1: Task\n#### T-P0-2: Next\n"
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].description == ""

    def test_multiline_description(self) -> None:
        """Multi-line description is joined with newlines."""
        content = (
            "## Active Tasks\n\n"
            "#### T-P0-1: Task\n"
            "- Line one\n"
            "- Line two\n"
            "- Line three\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert "Line one" in tasks[0].description
        assert "Line two" in tasks[0].description
        assert "Line three" in tasks[0].description


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    """Edge cases: no IDs, duplicates, empty sections, empty content."""

    def test_tasks_without_ids_skipped_with_warning(self) -> None:
        """Headings without task IDs produce warnings and are skipped."""
        content = _read_fixture("tasks_no_ids.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        # Only T-P0-1 and T-P0-2 have valid IDs
        ids = [t.local_task_id for t in tasks]
        assert "T-P0-1" in ids
        assert "T-P0-2" in ids
        assert len(tasks) == 2

        # Should have warnings for the two headings without IDs
        assert any("without task ID" in w for w in parser.warnings)
        no_id_warnings = [w for w in parser.warnings if "without task ID" in w]
        assert len(no_id_warnings) == 2

    def test_duplicate_ids_last_wins(self) -> None:
        """When duplicate task IDs appear, the last occurrence wins."""
        content = _read_fixture("tasks_duplicates.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        assert len(tasks) == 1
        assert tasks[0].local_task_id == "T-P0-1"
        assert tasks[0].status == TaskStatus.DONE  # from Completed section
        assert "Second occurrence" in tasks[0].title

    def test_duplicate_ids_produce_warning(self) -> None:
        """Duplicate task IDs produce a warning."""
        content = _read_fixture("tasks_duplicates.md")
        parser = TasksParser()
        parser.parse(content, "test")

        assert any("Duplicate task ID" in w for w in parser.warnings)

    def test_empty_sections(self) -> None:
        """TASKS.md with only empty sections produces no tasks."""
        content = _read_fixture("tasks_empty.md")
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert len(tasks) == 0
        assert len(parser.warnings) == 0

    def test_empty_content(self) -> None:
        """Completely empty content produces no tasks."""
        parser = TasksParser()
        tasks = parser.parse("", "test")
        assert len(tasks) == 0

    def test_task_outside_section_skipped(self) -> None:
        """Task heading before any section header is skipped with warning."""
        content = "# Title\n\n#### T-P0-1: Orphan\n- desc\n\n## Active Tasks\n"
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert len(tasks) == 0
        assert any("outside" in w for w in parser.warnings)

    def test_warnings_property_returns_copy(self) -> None:
        """The warnings property returns a copy, not the internal list."""
        parser = TasksParser()
        parser.parse("## Active Tasks\n\n#### No ID heading\n", "test")
        w1 = parser.warnings
        w2 = parser.warnings
        assert w1 is not w2

    def test_subsection_does_not_change_status(self) -> None:
        """### subsections don't alter the current status context."""
        content = (
            "## Active Tasks\n\n"
            "### P0 -- Must Have\n\n"
            "#### T-P0-1: Task A\n- desc\n\n"
            "### P1 -- Should Have\n\n"
            "#### T-P0-2: Task B\n- desc\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")

        assert len(tasks) == 2
        # Both should be BACKLOG (from Active Tasks)
        assert tasks[0].status == TaskStatus.BACKLOG
        assert tasks[1].status == TaskStatus.BACKLOG


# ===================================================================
# sync_project_tasks -- DB sync
# ===================================================================


class TestSyncProjectTasks:
    """Integration tests for sync_project_tasks."""

    @pytest.fixture
    def task_manager(self, session_factory) -> TaskManager:
        """Create a TaskManager with in-memory DB."""
        return TaskManager(session_factory)

    @pytest.fixture
    def project_dir(self, tmp_path: Path) -> Path:
        """Create a temp project dir with a sample TASKS.md."""
        md = _read_fixture("sample_tasks.md")
        (tmp_path / "TASKS.md").write_text(md, encoding="utf-8")
        return tmp_path

    @pytest.fixture
    def registry(self, project_dir: Path) -> ProjectRegistry:
        """Create a registry pointing to the temp project dir."""
        return _make_registry("proj", project_dir)

    async def test_sync_adds_new_tasks(
        self,
        task_manager: TaskManager,
        registry: ProjectRegistry,
    ) -> None:
        """First sync should add all parsed tasks to the DB."""
        result = await sync_project_tasks("proj", task_manager, registry)

        assert result.added == 7
        assert result.updated == 0
        assert result.unchanged == 0

        db_tasks = await task_manager.list_tasks(project_id="proj")
        assert len(db_tasks) == 7

    async def test_backlog_stays_backlog(
        self,
        task_manager: TaskManager,
        registry: ProjectRegistry,
    ) -> None:
        """Tasks from Active Tasks section enter DB as BACKLOG (no auto-promotion)."""
        await sync_project_tasks("proj", task_manager, registry)

        # T-P0-4 is in Active Tasks -> DB should stay BACKLOG
        task = await task_manager.get_task("proj:T-P0-4")
        assert task is not None
        assert task.status == TaskStatus.BACKLOG

    async def test_running_stays_running(
        self,
        task_manager: TaskManager,
        registry: ProjectRegistry,
    ) -> None:
        """Tasks from In Progress section enter DB as RUNNING."""
        await sync_project_tasks("proj", task_manager, registry)

        task = await task_manager.get_task("proj:T-P0-5")
        assert task is not None
        assert task.status == TaskStatus.RUNNING

    async def test_done_stays_done(
        self,
        task_manager: TaskManager,
        registry: ProjectRegistry,
    ) -> None:
        """Tasks from Completed section enter DB as DONE."""
        await sync_project_tasks("proj", task_manager, registry)

        task = await task_manager.get_task("proj:T-P0-1")
        assert task is not None
        assert task.status == TaskStatus.DONE

    async def test_blocked_stays_blocked(
        self,
        task_manager: TaskManager,
        registry: ProjectRegistry,
    ) -> None:
        """Tasks from Blocked section enter DB as BLOCKED."""
        await sync_project_tasks("proj", task_manager, registry)

        task = await task_manager.get_task("proj:T-P0-6")
        assert task is not None
        assert task.status == TaskStatus.BLOCKED

    async def test_second_sync_unchanged(
        self,
        task_manager: TaskManager,
        registry: ProjectRegistry,
    ) -> None:
        """Running sync twice without changes produces all 'unchanged'."""
        await sync_project_tasks("proj", task_manager, registry)
        result = await sync_project_tasks("proj", task_manager, registry)

        assert result.added == 0
        assert result.unchanged == 7
        assert result.updated == 0

    async def test_sync_updates_title(
        self,
        task_manager: TaskManager,
        project_dir: Path,
    ) -> None:
        """Changing a task title in TASKS.md triggers an update."""
        registry = _make_registry("proj", project_dir)
        await sync_project_tasks("proj", task_manager, registry)

        # Modify TASKS.md -- change T-P0-4's title
        md = (project_dir / "TASKS.md").read_text(encoding="utf-8")
        md = md.replace(
            "#### T-P0-4: TASKS.md parser",
            "#### T-P0-4: Markdown parser (updated)",
        )
        (project_dir / "TASKS.md").write_text(md, encoding="utf-8")

        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.updated >= 1

        task = await task_manager.get_task("proj:T-P0-4")
        assert task is not None
        assert "updated" in task.title

    async def test_sync_marks_done_from_tasks_md(
        self,
        task_manager: TaskManager,
        project_dir: Path,
    ) -> None:
        """Moving a task to Completed in TASKS.md sets DB status to DONE."""
        registry = _make_registry("proj", project_dir)
        await sync_project_tasks("proj", task_manager, registry)

        # Verify T-P0-4 is BACKLOG initially (no auto-promotion)
        task = await task_manager.get_task("proj:T-P0-4")
        assert task is not None
        assert task.status == TaskStatus.BACKLOG

        # Move T-P0-4 to Completed in TASKS.md
        md = (project_dir / "TASKS.md").read_text(encoding="utf-8")
        # Remove from Active Tasks
        md = md.replace(
            "#### T-P0-4: TASKS.md parser\n"
            "- **Priority**: P0\n"
            "- **Complexity**: S\n"
            "- **Depends on**: T-P0-3\n\n---",
            "",
        )
        # Add to Completed Tasks
        md = md.replace(
            "#### [x] T-P0-1:",
            "#### [x] T-P0-4: TASKS.md parser -- 2026-03-01\n"
            "- Completed\n\n#### [x] T-P0-1:",
        )
        (project_dir / "TASKS.md").write_text(md, encoding="utf-8")

        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.updated >= 1

        task = await task_manager.get_task("proj:T-P0-4")
        assert task is not None
        assert task.status == TaskStatus.DONE

    async def test_removed_tasks_stay_in_db(
        self,
        task_manager: TaskManager,
        project_dir: Path,
    ) -> None:
        """Tasks removed from TASKS.md remain in the database."""
        registry = _make_registry("proj", project_dir)
        await sync_project_tasks("proj", task_manager, registry)

        # Write a TASKS.md with fewer tasks
        minimal_md = (
            "## Active Tasks\n\n"
            "#### T-P0-4: TASKS.md parser\n- desc\n"
        )
        (project_dir / "TASKS.md").write_text(minimal_md, encoding="utf-8")

        result = await sync_project_tasks("proj", task_manager, registry)
        # Only T-P0-4 is in the new MD (description changed -> updated)
        assert result.added == 0
        assert result.updated + result.unchanged == 1

        # All 7 original tasks still in DB
        all_tasks = await task_manager.list_tasks(project_id="proj")
        assert len(all_tasks) == 7

    async def test_sync_no_repo_path(self, task_manager: TaskManager) -> None:
        """Sync returns warning when project has no repo_path."""
        config = OrchestratorConfig(
            projects={
                "norepo": ProjectConfig(
                    name="norepo",
                    repo_path=None,
                    executor_type=ExecutorType.CODE,
                ),
            },
        )
        registry = ProjectRegistry(config)
        result = await sync_project_tasks("norepo", task_manager, registry)

        assert result.added == 0
        assert any("no repo_path" in w for w in result.warnings)

    async def test_sync_missing_tasks_md(
        self,
        task_manager: TaskManager,
        tmp_path: Path,
    ) -> None:
        """Sync returns warning when TASKS.md does not exist."""
        registry = _make_registry("proj", tmp_path)
        result = await sync_project_tasks("proj", task_manager, registry)

        assert result.added == 0
        assert any("not found" in w for w in result.warnings)

    async def test_sync_global_id_format(
        self,
        task_manager: TaskManager,
        registry: ProjectRegistry,
    ) -> None:
        """Task IDs in DB use 'project_id:local_task_id' format."""
        await sync_project_tasks("proj", task_manager, registry)

        task = await task_manager.get_task("proj:T-P0-4")
        assert task is not None
        assert task.id == "proj:T-P0-4"
        assert task.project_id == "proj"
        assert task.local_task_id == "T-P0-4"

    async def test_sync_with_custom_status_sections(
        self,
        task_manager: TaskManager,
        tmp_path: Path,
    ) -> None:
        """Custom status_sections from project config are used."""
        md = "## Todo\n\n#### T-P0-1: Custom task\n- desc\n"
        (tmp_path / "TASKS.md").write_text(md, encoding="utf-8")

        registry = _make_registry(
            "proj",
            tmp_path,
            status_sections={"Todo": "backlog"},
        )
        result = await sync_project_tasks("proj", task_manager, registry)

        assert result.added == 1
        task = await task_manager.get_task("proj:T-P0-1")
        assert task is not None
        # Tasks stay in their parsed status (BACKLOG, no auto-promotion)
        assert task.status == TaskStatus.BACKLOG

    async def test_sync_executor_type_from_project(
        self,
        task_manager: TaskManager,
        registry: ProjectRegistry,
    ) -> None:
        """Synced tasks inherit executor_type from the project config."""
        await sync_project_tasks("proj", task_manager, registry)

        task = await task_manager.get_task("proj:T-P0-4")
        assert task is not None
        assert task.executor_type == ExecutorType.CODE

    async def test_sync_warnings_forwarded(
        self,
        task_manager: TaskManager,
        tmp_path: Path,
    ) -> None:
        """Parser warnings are included in SyncResult."""
        md = _read_fixture("tasks_no_ids.md")
        (tmp_path / "TASKS.md").write_text(md, encoding="utf-8")
        registry = _make_registry("proj", tmp_path)

        result = await sync_project_tasks("proj", task_manager, registry)
        assert any("without task ID" in w for w in result.warnings)
