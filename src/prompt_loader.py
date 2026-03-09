"""Load agent prompt templates from config/prompts/ directory.

Provides a single entry point ``load_prompt(name)`` that reads a .md file
from ``config/prompts/``, caches it in memory, and returns the content.
Supports ``{{variable}}`` template substitution via ``render_prompt()``
and ``{{include:filename}}`` for composing prompts from shared fragments.

Created in T-P1-113 to externalize inline prompt constants.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"

# Module-level cache: prompt name -> file content
_cache: dict[str, str] = {}

# Regex for {{include:filename}} directives
_INCLUDE_RE = re.compile(r"\{\{include:([^}]+)\}\}")


def load_prompt(name: str) -> str:
    """Load a prompt template by name from ``config/prompts/{name}.md``.

    Results are cached in memory after first load.

    Args:
        name: Prompt name without extension (e.g., ``"plan_system"``).

    Returns:
        The prompt file content as a string.

    Raises:
        FileNotFoundError: If the prompt file does not exist.
    """
    if name in _cache:
        return _cache[name]

    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    content = path.read_text(encoding="utf-8")
    _cache[name] = content
    return content


def _expand_includes(template: str) -> str:
    """Expand ``{{include:filename}}`` directives by inlining file contents.

    Reads each referenced file from ``config/prompts/`` and replaces the
    directive with the file's content.  Nested includes are not supported
    (single-level expansion only).

    Args:
        template: The template string potentially containing include directives.

    Returns:
        The template with all include directives replaced by file contents.

    Raises:
        FileNotFoundError: If an included file does not exist.
    """
    def _replace(match: re.Match[str]) -> str:
        filename = match.group(1).strip()
        path = _PROMPTS_DIR / filename
        if not path.is_file():
            raise FileNotFoundError(
                f"Included prompt file not found: {path}"
            )
        return path.read_text(encoding="utf-8")

    return _INCLUDE_RE.sub(_replace, template)


def render_prompt(name: str, **kwargs: str) -> str:
    """Load a prompt template and substitute ``{{key}}`` placeholders.

    Include expansion (``{{include:filename}}``) happens BEFORE variable
    substitution, so ``{{variable}}`` placeholders inside included files
    are resolved by the caller's kwargs.

    Args:
        name: Prompt name (passed to ``load_prompt``).
        **kwargs: Key-value pairs for template substitution.

    Returns:
        The rendered prompt string.

    Raises:
        FileNotFoundError: If the prompt file or any included file
            does not exist.
    """
    template = load_prompt(name)

    # Step 1: Expand {{include:filename}} directives
    template = _expand_includes(template)

    # Step 2: Substitute {{variable}} placeholders
    for key, value in kwargs.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def clear_cache() -> None:
    """Clear the prompt cache (useful for testing)."""
    _cache.clear()
