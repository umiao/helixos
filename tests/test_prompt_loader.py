"""Tests for src.prompt_loader -- prompt template loading and rendering.

Updated in T-P1-120: prompt files consolidated from 9 to 4.
"""

from __future__ import annotations

import pytest

from src.prompt_loader import clear_cache, load_prompt, render_prompt

# All prompt files that must exist in config/prompts/ (consolidated set)
EXPECTED_PROMPTS = [
    "enrichment_system",
    "execution_system",
    "plan_system",
    "review",
    "execution",
]

# Files that must NOT exist after consolidation
DELETED_PROMPTS = [
    "task_schema_context",
    "project_rules_context",
    "review_conventions_context",
    "review_feasibility",
    "review_adversarial",
    "review_default",
    "execution_prompt",
]


@pytest.fixture(autouse=True)
def _clear_prompt_cache() -> None:
    """Clear prompt cache before each test for isolation."""
    clear_cache()


@pytest.mark.parametrize("name", EXPECTED_PROMPTS)
def test_load_prompt_returns_nonempty(name: str) -> None:
    """Each prompt file loads and returns non-empty content."""
    content = load_prompt(name)
    assert isinstance(content, str)
    assert len(content.strip()) > 0


def test_load_prompt_missing_raises() -> None:
    """Loading a non-existent prompt raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_prompt("nonexistent_prompt_xyz")


@pytest.mark.parametrize("name", DELETED_PROMPTS)
def test_deleted_prompts_raise(name: str) -> None:
    """Deleted prompt files must no longer be loadable."""
    with pytest.raises(FileNotFoundError):
        load_prompt(name)


def test_load_prompt_caches() -> None:
    """Second call returns same object (cached)."""
    first = load_prompt("enrichment_system")
    second = load_prompt("enrichment_system")
    assert first is second


def test_clear_cache_works() -> None:
    """After clear_cache, prompts are reloaded from disk."""
    first = load_prompt("enrichment_system")
    clear_cache()
    second = load_prompt("enrichment_system")
    assert first == second
    # Not the same object after cache clear
    assert first is not second


# -- plan_system.md is now self-contained (no fragment placeholders) --


def test_plan_system_is_self_contained() -> None:
    """plan_system.md contains inlined task schema and project rules directly."""
    content = load_prompt("plan_system")
    # Must NOT have any unresolved {{...}} placeholders
    assert "{{" not in content
    assert "}}" not in content
    # Must contain content that was previously in task_schema_context.md
    assert "T-P{priority}-{number}" in content
    assert "Complexity" in content
    # Must contain content that was previously in project_rules_context.md
    assert "Scenario matrix" in content
    assert "Journey-first" in content


def test_plan_system_prompt_mentions_steps() -> None:
    """Plan prompt mentions implementation steps."""
    content = load_prompt("plan_system")
    assert "steps" in content.lower()
    assert "proposed" in content.lower()


# -- review.md uses {{reviewer_role}} and {{review_questions}} --


def test_review_template_has_placeholders() -> None:
    """Unified review template has the expected parameterization placeholders."""
    content = load_prompt("review")
    assert "{{reviewer_role}}" in content
    assert "{{review_questions}}" in content


def test_review_template_renders_feasibility() -> None:
    """Review template renders correctly for feasibility focus."""
    rendered = render_prompt(
        "review",
        reviewer_role="You are an expert code reviewer focusing on feasibility.",
        review_questions="1. Is this plan feasible?\n2. Are there edge cases?",
    )
    assert "{{reviewer_role}}" not in rendered
    assert "{{review_questions}}" not in rendered
    assert "feasibility" in rendered
    assert "edge cases" in rendered


def test_review_template_renders_adversarial() -> None:
    """Review template renders correctly for adversarial focus."""
    rendered = render_prompt(
        "review",
        reviewer_role="You are an adversarial reviewer (red team).",
        review_questions="1. Could this introduce vulnerabilities?",
    )
    assert "adversarial" in rendered
    assert "vulnerabilities" in rendered


def test_review_template_renders_default() -> None:
    """Review template renders correctly for default focus."""
    rendered = render_prompt(
        "review",
        reviewer_role="You are a code reviewer.",
        review_questions="1. Is this plan sound?\n2. Does it follow conventions?",
    )
    assert "code reviewer" in rendered
    assert "conventions" in rendered


def test_review_template_contains_inlined_rules() -> None:
    """Review template contains inlined conventions, not fragment references."""
    content = load_prompt("review")
    # Previously in review_conventions_context.md:
    assert "State Machine Rules" in content
    assert "Smoke Test Enforcement" in content
    # Inlined from task_schema_context.md / project_rules_context.md:
    assert "Task Planning Rules" in content
    assert "Key Constraints" in content


# -- execution.md (renamed from execution_prompt.md) --


def test_execution_prompt_has_placeholders() -> None:
    """Execution prompt template has the expected placeholders."""
    content = load_prompt("execution")
    assert "{{local_task_id}}" in content
    assert "{{title}}" in content
    assert "{{description}}" in content


def test_render_prompt_execution_template() -> None:
    """Execution prompt renders with task variables."""
    rendered = render_prompt(
        "execution",
        local_task_id="T-P0-42",
        title="Fix the bug",
        description="A detailed description here.",
    )
    assert "T-P0-42" in rendered
    assert "Fix the bug" in rendered
    assert "A detailed description here." in rendered


# -- enrichment_system.md unchanged --


def test_enrichment_prompt_mentions_priority() -> None:
    """Enrichment prompt includes priority guidance."""
    content = load_prompt("enrichment_system")
    assert "priority" in content.lower()
    assert "P0" in content


# -- No unresolved placeholders in any prompt (except review.md which has params) --


@pytest.mark.parametrize("name", ["enrichment_system", "plan_system"])
def test_no_unresolved_placeholders(name: str) -> None:
    """Self-contained prompts must have no {{...}} placeholders."""
    content = load_prompt(name)
    assert "{{" not in content, f"{name}.md has unresolved placeholder"


# -- Diff test: consolidated prompts are content-equivalent --


def test_plan_system_content_equivalent() -> None:
    """Consolidated plan_system.md contains all content from the old 3-file chain."""
    content = load_prompt("plan_system")
    # Key phrases from old task_schema_context.md
    assert "Do NOT assign IDs in your proposals" in content
    assert "Acceptance Criteria" in content
    # Key phrases from old project_rules_context.md
    assert "Schema changes require migration" in content
    assert "No emoji characters" in content
    # Key phrases from old plan_system.md
    assert "software architect" in content
    assert "proposed sub-tasks" in content
    assert '"plan"' in content  # JSON schema reference


def test_review_rendered_content_equivalent() -> None:
    """Rendered review prompts contain all content from old multi-file chain."""
    rendered = render_prompt(
        "review",
        reviewer_role="You are an expert code reviewer focusing on feasibility and edge cases.",
        review_questions=(
            "Analyze the following task plan and determine:\n"
            "1. Is this plan technically feasible given the codebase context?\n"
            "2. Are there edge cases or failure modes not addressed?\n"
            "3. Are the acceptance criteria clear and testable?\n"
            "4. Does the plan follow the project's task planning rules and conventions?"
        ),
    )
    # Key phrases from old review_conventions_context.md
    assert "State Machine Rules" in rendered
    assert "Smoke Test Enforcement" in rendered
    # Key phrases from old review_feasibility.md
    assert "feasibility" in rendered
    assert "edge cases" in rendered
    # Structural: JSON response format preserved (new schema)
    assert '"blocking_issues"' in rendered
    assert '"suggestions"' in rendered
    assert '"pass"' in rendered
