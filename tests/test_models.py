"""Tests for Pydantic models in src/models.py."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.models import (
    Dependency,
    ExecutionState,
    ExecutorType,
    LLMReview,
    Project,
    ReviewState,
    Task,
    TaskStatus,
)
from tests.factories import make_task

# ---------------------------------------------------------------------------
# TaskStatus enum
# ---------------------------------------------------------------------------


class TestTaskStatus:
    """TaskStatus enum validation."""

    def test_has_eight_values(self) -> None:
        """PRD specifies 8 status values (+ review_auto_approved = 9)."""
        assert len(TaskStatus) == 9

    def test_values_are_strings(self) -> None:
        """TaskStatus members should serialize to their string value."""
        assert TaskStatus.BACKLOG == "backlog"
        assert TaskStatus.RUNNING.value == "running"

    def test_from_string(self) -> None:
        """Should be constructable from a raw string."""
        assert TaskStatus("queued") == TaskStatus.QUEUED

    def test_invalid_value_raises(self) -> None:
        """Invalid string should raise ValueError."""
        with pytest.raises(ValueError):
            TaskStatus("invalid_status")


# ---------------------------------------------------------------------------
# ExecutorType enum
# ---------------------------------------------------------------------------


class TestExecutorType:
    """ExecutorType enum validation."""

    def test_has_three_values(self) -> None:
        assert len(ExecutorType) == 3

    def test_values(self) -> None:
        assert ExecutorType.CODE == "code"
        assert ExecutorType.AGENT == "agent"
        assert ExecutorType.SCHEDULED == "scheduled"


# ---------------------------------------------------------------------------
# Project model
# ---------------------------------------------------------------------------


class TestProject:
    """Project model validation and serialization."""

    def test_minimal_project(self) -> None:
        """Project with required fields only."""
        p = Project(id="P0", name="Test", executor_type=ExecutorType.CODE)
        assert p.id == "P0"
        assert p.repo_path is None
        assert p.tasks_file == "TASKS.md"
        assert p.max_concurrency == 1
        assert p.env_keys == []

    def test_full_project(self) -> None:
        """Project with all fields populated."""
        p = Project(
            id="P1",
            name="Job Hunter",
            repo_path=Path("/home/user/projects/job-hunter"),
            workspace_path=Path("/tmp/workspace"),
            tasks_file="TODO.md",
            executor_type=ExecutorType.AGENT,
            max_concurrency=2,
            env_keys=["API_KEY", "SECRET"],
            claude_md_path=Path("/home/user/claude.md"),
        )
        assert p.repo_path == Path("/home/user/projects/job-hunter")
        assert p.env_keys == ["API_KEY", "SECRET"]

    def test_serialization_roundtrip(self) -> None:
        """model_dump -> model_validate should preserve data."""
        p = Project(id="P2", name="Blog", executor_type=ExecutorType.SCHEDULED)
        data = p.model_dump(mode="json")
        p2 = Project.model_validate(data)
        assert p2 == p

    def test_from_attributes_config(self) -> None:
        """model_config should have from_attributes = True."""
        assert Project.model_config.get("from_attributes") is True


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------


class TestTask:
    """Task model validation and serialization."""

    def test_defaults(self) -> None:
        """Default values should match PRD spec."""
        t = make_task()
        assert t.status == TaskStatus.BACKLOG
        assert t.description == ""
        assert t.depends_on == []
        assert t.review is None
        assert t.execution is None
        assert t.completed_at is None

    def test_task_with_review(self) -> None:
        """Task with embedded ReviewState."""
        review = ReviewState(
            rounds_total=2,
            rounds_completed=1,
            consensus_score=0.9,
        )
        t = make_task(review=review)
        assert t.review is not None
        assert t.review.consensus_score == 0.9

    def test_task_with_execution(self) -> None:
        """Task with embedded ExecutionState."""
        execution = ExecutionState(
            started_at=datetime.now(UTC),
            retry_count=1,
            result="failed",
            error_summary="Timeout",
        )
        t = make_task(execution=execution)
        assert t.execution is not None
        assert t.execution.result == "failed"

    def test_serialization_roundtrip(self) -> None:
        """Full round-trip serialization."""
        now = datetime.now(UTC)
        t = make_task(
            status=TaskStatus.RUNNING,
            depends_on=["P1:T-P1-1"],
            review=ReviewState(rounds_completed=2),
            execution=ExecutionState(started_at=now),
            created_at=now,
            updated_at=now,
        )
        data = t.model_dump(mode="json")
        t2 = Task.model_validate(data)
        assert t2.id == t.id
        assert t2.status == t.status
        assert t2.review.rounds_completed == 2
        assert t2.execution.started_at is not None


# ---------------------------------------------------------------------------
# ReviewState + LLMReview
# ---------------------------------------------------------------------------


class TestReviewState:
    """ReviewState model tests."""

    def test_defaults(self) -> None:
        rs = ReviewState()
        assert rs.rounds_total == 3
        assert rs.rounds_completed == 0
        assert rs.reviews == []
        assert rs.human_decision_needed is False

    def test_with_reviews(self) -> None:
        review = LLMReview(
            model="claude-sonnet-4-5",
            focus="feasibility",
            verdict="approve",
            summary="Looks good",
            timestamp=datetime.now(UTC),
        )
        rs = ReviewState(reviews=[review], rounds_completed=1)
        assert len(rs.reviews) == 1
        assert rs.reviews[0].verdict == "approve"

    def test_roundtrip(self) -> None:
        rs = ReviewState(
            consensus_score=0.85,
            decision_points=["Should we use Redis?"],
            human_choice="yes",
        )
        data = rs.model_dump(mode="json")
        rs2 = ReviewState.model_validate(data)
        assert rs2.consensus_score == 0.85
        assert rs2.decision_points == ["Should we use Redis?"]


# ---------------------------------------------------------------------------
# ExecutionState
# ---------------------------------------------------------------------------


class TestExecutionState:
    """ExecutionState model tests."""

    def test_defaults(self) -> None:
        es = ExecutionState()
        assert es.retry_count == 0
        assert es.max_retries == 3
        assert es.result == "pending"
        assert es.log_tail == []

    def test_with_data(self) -> None:
        es = ExecutionState(
            exit_code=1,
            log_tail=["line1", "line2"],
            result="failed",
            error_summary="exit code 1",
        )
        assert es.exit_code == 1
        assert len(es.log_tail) == 2

    def test_roundtrip(self) -> None:
        now = datetime.now(UTC)
        es = ExecutionState(started_at=now, finished_at=now, exit_code=0, result="success")
        data = es.model_dump(mode="json")
        es2 = ExecutionState.model_validate(data)
        assert es2.result == "success"


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


class TestDependency:
    """Dependency model tests."""

    def test_basic(self) -> None:
        d = Dependency(upstream_task="P0:T-1", downstream_task="P1:T-2")
        assert d.fulfilled is False
        assert d.contract_path is None

    def test_with_contract(self) -> None:
        d = Dependency(
            upstream_task="P2:T-output",
            downstream_task="P3:T-import",
            contract_path="contracts/schema.json",
            fulfilled=True,
        )
        assert d.fulfilled is True
        assert d.contract_path == "contracts/schema.json"

    def test_roundtrip(self) -> None:
        d = Dependency(upstream_task="P0:T-1", downstream_task="P1:T-2", fulfilled=True)
        data = d.model_dump(mode="json")
        d2 = Dependency.model_validate(data)
        assert d2 == d
