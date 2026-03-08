"""Tests for src.prompt_loader -- prompt template loading and rendering."""

from __future__ import annotations

import pytest

from src.prompt_loader import clear_cache, load_prompt, render_prompt

# All prompt files that must exist in config/prompts/
EXPECTED_PROMPTS = [
    "enrichment_system",
    "task_schema_context",
    "project_rules_context",
    "plan_system",
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


def test_render_prompt_substitutes_variables() -> None:
    """render_prompt replaces {{key}} placeholders."""
    rendered = render_prompt(
        "plan_system",
        task_schema_context="[SCHEMA]",
        project_rules_context="[RULES]",
    )
    assert "[SCHEMA]" in rendered
    assert "[RULES]" in rendered
    assert "{{task_schema_context}}" not in rendered
    assert "{{project_rules_context}}" not in rendered


def test_render_prompt_execution_template() -> None:
    """Execution prompt renders with task variables."""
    rendered = render_prompt(
        "execution_prompt",
        local_task_id="T-P0-42",
        title="Fix the bug",
        description="A detailed description here.",
    )
    assert "T-P0-42" in rendered
    assert "Fix the bug" in rendered
    assert "A detailed description here." in rendered


# Key phrase checks: verify prompts contain expected content
def test_enrichment_prompt_mentions_priority() -> None:
    """Enrichment prompt includes priority guidance."""
    content = load_prompt("enrichment_system")
    assert "priority" in content.lower()
    assert "P0" in content


def test_plan_system_prompt_mentions_steps() -> None:
    """Plan prompt mentions implementation steps."""
    content = load_prompt("plan_system")
    assert "steps" in content.lower()
    assert "proposed" in content.lower()


def test_review_feasibility_mentions_edge_cases() -> None:
    """Feasibility review prompt mentions edge cases."""
    content = load_prompt("review_feasibility")
    assert "edge case" in content.lower()


def test_review_adversarial_mentions_vulnerabilities() -> None:
    """Adversarial review prompt mentions vulnerabilities."""
    content = load_prompt("review_adversarial")
    assert "vulnerabilit" in content.lower()


def test_review_default_mentions_conventions() -> None:
    """Default review prompt mentions conventions."""
    content = load_prompt("review_default")
    assert "convention" in content.lower()


def test_execution_prompt_has_placeholders() -> None:
    """Execution prompt template has the expected placeholders."""
    content = load_prompt("execution_prompt")
    assert "{{local_task_id}}" in content
    assert "{{title}}" in content
    assert "{{description}}" in content
