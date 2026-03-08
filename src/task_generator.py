"""Deterministic Task Generator -- proposal-to-TASKS.md pipeline.

Processes ``proposed_tasks[]`` from a plan result into fully-formed
TASKS.md entries with auto-allocated IDs, dependency validation,
cycle detection, and diff generation for human approval.

Pure Python -- no LLM calls.  Human-in-the-loop is mandatory:
a diff is generated for review before any write occurs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.dependency_graph import detect_cycles
from src.enrichment import MAX_TASKS_PER_PLAN, ProposedTask
from src.tasks_writer import TASK_ID_RE, TasksWriter, generate_next_task_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AllocatedTask:
    """A proposed task with an allocated ID, ready for TASKS.md insertion."""

    task_id: str
    title: str
    description: str
    priority: str
    complexity: str
    depends_on: list[str]
    acceptance_criteria: list[str]
    parent_task_id: str


@dataclass
class GeneratorResult:
    """Result of processing proposed tasks."""

    success: bool
    allocated_tasks: list[AllocatedTask] = field(default_factory=list)
    diff_text: str = ""
    error: str | None = None


@dataclass
class WriteAllResult:
    """Result of writing all allocated tasks to TASKS.md."""

    success: bool
    written_ids: list[str] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_proposals(
    proposals: list[ProposedTask],
) -> str | None:
    """Validate a list of proposals.  Returns error string or None."""
    if len(proposals) > MAX_TASKS_PER_PLAN:
        return (
            f"Too many proposed tasks: {len(proposals)} "
            f"(max {MAX_TASKS_PER_PLAN})"
        )

    for i, p in enumerate(proposals):
        if not p.title.strip():
            return f"Proposed task [{i}] has empty title"
        if not p.description.strip():
            return f"Proposed task [{i}] ({p.title!r}) has empty description"

    return None


def _resolve_dependencies(
    proposals: list[ProposedTask],
    existing_ids: set[str],
    title_to_id: dict[str, str],
) -> tuple[list[list[str]], str | None]:
    """Resolve dependency references for each proposal.

    Dependencies can reference:
    - Existing task IDs (e.g. "T-P0-42") -- must exist in existing_ids
    - Other proposal titles -- resolved via title_to_id mapping

    Returns (resolved_deps_per_proposal, error_or_none).
    """
    resolved: list[list[str]] = []

    for p in proposals:
        task_deps: list[str] = []
        for dep in p.dependencies:
            dep_stripped = dep.strip()
            if not dep_stripped:
                continue

            # Check if it's an existing task ID reference
            if TASK_ID_RE.fullmatch(dep_stripped):
                if dep_stripped not in existing_ids:
                    return [], (
                        f"Proposed task {p.title!r}: dependency "
                        f"{dep_stripped!r} references non-existent task"
                    )
                task_deps.append(dep_stripped)
            elif dep_stripped in title_to_id:
                # Reference to another proposed task by title
                task_deps.append(title_to_id[dep_stripped])
            else:
                return [], (
                    f"Proposed task {p.title!r}: dependency "
                    f"{dep_stripped!r} is neither a valid task ID "
                    f"nor a title of another proposed task"
                )
        resolved.append(task_deps)

    return resolved, None


def _detect_cycles_in_allocated(
    allocated: list[AllocatedTask],
) -> str | None:
    """Detect dependency cycles among allocated tasks.

    Delegates to the shared ``detect_cycles()`` from ``dependency_graph``.

    Returns error string describing the cycle, or None.
    """
    id_set = {t.task_id for t in allocated}
    adj: dict[str, list[str]] = {t.task_id: [] for t in allocated}
    for t in allocated:
        for dep in t.depends_on:
            if dep in id_set:
                adj[t.task_id].append(dep)

    cycles = detect_cycles(adj)
    if cycles:
        cycle_str = " -> ".join(cycles[0])
        return f"Circular dependency detected: {cycle_str}"

    return None


# ---------------------------------------------------------------------------
# ID allocation
# ---------------------------------------------------------------------------


def _allocate_ids(
    proposals: list[ProposedTask],
    tasks_md_content: str,
) -> tuple[list[str], dict[str, str]]:
    """Allocate sequential task IDs for each proposal.

    Returns (list_of_ids, title_to_id_mapping).
    """
    # Track content as we allocate to avoid collisions
    content = tasks_md_content
    allocated_ids: list[str] = []
    title_to_id: dict[str, str] = {}

    for p in proposals:
        priority = p.suggested_priority
        task_id = generate_next_task_id(content, priority)
        allocated_ids.append(task_id)
        title_to_id[p.title] = task_id
        # Add the new ID to content so next allocation sees it
        content += f"\n{task_id}"

    return allocated_ids, title_to_id


# ---------------------------------------------------------------------------
# Task block formatting
# ---------------------------------------------------------------------------


def _build_full_task_block(task: AllocatedTask) -> str:
    """Build a full TASKS.md task block with all metadata fields.

    Follows the project's task schema template:
    ```
    #### T-PX-NN: Title
    - **Priority**: P0 | P1 | P2 | P3
    - **Complexity**: S (< 1 session) | M (1-2 sessions) | L (3+ sessions)
    - **Depends on**: T-XX-NN | None
    - **Description**: What and why
    - **Acceptance Criteria**:
      1. ...
    ```
    """
    complexity_labels = {
        "S": "S (< 1 session)",
        "M": "M (1-2 sessions)",
        "L": "L (3+ sessions)",
    }
    complexity_display = complexity_labels.get(task.complexity, task.complexity)

    deps_display = ", ".join(task.depends_on) if task.depends_on else "None"

    lines = [
        f"#### {task.task_id}: {task.title}",
        f"- **Priority**: {task.priority}",
        f"- **Complexity**: {complexity_display}",
        f"- **Depends on**: {deps_display}",
        f"- **Description**: {task.description}",
    ]

    if task.acceptance_criteria:
        lines.append("- **Acceptance Criteria**:")
        for i, ac in enumerate(task.acceptance_criteria, 1):
            lines.append(f"  {i}. {ac}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Diff generation
# ---------------------------------------------------------------------------


def _generate_diff(
    allocated_tasks: list[AllocatedTask],
    parent_task_id: str,
) -> str:
    """Generate a human-readable diff showing what will be added.

    Returns a text summary suitable for display before approval.
    """
    lines: list[str] = []
    lines.append(f"=== Task Generator: {len(allocated_tasks)} tasks from {parent_task_id} ===")
    lines.append("")

    for task in allocated_tasks:
        block = _build_full_task_block(task)
        for line in block.rstrip("\n").split("\n"):
            lines.append(f"+ {line}")
        lines.append("")

    # Dependency summary
    dep_entries = [
        f"  {t.task_id} depends on {', '.join(t.depends_on) if t.depends_on else 'None'}"
        for t in allocated_tasks
    ]
    lines.append("Dependency graph additions:")
    lines.extend(dep_entries)
    lines.append("")
    lines.append(f"Total: {len(allocated_tasks)} new tasks to insert into Active Tasks section")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def _scan_existing_task_ids(content: str) -> set[str]:
    """Scan TASKS.md content for all existing task IDs."""
    return {m.group(0) for m in TASK_ID_RE.finditer(content)}


def process_proposals(
    proposals: list[ProposedTask],
    tasks_md_content: str,
    parent_task_id: str,
) -> GeneratorResult:
    """Process proposed tasks into allocated tasks with validation.

    This is the main entry point for the task generator pipeline.
    It performs:
    1. Schema validation (required fields)
    2. Count enforcement (max 8)
    3. ID allocation
    4. Dependency resolution
    5. Cycle detection
    6. Diff generation

    Args:
        proposals: List of ProposedTask from plan output.
        tasks_md_content: Current TASKS.md content for ID allocation.
        parent_task_id: The task ID that generated these proposals.

    Returns:
        GeneratorResult with allocated tasks and diff text on success,
        or error message on failure.
    """
    # 1. Validate proposals
    error = _validate_proposals(proposals)
    if error is not None:
        return GeneratorResult(success=False, error=error)

    if not proposals:
        return GeneratorResult(success=True, allocated_tasks=[], diff_text="")

    # 2. Allocate IDs
    allocated_ids, title_to_id = _allocate_ids(proposals, tasks_md_content)

    # 3. Resolve dependencies
    existing_ids = _scan_existing_task_ids(tasks_md_content)
    # Also include newly allocated IDs as valid targets
    all_valid_ids = existing_ids | set(allocated_ids)
    resolved_deps, dep_error = _resolve_dependencies(
        proposals, all_valid_ids, title_to_id,
    )
    if dep_error is not None:
        return GeneratorResult(success=False, error=dep_error)

    # 4. Build allocated tasks
    allocated_tasks: list[AllocatedTask] = []
    for i, proposal in enumerate(proposals):
        allocated_tasks.append(AllocatedTask(
            task_id=allocated_ids[i],
            title=proposal.title,
            description=proposal.description,
            priority=proposal.suggested_priority,
            complexity=proposal.suggested_complexity,
            depends_on=resolved_deps[i],
            acceptance_criteria=proposal.acceptance_criteria,
            parent_task_id=parent_task_id,
        ))

    # 5. Cycle detection
    cycle_error = _detect_cycles_in_allocated(allocated_tasks)
    if cycle_error is not None:
        return GeneratorResult(success=False, error=cycle_error)

    # 6. Generate diff
    diff_text = _generate_diff(allocated_tasks, parent_task_id)

    return GeneratorResult(
        success=True,
        allocated_tasks=allocated_tasks,
        diff_text=diff_text,
    )


def extract_proposals_from_plan(plan_json: str | None) -> list[ProposedTask]:
    """Extract proposed tasks from a plan_json string.

    Args:
        plan_json: JSON string of the plan result, or None.

    Returns:
        List of ProposedTask objects (may be empty).
    """
    if not plan_json:
        return []

    try:
        data = json.loads(plan_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse plan_json for proposal extraction")
        return []

    raw_proposals = data.get("proposed_tasks", [])
    if not raw_proposals:
        return []

    proposals: list[ProposedTask] = []
    for raw in raw_proposals:
        try:
            proposals.append(ProposedTask.model_validate(raw))
        except Exception as exc:
            logger.warning("Skipping invalid proposed task: %s", exc)

    return proposals


# ---------------------------------------------------------------------------
# TASKS.md writer
# ---------------------------------------------------------------------------


def write_allocated_tasks(
    writer: TasksWriter,
    allocated_tasks: list[AllocatedTask],
) -> WriteAllResult:
    """Write allocated tasks to TASKS.md via the TasksWriter.

    Uses the writer's locking and backup mechanisms.  Each task
    is inserted as a full task block in the Active Tasks section.

    Args:
        writer: TasksWriter instance for the target TASKS.md.
        allocated_tasks: Tasks to insert (already validated).

    Returns:
        WriteAllResult with list of written IDs on success.
    """
    if not allocated_tasks:
        return WriteAllResult(success=True)

    # Read current content once (we'll do the writes atomically)
    path = writer.path
    if not path.is_file():
        return WriteAllResult(
            success=False,
            error=f"TASKS.md not found at {path}",
        )

    # Build all task blocks and insert them in one write
    # (avoid multiple read-write cycles with potential ID collision)
    try:
        _write_tasks_atomic(writer, allocated_tasks)
    except Exception as exc:
        return WriteAllResult(
            success=False,
            error=f"Failed to write tasks: {exc}",
        )

    return WriteAllResult(
        success=True,
        written_ids=[t.task_id for t in allocated_tasks],
    )


def _write_tasks_atomic(
    writer: TasksWriter,
    allocated_tasks: list[AllocatedTask],
) -> None:
    """Write all tasks in a single atomic file operation.

    Uses the writer's internal locks for safety.
    """
    import shutil

    from src.tasks_writer import _find_active_section_end, _validate_written_file

    path = writer.path

    with writer._thread_lock, writer._file_lock:  # noqa: SLF001
        content = path.read_text(encoding="utf-8")

        insert_line = _find_active_section_end(content)
        if insert_line is None:
            content = content.rstrip("\n") + "\n\n## Active Tasks\n\n"
            insert_line = len(content.split("\n"))

        # Build all blocks
        all_blocks: list[str] = []
        for task in allocated_tasks:
            block = _build_full_task_block(task)
            all_blocks.append(block)

        # Insert all blocks at once
        lines = content.split("\n")
        insert_lines: list[str] = []
        for block in all_blocks:
            insert_lines.extend(block.rstrip("\n").split("\n"))
            insert_lines.append("")  # blank line separator

        lines[insert_line:insert_line] = insert_lines
        new_content = "\n".join(lines)

        # Create backup
        bak_path = path.with_suffix(".md.bak")
        if path.is_file():
            shutil.copy2(str(path), str(bak_path))

        # Write
        path.write_text(new_content, encoding="utf-8")

        # Validate -- check all IDs are present
        for task in allocated_tasks:
            error = _validate_written_file(path, task.task_id)
            if error is not None:
                # Restore backup
                shutil.copy2(str(bak_path), str(path))
                msg = f"Post-write validation failed for {task.task_id}: {error}"
                raise RuntimeError(msg)

        logger.info(
            "Wrote %d tasks to %s: %s",
            len(allocated_tasks),
            path,
            [t.task_id for t in allocated_tasks],
        )
