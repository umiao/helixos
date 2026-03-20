"""Utility for extracting task IDs from TASKS.md sections.

Used by pre-commit and session hooks to detect duplicate task IDs
across sections (e.g., a task appearing in both Active and Completed).
"""
import re


def _extract_section_task_ids(content: str, section_name: str) -> set[str]:
    """Extract all task IDs (T-P*-N) from a named ## section in TASKS.md."""
    pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return set()
    return set(re.findall(r"(T-P\d+-\d+)", match.group(1)))
