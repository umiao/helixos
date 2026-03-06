"""Tests for plan_status bidirectional sync between TASKS.md and DB.

Covers:
- ParsedTask.plan_status field (AC1)
- Parser recognition of `- **Plan**: <value>` (AC2)
- TasksWriter.update_task_plan_status (AC3)
- upsert_task plan_status=None -> DB wins (AC4, AC7)
- Round-trip: generate plan -> TASKS.md=ready -> sync -> DB=ready (AC6)
- Absence: DB=ready, TASKS.md absent -> sync -> DB still ready (AC7)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import OrchestratorConfig, ProjectConfig, ProjectRegistry
from src.models import ExecutorType, PlanStatus, Task, TaskStatus
from src.sync.tasks_parser import ParsedTask, TasksParser, sync_project_tasks
from src.task_manager import TaskManager, UpsertResult
from src.tasks_writer import TasksWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(
    project_id: str,
    repo_path: Path,
    tasks_file: str = "TASKS.md",
) -> ProjectRegistry:
    """Create a minimal ProjectRegistry for testing."""
    config = OrchestratorConfig(
        projects={
            project_id: ProjectConfig(
                name=project_id,
                repo_path=repo_path,
                executor_type=ExecutorType.CODE,
                tasks_file=tasks_file,
            ),
        },
    )
    return ProjectRegistry(config)


# ===================================================================
# AC1: ParsedTask has plan_status: str | None = None
# ===================================================================


class TestParsedTaskPlanStatus:
    """ParsedTask dataclass includes plan_status."""

    def test_default_none(self) -> None:
        """plan_status defaults to None."""
        pt = ParsedTask(
            local_task_id="T-P0-1",
            title="Test",
            status=TaskStatus.BACKLOG,
            description="desc",
        )
        assert pt.plan_status is None

    def test_explicit_value(self) -> None:
        """plan_status can be set explicitly."""
        pt = ParsedTask(
            local_task_id="T-P0-1",
            title="Test",
            status=TaskStatus.BACKLOG,
            description="desc",
            plan_status="ready",
        )
        assert pt.plan_status == "ready"


# ===================================================================
# AC2: Parser recognizes `- **Plan**: <value>` with whitelist
# ===================================================================


class TestParserPlanStatus:
    """Parser extracts plan_status from task descriptions."""

    def test_plan_ready(self) -> None:
        """Parses `- **Plan**: ready` correctly."""
        content = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Priority**: P0\n"
            "- **Plan**: ready\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert len(tasks) == 1
        assert tasks[0].plan_status == "ready"

    def test_plan_failed(self) -> None:
        """Parses `- **Plan**: failed` correctly."""
        content = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Plan**: failed\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].plan_status == "failed"

    def test_plan_none_value(self) -> None:
        """Parses `- **Plan**: none` correctly."""
        content = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Plan**: none\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].plan_status == "none"

    def test_plan_absent(self) -> None:
        """No Plan line -> plan_status is None (absent sentinel)."""
        content = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Priority**: P0\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].plan_status is None

    def test_plan_invalid_value_warning(self) -> None:
        """Invalid Plan value -> None + warning."""
        content = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Plan**: bogus\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].plan_status is None
        assert any("invalid Plan status" in w for w in parser.warnings)

    def test_plan_case_insensitive(self) -> None:
        """Plan value is case-insensitive."""
        content = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Plan**: Ready\n"
        )
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert tasks[0].plan_status == "ready"


# ===================================================================
# AC3: TasksWriter.update_task_plan_status
# ===================================================================


class TestWriterUpdatePlanStatus:
    """TasksWriter.update_task_plan_status inserts/updates Plan field."""

    def test_insert_plan_status(self, tmp_path: Path) -> None:
        """Inserts Plan line when none exists."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Priority**: P0\n"
            "- **Complexity**: S\n\n"
            "## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.update_task_plan_status("T-P0-1", "ready")
        assert result is True

        content = tasks_md.read_text(encoding="utf-8")
        assert "- **Plan**: ready" in content

    def test_update_existing_plan_status(self, tmp_path: Path) -> None:
        """Updates existing Plan line."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Priority**: P0\n"
            "- **Plan**: failed\n\n"
            "## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.update_task_plan_status("T-P0-1", "ready")
        assert result is True

        content = tasks_md.read_text(encoding="utf-8")
        assert "- **Plan**: ready" in content
        assert "- **Plan**: failed" not in content

    def test_creates_backup(self, tmp_path: Path) -> None:
        """Creates .bak backup before writing."""
        tasks_md = tmp_path / "TASKS.md"
        original = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- desc\n"
        )
        tasks_md.write_text(original, encoding="utf-8")
        writer = TasksWriter(tasks_md)
        writer.update_task_plan_status("T-P0-1", "ready")

        bak = tasks_md.with_suffix(".md.bak")
        assert bak.is_file()
        assert bak.read_text(encoding="utf-8") == original

    def test_task_not_found(self, tmp_path: Path) -> None:
        """Returns False when task ID is not in file."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n- desc\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.update_task_plan_status("T-P0-99", "ready")
        assert result is False

    def test_file_missing(self, tmp_path: Path) -> None:
        """Returns False when TASKS.md does not exist."""
        tasks_md = tmp_path / "TASKS.md"
        writer = TasksWriter(tasks_md)
        result = writer.update_task_plan_status("T-P0-1", "ready")
        assert result is False

    def test_insert_after_heading_when_no_metadata(self, tmp_path: Path) -> None:
        """Inserts Plan right after heading when there are no metadata lines."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "Some plain description text\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        result = writer.update_task_plan_status("T-P0-1", "ready")
        assert result is True

        content = tasks_md.read_text(encoding="utf-8")
        lines = content.split("\n")
        # Plan line should be right after the heading
        heading_idx = next(i for i, line in enumerate(lines) if "T-P0-1" in line)
        assert "- **Plan**: ready" in lines[heading_idx + 1]


# ===================================================================
# AC4 + AC7: upsert_task plan_status semantics
# ===================================================================


class TestUpsertPlanStatus:
    """upsert_task respects plan_status=None (DB wins) vs explicit value."""

    @pytest.fixture
    def task_manager(self, session_factory) -> TaskManager:
        """Create a TaskManager with in-memory DB."""
        return TaskManager(session_factory)

    async def test_create_with_plan_status(
        self, task_manager: TaskManager,
    ) -> None:
        """New task with explicit plan_status sets it in DB."""
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task, plan_status="ready")
        assert result == UpsertResult.created

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "ready"

    async def test_create_without_plan_status(
        self, task_manager: TaskManager,
    ) -> None:
        """New task without plan_status gets default 'none'."""
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task)
        assert result == UpsertResult.created

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == PlanStatus.NONE

    async def test_absence_preserves_db_value(
        self, task_manager: TaskManager,
    ) -> None:
        """AC7: DB=ready, TASKS.md line absent (plan_status=None) -> DB still ready."""
        # Create task with plan_status=ready
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
            plan_status="ready",
        )
        await task_manager.create_task(task)

        # Upsert with plan_status=None (line absent in TASKS.md)
        task2 = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task2, plan_status=None)
        assert result == UpsertResult.unchanged

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "ready"

    async def test_explicit_overwrite(
        self, task_manager: TaskManager,
    ) -> None:
        """Explicit plan_status in TASKS.md overwrites DB value."""
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
            plan_status="ready",
        )
        await task_manager.create_task(task)

        task2 = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task2, plan_status="failed")
        assert result == UpsertResult.updated

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "failed"

    async def test_explicit_none_resets(
        self, task_manager: TaskManager,
    ) -> None:
        """Explicit `- **Plan**: none` in TASKS.md resets DB to 'none'."""
        task = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
            plan_status="ready",
        )
        await task_manager.create_task(task)

        task2 = Task(
            id="proj:T-P0-1",
            project_id="proj",
            local_task_id="T-P0-1",
            title="Test",
            executor_type=ExecutorType.CODE,
        )
        result = await task_manager.upsert_task(task2, plan_status="none")
        assert result == UpsertResult.updated

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "none"


# ===================================================================
# AC6: Round-trip test via sync_project_tasks
# ===================================================================


class TestRoundTrip:
    """End-to-end: TASKS.md with Plan field -> sync -> DB preserves it."""

    @pytest.fixture
    def task_manager(self, session_factory) -> TaskManager:
        """Create a TaskManager with in-memory DB."""
        return TaskManager(session_factory)

    async def test_round_trip_ready(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """AC6: TASKS.md has `- **Plan**: ready` -> sync -> DB plan_status=ready."""
        md = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Priority**: P0\n"
            "- **Plan**: ready\n"
        )
        (tmp_path / "TASKS.md").write_text(md, encoding="utf-8")
        registry = _make_registry("proj", tmp_path)

        result = await sync_project_tasks("proj", task_manager, registry)
        assert result.added == 1

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "ready"

    async def test_round_trip_preserves_on_resync(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """Plan=ready in TASKS.md survives a second sync."""
        md = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Plan**: ready\n"
        )
        (tmp_path / "TASKS.md").write_text(md, encoding="utf-8")
        registry = _make_registry("proj", tmp_path)

        await sync_project_tasks("proj", task_manager, registry)
        result2 = await sync_project_tasks("proj", task_manager, registry)
        assert result2.unchanged == 1

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "ready"

    async def test_absence_does_not_reset_db(
        self, task_manager: TaskManager, tmp_path: Path,
    ) -> None:
        """AC7: DB=ready, TASKS.md has no Plan line -> sync -> DB still ready."""
        # First sync with Plan=ready
        md_with_plan = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Plan**: ready\n"
        )
        (tmp_path / "TASKS.md").write_text(md_with_plan, encoding="utf-8")
        registry = _make_registry("proj", tmp_path)
        await sync_project_tasks("proj", task_manager, registry)

        # Now remove the Plan line and re-sync
        md_without_plan = (
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Priority**: P0\n"
        )
        (tmp_path / "TASKS.md").write_text(md_without_plan, encoding="utf-8")
        await sync_project_tasks("proj", task_manager, registry)

        db_task = await task_manager.get_task("proj:T-P0-1")
        assert db_task is not None
        assert db_task.plan_status == "ready"


# ===================================================================
# Writer + Parser round-trip
# ===================================================================


class TestWriterParserRoundTrip:
    """Writer writes Plan line, parser reads it back correctly."""

    def test_write_then_parse(self, tmp_path: Path) -> None:
        """Writer inserts Plan=ready, parser reads it back."""
        tasks_md = tmp_path / "TASKS.md"
        tasks_md.write_text(
            "## Active Tasks\n\n"
            "#### T-P0-1: My task\n"
            "- **Priority**: P0\n\n"
            "## Completed Tasks\n",
            encoding="utf-8",
        )
        writer = TasksWriter(tasks_md)
        writer.update_task_plan_status("T-P0-1", "ready")

        content = tasks_md.read_text(encoding="utf-8")
        parser = TasksParser()
        tasks = parser.parse(content, "test")
        assert len(tasks) == 1
        assert tasks[0].plan_status == "ready"
