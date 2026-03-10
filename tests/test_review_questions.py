"""Tests for the review clarifying questions workflow.

Tests question extraction from reviewer output, question answering,
and injection of answered questions into replan feedback.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from src.models import ReviewQuestion, ReviewState, Task
from src.review_pipeline import (
    BlockingIssue,
    ReviewResult,
    ReviewResultQuestion,
    extract_questions_from_review,
)
from src.routes.reviews import _build_replan_feedback

# ------------------------------------------------------------------
# extract_questions_from_review tests
# ------------------------------------------------------------------


class TestExtractQuestionsFromReview:
    """Tests for extract_questions_from_review()."""

    def test_explicit_questions_extracted(self) -> None:
        """Questions from the explicit 'questions' field are extracted."""
        result = ReviewResult(
            blocking_issues=[],
            suggestions=[],
            **{"pass": True},
            questions=[
                ReviewResultQuestion(question="What database will this use?"),
                ReviewResultQuestion(
                    question="Is there a migration strategy?",
                    context="Critical for production deployments.",
                ),
            ],
        )
        questions = extract_questions_from_review(result, "feasibility")
        assert len(questions) == 2
        assert questions[0].text == "What database will this use?"
        assert questions[0].source_reviewer == "feasibility"
        assert questions[1].text == "Is there a migration strategy?"

    def test_fallback_questions_from_suggestions(self) -> None:
        """Questions are extracted from suggestions ending with '?'."""
        result = ReviewResult(
            blocking_issues=[],
            suggestions=[
                "Consider adding error handling. Have you tested edge cases?",
                "The design looks good overall.",
            ],
            **{"pass": True},
            questions=[],
        )
        questions = extract_questions_from_review(result, "code_quality")
        assert len(questions) == 1
        assert "Have you tested edge cases?" in questions[0].text

    def test_fallback_questions_from_blocking_issues(self) -> None:
        """Questions are extracted from blocking issues ending with '?'."""
        result = ReviewResult(
            blocking_issues=[
                BlockingIssue(
                    issue="Missing auth check. What prevents unauthorized access?",
                    severity="high",
                ),
            ],
            suggestions=[],
            **{"pass": False},
            questions=[],
        )
        questions = extract_questions_from_review(result, "security")
        assert len(questions) == 1
        assert "What prevents unauthorized access?" in questions[0].text

    def test_deduplication(self) -> None:
        """Duplicate question texts are deduplicated."""
        result = ReviewResult(
            blocking_issues=[],
            suggestions=["Is this tested?"],
            **{"pass": True},
            questions=[
                ReviewResultQuestion(question="Is this tested?"),
            ],
        )
        questions = extract_questions_from_review(result, "test")
        assert len(questions) == 1

    def test_no_questions_when_none_found(self) -> None:
        """Empty list returned when no questions exist."""
        result = ReviewResult(
            blocking_issues=[],
            suggestions=["Looks good."],
            **{"pass": True},
            questions=[],
        )
        questions = extract_questions_from_review(result, "general")
        assert questions == []

    def test_unique_ids(self) -> None:
        """Each question gets a unique ID."""
        result = ReviewResult(
            blocking_issues=[],
            suggestions=[],
            **{"pass": True},
            questions=[
                ReviewResultQuestion(question="Q1?"),
                ReviewResultQuestion(question="Q2?"),
            ],
        )
        questions = extract_questions_from_review(result, "test")
        assert len(questions) == 2
        assert questions[0].id != questions[1].id


# ------------------------------------------------------------------
# ReviewQuestion model tests
# ------------------------------------------------------------------


class TestReviewQuestionModel:
    """Tests for the ReviewQuestion Pydantic model."""

    def test_defaults(self) -> None:
        """ReviewQuestion has sensible defaults."""
        q = ReviewQuestion(id="abc123", text="What is the plan?")
        assert q.answer == ""
        assert q.source_reviewer == ""
        assert q.answered_at is None

    def test_serialization_roundtrip(self) -> None:
        """ReviewQuestion survives JSON serialization/deserialization."""
        q = ReviewQuestion(
            id="abc123",
            text="What is the plan?",
            answer="We will use SQLite.",
            source_reviewer="feasibility",
            answered_at=datetime.now(UTC),
        )
        data = json.loads(q.model_dump_json())
        q2 = ReviewQuestion.model_validate(data)
        assert q2.id == q.id
        assert q2.text == q.text
        assert q2.answer == q.answer


# ------------------------------------------------------------------
# ReviewState.questions persistence tests
# ------------------------------------------------------------------


class TestReviewStateQuestions:
    """Tests for ReviewState with questions field."""

    def test_default_empty(self) -> None:
        """ReviewState.questions defaults to empty list."""
        state = ReviewState()
        assert state.questions == []

    def test_questions_in_review_state(self) -> None:
        """ReviewState can hold questions."""
        q = ReviewQuestion(id="q1", text="How?", source_reviewer="test")
        state = ReviewState(questions=[q])
        assert len(state.questions) == 1
        assert state.questions[0].text == "How?"

    def test_serialization_with_questions(self) -> None:
        """ReviewState with questions survives JSON roundtrip."""
        q = ReviewQuestion(id="q1", text="How?", answer="Like this.")
        state = ReviewState(questions=[q])
        data = json.loads(state.model_dump_json())
        state2 = ReviewState.model_validate(data)
        assert len(state2.questions) == 1
        assert state2.questions[0].answer == "Like this."

    def test_backward_compat_no_questions_field(self) -> None:
        """Existing review_json without 'questions' key deserializes cleanly."""
        old_data = {
            "rounds_total": 1,
            "rounds_completed": 1,
            "reviews": [],
            "consensus_score": 1.0,
            "human_decision_needed": False,
            "decision_points": [],
            "human_choice": None,
            "lifecycle_state": "approved",
        }
        state = ReviewState.model_validate(old_data)
        assert state.questions == []


# ------------------------------------------------------------------
# _build_replan_feedback with questions tests
# ------------------------------------------------------------------


class TestBuildReplanFeedbackWithQuestions:
    """Tests for _build_replan_feedback including answered questions."""

    def _make_task_with_questions(
        self,
        questions: list[ReviewQuestion],
    ) -> Task:
        """Create a minimal Task with review questions."""
        return Task(
            id="test:1",
            project_id="proj",
            local_task_id="1",
            title="Test",
            executor_type="code",
            review=ReviewState(questions=questions),
        )

    def test_answered_questions_included(self) -> None:
        """Answered questions appear in replan feedback."""
        q = ReviewQuestion(
            id="q1",
            text="What database?",
            answer="PostgreSQL",
            source_reviewer="feasibility",
        )
        task = self._make_task_with_questions([q])
        feedback = _build_replan_feedback(task, "Please fix")
        assert "Q (feasibility): What database?" in feedback
        assert "A: PostgreSQL" in feedback
        assert "Please fix" in feedback

    def test_unanswered_questions_excluded(self) -> None:
        """Unanswered questions are not in replan feedback."""
        q = ReviewQuestion(
            id="q1",
            text="What database?",
            answer="",
            source_reviewer="feasibility",
        )
        task = self._make_task_with_questions([q])
        feedback = _build_replan_feedback(task, "")
        assert "What database?" not in feedback

    def test_mixed_answered_unanswered(self) -> None:
        """Only answered questions are included, unanswered are skipped."""
        questions = [
            ReviewQuestion(
                id="q1", text="Q1?", answer="A1", source_reviewer="r1",
            ),
            ReviewQuestion(
                id="q2", text="Q2?", answer="", source_reviewer="r2",
            ),
        ]
        task = self._make_task_with_questions(questions)
        feedback = _build_replan_feedback(task, "")
        assert "Q (r1): Q1?" in feedback
        assert "A: A1" in feedback
        assert "Q2?" not in feedback

    def test_no_questions_no_section(self) -> None:
        """When no questions exist, no questions section is added."""
        task = self._make_task_with_questions([])
        feedback = _build_replan_feedback(task, "reason")
        assert "Answered clarifying questions" not in feedback
        assert "reason" in feedback
