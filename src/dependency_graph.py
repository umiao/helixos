"""Dependency graph validation and cycle detection.

Pure functions for validating task dependency graphs: missing reference
detection, DFS-based cycle detection, and priority extraction.
Extracted from scheduler.py (T-P1-112) to reduce module size and
enable reuse by task_generator.py.
"""

from __future__ import annotations

import logging
import re

from src.models import Task

logger = logging.getLogger(__name__)


def validate_dependency_graph(
    tasks: list[Task],
) -> tuple[list[str], list[list[str]]]:
    """Validate a dependency graph for missing references and cycles.

    Args:
        tasks: All active tasks to validate.

    Returns:
        A tuple of (missing_ref_errors, cycles) where:
        - missing_ref_errors: list of human-readable error strings
        - cycles: list of cycles, each a list of task IDs forming the cycle
    """
    task_ids = {t.id for t in tasks}
    missing_errors: list[str] = []
    adjacency: dict[str, list[str]] = {t.id: [] for t in tasks}

    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id not in task_ids:
                missing_errors.append(
                    f"Task {task.id} depends on {dep_id} which does not exist"
                )
            else:
                adjacency[task.id].append(dep_id)

    cycles = detect_cycles(adjacency)
    return missing_errors, cycles


def detect_cycles(adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Detect all cycles in a directed graph using DFS.

    Args:
        adjacency: Map of node -> list of nodes it depends on.

    Returns:
        List of cycles found, each cycle as a list of node IDs.
    """
    _white, _gray, _black = 0, 1, 2
    color: dict[str, int] = {node: _white for node in adjacency}
    path: list[str] = []
    cycles: list[list[str]] = []

    def dfs(node: str) -> None:
        color[node] = _gray
        path.append(node)
        for neighbor in adjacency[node]:
            if neighbor not in color:
                continue
            if color[neighbor] == _gray:
                # Found a cycle: extract it from path
                cycle_start = path.index(neighbor)
                cycles.append(path[cycle_start:] + [neighbor])
            elif color[neighbor] == _white:
                dfs(neighbor)
        path.pop()
        color[node] = _black

    for node in adjacency:
        if color[node] == _white:
            dfs(node)

    return cycles


_PRIORITY_RE = re.compile(r"T-P(\d+)-\d+")


def extract_priority(local_task_id: str) -> int:
    """Extract numeric priority from a task ID like 'T-P0-42'.

    Also handles global IDs like 'proj:T-P0-42' by searching
    anywhere in the string.

    Args:
        local_task_id: Task ID in format ``T-P{digit}-{number}``
            or ``project:T-P{digit}-{number}``.

    Returns:
        Priority as integer (lower = higher priority), or 99 if
        the ID doesn't match the expected format.
    """
    m = _PRIORITY_RE.search(local_task_id)
    return int(m.group(1)) if m else 99
