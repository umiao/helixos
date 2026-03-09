"""Eval test cases for prompt templates.

Verifies that prompt templates render correctly with expected content.
These are prompt content tests (no SDK calls), checking that templates
include few-shot examples, anti-patterns, schema guidance, scope
constraints, and other production-grade prompt features added in T-P1-115.
"""

from __future__ import annotations

import pytest

from src.prompt_loader import clear_cache, load_prompt, render_prompt


@pytest.fixture(autouse=True)
def _clear_prompt_cache() -> None:
    """Clear prompt cache before each test for isolation."""
    clear_cache()


# ------------------------------------------------------------------
# Plan prompt eval cases
# ------------------------------------------------------------------


class TestPlanPromptEval:
    """Eval tests for plan_system.md prompt template."""

    def test_few_shot_example_present(self) -> None:
        """Plan prompt contains a few-shot example of good task decomposition."""
        content = load_prompt("plan_system")
        assert "Few-Shot Example" in content
        assert "proposed_tasks" in content
        assert "acceptance_criteria" in content

    def test_anti_patterns_present(self) -> None:
        """Plan prompt contains anti-pattern examples to avoid."""
        content = load_prompt("plan_system")
        assert "Anti-Patterns" in content
        assert "Too many tasks" in content
        assert "Vague acceptance criteria" in content
        assert "Scope creep" in content

    def test_task_scope_guidance_present(self) -> None:
        """Plan prompt contains task scope guidance note."""
        content = load_prompt("plan_system")
        assert "Task Scope Guidance" in content
        assert "fewer, well-scoped tasks" in content

    def test_schema_docs_preserved(self) -> None:
        """Plan prompt still contains existing schema documentation after render."""
        content = render_prompt("plan_system")
        assert "T-P{priority}-{number}" in content
        assert "Complexity" in content
        assert '"plan"' in content


# ------------------------------------------------------------------
# Review prompt eval cases
# ------------------------------------------------------------------


class TestReviewPromptEval:
    """Eval tests for review.md prompt template."""

    def test_new_schema_fields_documented(self) -> None:
        """Review prompt documents blocking_issues, suggestions, pass fields."""
        content = load_prompt("review")
        assert "blocking_issues" in content
        assert "suggestions" in content
        assert '"pass"' in content

    def test_severity_levels_documented(self) -> None:
        """Review prompt documents severity levels for blocking issues."""
        content = load_prompt("review")
        assert "high" in content
        assert "medium" in content

    def test_reviewer_role_placeholder(self) -> None:
        """Review prompt has reviewer_role placeholder for per-focus rendering."""
        content = load_prompt("review")
        assert "{{reviewer_role}}" in content

    def test_rendered_feasibility_contains_schema(self) -> None:
        """Rendered feasibility review prompt contains new schema instruction."""
        rendered = render_prompt(
            "review",
            reviewer_role="You are an expert code reviewer focusing on feasibility.",
            review_questions="1. Is this plan feasible?",
        )
        assert "blocking_issues" in rendered
        assert '"pass"' in rendered
        assert "feasibility" in rendered


# ------------------------------------------------------------------
# Execution prompt eval cases
# ------------------------------------------------------------------


class TestExecutionPromptEval:
    """Eval tests for execution.md and execution_system.md prompt templates."""

    def test_task_placeholders_present(self) -> None:
        """Execution prompt contains task ID, title, description placeholders."""
        content = load_prompt("execution")
        assert "{{local_task_id}}" in content
        assert "{{title}}" in content
        assert "{{description}}" in content

    def test_scope_constraint_present(self) -> None:
        """Execution prompt contains scope constraint reminder."""
        content = load_prompt("execution")
        assert "do not fix unrelated issues" in content.lower()

    def test_rendered_contains_task_data(self) -> None:
        """Rendered execution prompt contains task-specific data."""
        rendered = render_prompt(
            "execution",
            local_task_id="T-P0-42",
            title="Fix the authentication bug",
            description="Users cannot log in when session expires.",
        )
        assert "T-P0-42" in rendered
        assert "Fix the authentication bug" in rendered
        assert "Users cannot log in" in rendered

    def test_system_prompt_focused_agent_role(self) -> None:
        """Execution system prompt emphasizes focused implementation agent role."""
        content = load_prompt("execution_system")
        assert "focused implementation agent" in content.lower()

    def test_system_prompt_file_constraint(self) -> None:
        """Execution system prompt has file modification constraint with exception."""
        content = load_prompt("execution_system")
        assert "test files" in content.lower()
        assert "config files" in content.lower()


# ------------------------------------------------------------------
# Enrichment prompt eval cases
# ------------------------------------------------------------------


class TestEnrichmentPromptEval:
    """Eval tests for enrichment_system.md prompt template."""

    def test_scope_expansion_prohibition(self) -> None:
        """Enrichment prompt forbids scope expansion."""
        content = load_prompt("enrichment_system")
        assert "Do NOT expand the scope" in content

    def test_plan_context_note(self) -> None:
        """Enrichment prompt mentions receiving plan context."""
        content = load_prompt("enrichment_system")
        assert "plan context" in content.lower()

    def test_json_response_format(self) -> None:
        """Enrichment prompt specifies JSON response format."""
        content = load_prompt("enrichment_system")
        assert '"description"' in content
        assert '"priority"' in content
