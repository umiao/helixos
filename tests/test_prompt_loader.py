"""Tests for src.prompt_loader -- prompt template loading and rendering.

Updated in T-P1-120: prompt files consolidated from 9 to 4.
"""

from __future__ import annotations

import pytest

from src.prompt_loader import _expand_includes, clear_cache, load_prompt, render_prompt

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


# -- plan_system.md uses {{include:_shared_rules.md}} --


def test_plan_system_has_include_directive() -> None:
    """plan_system.md uses {{include:_shared_rules.md}} for shared rules."""
    raw = load_prompt("plan_system")
    assert "{{include:_shared_rules.md}}" in raw


def test_plan_system_renders_self_contained() -> None:
    """After render, plan_system contains expanded shared rules."""
    content = render_prompt("plan_system")
    # Must NOT have any unresolved {{include:...}} directives
    assert "{{include:" not in content
    # Must contain content from _shared_rules.md
    assert "T-P{priority}-{number}" in content
    assert "Complexity" in content
    assert "Scenario matrix" in content
    assert "Journey-first" in content
    # Must now include State Machine and Smoke Test (previously missing)
    assert "State Machine Rules" in content
    assert "Smoke Test Enforcement" in content


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
    assert "{{include:_shared_rules.md}}" in content


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


def test_review_template_contains_shared_rules_after_render() -> None:
    """Rendered review template contains all shared rules via include."""
    rendered = render_prompt(
        "review",
        reviewer_role="You are a code reviewer.",
        review_questions="1. Is this plan sound?",
    )
    assert "State Machine Rules" in rendered
    assert "Smoke Test Enforcement" in rendered
    assert "Task Planning Rules" in rendered
    assert "Key Constraints" in rendered
    assert "{{include:" not in rendered


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


def test_no_unresolved_placeholders_enrichment() -> None:
    """Enrichment system prompt must have no {{...}} placeholders."""
    content = load_prompt("enrichment_system")
    assert "{{" not in content, "enrichment_system.md has unresolved placeholder"


def test_no_unresolved_placeholders_plan_rendered() -> None:
    """Plan system rendered prompt must have no {{...}} placeholders."""
    content = render_prompt("plan_system")
    assert "{{" not in content, "plan_system.md has unresolved placeholder after render"


# -- Diff test: consolidated prompts are content-equivalent --


def test_plan_system_content_equivalent() -> None:
    """Rendered plan_system contains all content from the old 3-file chain."""
    content = render_prompt("plan_system")
    # Key phrases from old task_schema_context.md (now in _shared_rules.md)
    assert "Do NOT assign IDs in your proposals" in content
    assert "Acceptance Criteria" in content
    # Key phrases from old project_rules_context.md (now in _shared_rules.md)
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


# -- Include directive tests (T-P1-124) --


def test_include_expands_file() -> None:
    """{{include:_shared_rules.md}} expands to file contents."""
    expanded = _expand_includes("Before\n{{include:_shared_rules.md}}\nAfter")
    assert "Before" in expanded
    assert "After" in expanded
    assert "Task Schema" in expanded
    assert "{{include:" not in expanded


def test_include_missing_file_raises() -> None:
    """{{include:nonexistent.md}} raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        _expand_includes("{{include:nonexistent_file.md}}")


def test_shared_section_identical_in_both_prompts() -> None:
    """Both plan and review prompts contain the same shared rules section."""
    plan = render_prompt("plan_system")
    review = render_prompt(
        "review",
        reviewer_role="You are a code reviewer.",
        review_questions="1. Is this plan sound?",
    )
    # Extract shared section by finding common rule headers
    shared_headers = [
        "Task Schema (from TASKS.md conventions)",
        "Project Rules (from CLAUDE.md)",
        "Task Planning Rules",
        "Key Constraints",
        "State Machine Rules",
        "Smoke Test Enforcement",
        "Anti-Patterns (avoid these)",
    ]
    for header in shared_headers:
        assert header in plan, f"Plan missing: {header}"
        assert header in review, f"Review missing: {header}"


def test_rule_coverage_parity_plan_vs_review() -> None:
    """All rule sections in review must also appear in plan (and vice versa).

    T-P1-125: Ensures the planner knows every rule the reviewer checks against,
    and the reviewer knows every anti-pattern the planner should avoid.
    """
    plan = render_prompt("plan_system")
    review = render_prompt(
        "review",
        reviewer_role="You are a code reviewer.",
        review_questions="1. Is this plan sound?",
    )
    # Every rule section header that must appear in BOTH prompts
    rule_headers = [
        "Task Planning Rules",
        "Key Constraints",
        "State Machine Rules",
        "Smoke Test Enforcement",
        "Anti-Patterns (avoid these)",
    ]
    for header in rule_headers:
        assert header in plan, f"Plan prompt missing rule section: {header}"
        assert header in review, f"Review prompt missing rule section: {header}"


def test_include_variable_resolution() -> None:
    """Variables inside included files are resolved by caller's kwargs.

    _shared_rules.md uses T-P{priority}-{number} which should NOT be
    treated as a variable (it's literal text). But if an included file
    had {{some_var}}, the caller's kwargs would resolve it.
    """
    # render_prompt expands includes THEN substitutes variables.
    # The review template has {{reviewer_role}} and {{review_questions}} --
    # these appear in the raw template, not the included file, but the
    # mechanism works the same way: includes first, then substitution.
    rendered = render_prompt(
        "review",
        reviewer_role="TEST_ROLE",
        review_questions="TEST_QUESTIONS",
    )
    assert "TEST_ROLE" in rendered
    assert "TEST_QUESTIONS" in rendered
    assert "{{reviewer_role}}" not in rendered
    assert "{{review_questions}}" not in rendered
    assert "{{include:" not in rendered
