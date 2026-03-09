"""Tests for structured plan_json injection into review pipeline.

Verifies that _format_plan_json_for_review() correctly formats plan data
and that _call_reviewer() injects structured plan data into user content
when task.plan_json is available.

Created for T-P1-123.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from src.config import ReviewerConfig, ReviewPipelineConfig
from src.models import TaskStatus
from src.review_pipeline import ReviewPipeline, _format_plan_json_for_review
from src.sdk_adapter import ClaudeEvent, ClaudeEventType
from tests.factories import make_task


def _make_review_events(verdict: str = "approve") -> list[ClaudeEvent]:
    """Create minimal review events for mocking."""
    pass_value = verdict == "approve"
    blocking = [] if pass_value else [{"issue": "Problem found", "severity": "high"}]
    inner = {
        "blocking_issues": blocking,
        "suggestions": [],
        "pass": pass_value,
    }
    return [
        ClaudeEvent(type=ClaudeEventType.INIT, session_id="sess-test"),
        ClaudeEvent(
            type=ClaudeEventType.RESULT,
            structured_output=inner,
            result_text=None,
            model="claude-sonnet-4-5",
            session_id="sess-test",
        ),
    ]


SAMPLE_PLAN_JSON = json.dumps({
    "steps": [
        {"step": "Add validation to input handler", "files": ["src/handler.py"]},
        {"step": "Write unit tests", "files": ["tests/test_handler.py"]},
    ],
    "acceptance_criteria": [
        "Input validation rejects empty strings",
        "Unit tests cover all branches",
    ],
    "proposed_tasks": [
        {"title": "Implement validation", "depends_on": []},
        {"title": "Add tests", "depends_on": ["Implement validation"]},
    ],
})


# ------------------------------------------------------------------
# Tests for _format_plan_json_for_review
# ------------------------------------------------------------------


class TestFormatPlanJsonForReview:
    """Tests for the _format_plan_json_for_review helper."""

    def test_none_returns_empty(self) -> None:
        """None plan_json returns empty string."""
        assert _format_plan_json_for_review(None) == ""

    def test_empty_string_returns_empty(self) -> None:
        """Empty string plan_json returns empty string."""
        assert _format_plan_json_for_review("") == ""

    def test_malformed_json_returns_empty(self) -> None:
        """Malformed JSON returns empty string (no crash)."""
        assert _format_plan_json_for_review("{bad json") == ""

    def test_non_dict_json_returns_empty(self) -> None:
        """JSON that is not a dict returns empty string."""
        assert _format_plan_json_for_review(json.dumps([1, 2, 3])) == ""

    def test_steps_formatted_with_indices(self) -> None:
        """Steps are formatted with 'Step N:' prefix."""
        result = _format_plan_json_for_review(SAMPLE_PLAN_JSON)
        assert "Step 1: Add validation to input handler" in result
        assert "Step 2: Write unit tests" in result

    def test_steps_include_files(self) -> None:
        """Step files are listed beneath each step."""
        result = _format_plan_json_for_review(SAMPLE_PLAN_JSON)
        assert "- File: src/handler.py" in result
        assert "- File: tests/test_handler.py" in result

    def test_acceptance_criteria_indexed(self) -> None:
        """Acceptance criteria are formatted with 'AC N:' prefix."""
        result = _format_plan_json_for_review(SAMPLE_PLAN_JSON)
        assert "AC 1: Input validation rejects empty strings" in result
        assert "AC 2: Unit tests cover all branches" in result

    def test_proposed_tasks_indexed_with_deps(self) -> None:
        """Proposed tasks are formatted with dependencies."""
        result = _format_plan_json_for_review(SAMPLE_PLAN_JSON)
        assert "Task 1: Implement validation" in result
        assert "Task 2: Add tests [depends: Implement validation]" in result

    def test_section_delimiters_present(self) -> None:
        """Structured data is wrapped in delimiter markers."""
        result = _format_plan_json_for_review(SAMPLE_PLAN_JSON)
        assert "--- Structured Plan Data ---" in result
        assert "--- End Structured Plan Data ---" in result

    def test_steps_only(self) -> None:
        """Plan with only steps (no ACs or tasks) still formats."""
        plan = json.dumps({"steps": [{"step": "Do something", "files": []}]})
        result = _format_plan_json_for_review(plan)
        assert "Step 1: Do something" in result
        assert "Acceptance Criteria" not in result
        assert "Proposed Sub-Tasks" not in result

    def test_string_steps(self) -> None:
        """Steps can be plain strings instead of dicts."""
        plan = json.dumps({"steps": ["First step", "Second step"]})
        result = _format_plan_json_for_review(plan)
        assert "Step 1: First step" in result
        assert "Step 2: Second step" in result

    def test_string_proposed_tasks(self) -> None:
        """Proposed tasks can be plain strings."""
        plan = json.dumps({"proposed_tasks": ["Task A", "Task B"]})
        result = _format_plan_json_for_review(plan)
        assert "Task 1: Task A" in result
        assert "Task 2: Task B" in result

    def test_empty_dict_returns_empty(self) -> None:
        """Empty dict (no steps/ACs/tasks) returns empty string."""
        assert _format_plan_json_for_review(json.dumps({})) == ""


# ------------------------------------------------------------------
# Tests for _call_reviewer integration with plan_json
# ------------------------------------------------------------------


class TestCallReviewerPlanJsonInjection:
    """Tests that _call_reviewer injects structured plan data into user content."""

    @pytest.fixture()
    def pipeline(self) -> ReviewPipeline:
        """Create a ReviewPipeline with a single reviewer."""
        config = ReviewPipelineConfig(
            reviewers=[
                ReviewerConfig(
                    model="claude-sonnet-4-5",
                    focus="feasibility_and_edge_cases",
                    required=True,
                ),
            ],
        )
        return ReviewPipeline(config)

    @pytest.mark.asyncio()
    async def test_plan_json_injected_into_user_content(
        self, pipeline: ReviewPipeline,
    ) -> None:
        """When task has plan_json, structured data appears in prompt sent to SDK."""
        task = make_task(status=TaskStatus.REVIEW, description="A test task description", plan_json=SAMPLE_PLAN_JSON)
        captured_prompts: list[str] = []

        async def _mock_query(prompt: str, options: Any = None):
            captured_prompts.append(prompt)
            for event in _make_review_events("approve"):
                yield event

        with (
            patch("src.review_pipeline.run_claude_query", side_effect=_mock_query),
            patch("src.review_pipeline.get_session_context", return_value=""),
        ):
                reviewer = pipeline.reviewers[0]
                await pipeline._call_reviewer(reviewer, task, "Plan text here")

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        # Verify structured sections are present
        assert "--- Structured Plan Data ---" in prompt
        assert "Step 1: Add validation to input handler" in prompt
        assert "AC 1: Input validation rejects empty strings" in prompt
        assert "Task 1: Implement validation" in prompt

    @pytest.mark.asyncio()
    async def test_no_plan_json_no_structured_section(
        self, pipeline: ReviewPipeline,
    ) -> None:
        """When task has no plan_json, no structured section in prompt."""
        task = make_task(status=TaskStatus.REVIEW, description="A test task description", plan_json=None)
        captured_prompts: list[str] = []

        async def _mock_query(prompt: str, options: Any = None):
            captured_prompts.append(prompt)
            for event in _make_review_events("approve"):
                yield event

        with (
            patch("src.review_pipeline.run_claude_query", side_effect=_mock_query),
            patch("src.review_pipeline.get_session_context", return_value=""),
        ):
                reviewer = pipeline.reviewers[0]
                await pipeline._call_reviewer(reviewer, task, "Plan text here")

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "--- Structured Plan Data ---" not in prompt
        # Original plan content still present
        assert "Plan:\nPlan text here" in prompt

    @pytest.mark.asyncio()
    async def test_malformed_plan_json_falls_back_gracefully(
        self, pipeline: ReviewPipeline,
    ) -> None:
        """Malformed plan_json doesn't crash; falls back to description only."""
        task = make_task(status=TaskStatus.REVIEW, description="A test task description", plan_json="{invalid json")
        captured_prompts: list[str] = []

        async def _mock_query(prompt: str, options: Any = None):
            captured_prompts.append(prompt)
            for event in _make_review_events("approve"):
                yield event

        with (
            patch("src.review_pipeline.run_claude_query", side_effect=_mock_query),
            patch("src.review_pipeline.get_session_context", return_value=""),
        ):
                reviewer = pipeline.reviewers[0]
                await pipeline._call_reviewer(reviewer, task, "Plan text here")

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "--- Structured Plan Data ---" not in prompt
        assert "Plan:\nPlan text here" in prompt

    @pytest.mark.asyncio()
    async def test_structured_data_after_plan_content(
        self, pipeline: ReviewPipeline,
    ) -> None:
        """Structured plan data appears after the plan content, not replacing it."""
        task = make_task(status=TaskStatus.REVIEW, description="A test task description", plan_json=SAMPLE_PLAN_JSON)
        captured_prompts: list[str] = []

        async def _mock_query(prompt: str, options: Any = None):
            captured_prompts.append(prompt)
            for event in _make_review_events("approve"):
                yield event

        with (
            patch("src.review_pipeline.run_claude_query", side_effect=_mock_query),
            patch("src.review_pipeline.get_session_context", return_value=""),
        ):
                reviewer = pipeline.reviewers[0]
                await pipeline._call_reviewer(reviewer, task, "My plan details")

        prompt = captured_prompts[0]
        plan_pos = prompt.index("Plan:\nMy plan details")
        structured_pos = prompt.index("--- Structured Plan Data ---")
        assert structured_pos > plan_pos, "Structured data should come after plan content"
