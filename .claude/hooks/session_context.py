"""SessionStart hook: output project context summary at session start."""
import contextlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402


def _find_project_root() -> Path:
    """Find the project root by looking for CLAUDE.md."""
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent.parent,  # .claude/hooks/ -> project root
    ]
    for candidate in candidates:
        if (candidate / "CLAUDE.md").exists():
            return candidate
    return Path.cwd()


def _get_current_task_id(root: Path) -> str | None:
    """Read current task ID from session_state.json."""
    state_file = root / ".claude" / "session_state.json"
    if not state_file.exists():
        return None
    try:
        content = state_file.read_text(encoding="utf-8")
        state = json.loads(content)
        return state.get("current_task")
    except (json.JSONDecodeError, OSError):
        return None


def _get_last_progress_entries(root: Path, count: int = 1) -> str:
    """Extract the last N entries from PROGRESS.md."""
    progress_file = root / "PROGRESS.md"
    if not progress_file.exists():
        return "No PROGRESS.md found."

    content = progress_file.read_text(encoding="utf-8")
    entries = re.split(r"(?=^## \d{4}-\d{2}-\d{2})", content, flags=re.MULTILINE)
    real_entries = [e.strip() for e in entries if re.match(r"## \d{4}-\d{2}-\d{2}", e.strip())]

    if not real_entries:
        return "No progress entries yet."

    recent = real_entries[-count:]
    return "\n\n".join(recent)


def _summarize_task_oneline(task_block: str) -> str:
    """Convert a full task block into a one-line summary.

    Input: multi-line task block starting with '#### T-XX-N: Title'
    Output: 'T-XX-N: Title [P1, M, blocked by T-XX-N]' or similar
    """
    lines = task_block.strip().splitlines()
    if not lines:
        return ""

    # Extract title from #### header
    title_match = re.match(r"####\s+(T-\S+:\s+.+)", lines[0])
    if not title_match:
        return lines[0].lstrip("# ").strip()
    title = title_match.group(1).strip()

    # Extract metadata
    priority = ""
    complexity = ""
    depends = ""
    needs_input = ""
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith("- **Priority**:"):
            priority = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("- **Complexity**:"):
            complexity = stripped.split(":", 1)[1].strip().split("(")[0].strip()
        elif stripped.startswith("- **Depends on**:"):
            depends = stripped.split(":", 1)[1].strip()
        elif "[NEEDS-INPUT" in stripped:
            needs_input_match = re.search(r"\[NEEDS-INPUT[^\]]*\]", stripped)
            if needs_input_match:
                needs_input = needs_input_match.group(0)

    parts = [title]
    meta = []
    if priority:
        meta.append(priority)
    if complexity:
        meta.append(complexity)
    if depends:
        meta.append(f"depends: {depends}")
    if needs_input:
        meta.append(needs_input)
    if meta:
        parts.append(f"[{', '.join(meta)}]")

    return " ".join(parts)


def _get_active_tasks(root: Path, current_task_id: str | None) -> str:
    """Extract tasks from TASKS.md with two-tier detail.

    Current task (from session_state.json): FULL details.
    All other tasks: ONE LINE each.
    """
    tasks_file = root / "TASKS.md"
    if not tasks_file.exists():
        return "No TASKS.md found."

    content = tasks_file.read_text(encoding="utf-8")
    sections: list[str] = []

    # Extract section content for In Progress, Active Tasks, Blocked
    for section_name in ["In Progress", "Active Tasks", "Blocked"]:
        section_match = re.search(
            rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL
        )
        if not section_match:
            continue
        text = section_match.group(1).strip()
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
        if not text:
            continue

        # Split into individual task blocks (#### headers)
        task_blocks = re.split(r"(?=^#### )", text, flags=re.MULTILINE)
        task_blocks = [b.strip() for b in task_blocks if b.strip()]

        if not task_blocks:
            # No #### headers -- might be plain text
            sections.append(f"**{section_name}:**\n{text}")
            continue

        task_lines: list[str] = []
        for block in task_blocks:
            # Check if this is the current task
            task_id_match = re.match(r"####\s+(T-\S+):", block)
            task_id = task_id_match.group(1) if task_id_match else None

            if current_task_id and task_id == current_task_id:
                # FULL details for current task
                task_lines.append(f"[CURRENT TASK]\n{block}")
            else:
                # ONE LINE summary for other tasks
                summary = _summarize_task_oneline(block)
                if summary:
                    task_lines.append(summary)

        if task_lines:
            sections.append(f"**{section_name}:**\n" + "\n".join(task_lines))

    return "\n\n".join(sections) if sections else "No active tasks."


def _get_recent_lessons(root: Path, current_task_id: str | None) -> str:
    """Extract relevant lessons from LESSONS.md.

    Uses keyword-based filtering: extracts keywords from the current task title
    and matches against lesson tags. Falls back to last 3 if no matches.
    """
    lessons_file = root / "LESSONS.md"
    if not lessons_file.exists():
        return "No LESSONS.md found."

    content = lessons_file.read_text(encoding="utf-8")
    entries = re.split(r"(?=^### \[\d{4}-\d{2}-\d{2}\])", content, flags=re.MULTILINE)
    real_entries = [e.strip() for e in entries if re.match(r"### \[\d{4}-\d{2}-\d{2}\]", e.strip())]

    if not real_entries:
        return "No lessons logged yet."

    # If we know the current task, try keyword-based filtering
    if current_task_id:
        tasks_file = root / "TASKS.md"
        if tasks_file.exists():
            tasks_content = tasks_file.read_text(encoding="utf-8")
            # Find the task block for the current task
            task_match = re.search(
                rf"####\s+{re.escape(current_task_id)}:\s*(.+)",
                tasks_content,
            )
            if task_match:
                title = task_match.group(1).lower()
                # Extract meaningful words as keywords (skip common words)
                skip_words = {"the", "a", "an", "and", "or", "for", "with", "in", "on", "to", "of"}
                keywords = [
                    w for w in re.findall(r"[a-z]{3,}", title)
                    if w not in skip_words
                ]
                if keywords:
                    # Filter entries that mention any keyword in tags or title
                    matched = [
                        e for e in real_entries
                        if any(kw in e.lower() for kw in keywords)
                    ]
                    if matched:
                        return "\n\n".join(matched[-3:])

    # Fall back to last 3 entries
    recent = real_entries[-3:]
    return "\n\n".join(recent)


def _get_human_input_status(root: Path) -> str:
    """Parse docs/human_input/README.md for human input task status.

    Returns a formatted [INPUT] section or empty string if no README exists.
    """
    readme = root / "docs" / "human_input" / "README.md"
    if not readme.exists():
        return ""

    try:
        content = readme.read_text(encoding="utf-8")
    except OSError:
        return ""

    # Parse ## T-P*: headers and **Status**: lines
    task_pattern = re.compile(r"^## (T-P\d+-\S+):\s*(.+)", re.MULTILINE)
    status_pattern = re.compile(r"\*\*Status\*\*:\s*\[([x ])\]", re.IGNORECASE)

    items: list[str] = []
    for task_match in task_pattern.finditer(content):
        task_id = task_match.group(1)
        task_desc = task_match.group(2).strip()
        # Find the next **Status** line after this header
        after_header = content[task_match.end():]
        status_match = status_pattern.search(after_header)
        if not status_match:
            items.append(f"{task_id} ({task_desc}: unknown)")
            continue

        is_complete = status_match.group(1).lower() == "x"
        if is_complete:
            items.append(f"{task_id} (complete -- run /collect-input validate {task_id})")
        else:
            # Check for "Blocked" or dependency notes
            note_area = after_header[:500]
            if "blocked" in note_area.lower() or "depends" in note_area.lower():
                items.append(f"{task_id} (blocked)")
            else:
                items.append(f"{task_id} (not started)")

    if not items:
        return ""

    return "[INPUT] Human input needed: " + ", ".join(items)


def _get_checkpoint(root: Path) -> str:
    """Read .claude/checkpoint.json and format progress summary.

    Returns a formatted [CHECKPOINT] section or empty string if no checkpoint exists.
    """
    checkpoint_file = root / ".claude" / "checkpoint.json"
    if not checkpoint_file.exists():
        return ""

    try:
        content = checkpoint_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except (json.JSONDecodeError, OSError):
        return ""

    task = data.get("task", "unknown")
    subtasks = data.get("subtasks", [])
    if not subtasks:
        return ""

    done = sum(1 for s in subtasks if s.get("done"))
    total = len(subtasks)
    # Find next incomplete subtask
    next_task = next((s.get("name", "?") for s in subtasks if not s.get("done")), None)

    parts = [f"[CHECKPOINT] {task}: {done}/{total} subtasks done."]
    if next_task:
        parts.append(f"Next: {next_task}")

    last_file = data.get("last_working_file")
    if last_file:
        line = data.get("last_working_line")
        loc = f"{last_file}:{line}" if line else last_file
        parts.append(f"Last working: {loc}")

    return " ".join(parts)


def _get_autonomous_state(root: Path) -> tuple[str, str]:
    """Read .claude/session_state.json and format autonomous mode status.

    Returns:
        Tuple of (status_line, rules_block). status_line is the one-line summary.
        rules_block contains the autonomous mode rules (from docs/workflow/autonomous.md)
        or empty string if not in autonomous mode.
    """
    state_file = root / ".claude" / "session_state.json"
    if not state_file.exists():
        return "", ""

    try:
        content = state_file.read_text(encoding="utf-8")
        state = json.loads(content)
    except (json.JSONDecodeError, OSError):
        return "", ""

    if state.get("mode") != "autonomous":
        return "", ""

    parts = ["[AUTONOMOUS] Mode active."]

    completed = state.get("completed_this_session", [])
    if completed:
        parts.append(f"Completed: {', '.join(completed)}.")

    current = state.get("current_task")
    retry = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)
    if current:
        parts.append(f"Current: {current} (attempt {retry + 1}/{max_retries}).")

    skipped = state.get("skipped_tasks", [])
    if skipped:
        skip_strs = [f"{s['task']} ({s.get('reason', 'unknown')})" for s in skipped]
        parts.append(f"Skipped: {', '.join(skip_strs)}.")

    if state.get("all_done"):
        parts.append("All tasks complete.")

    status_line = " ".join(parts)

    # Load autonomous mode rules from docs/workflow/autonomous.md
    rules_file = root / "docs" / "workflow" / "autonomous.md"
    rules_block = ""
    if rules_file.exists():
        with contextlib.suppress(OSError):
            rules_block = rules_file.read_text(encoding="utf-8").strip()

    return status_line, rules_block


def main(hook_input: dict) -> None:
    """Output project context for session startup."""
    root = _find_project_root()

    current_task_id = _get_current_task_id(root)
    autonomous_status, autonomous_rules = _get_autonomous_state(root)
    progress = _get_last_progress_entries(root, count=1)
    tasks = _get_active_tasks(root, current_task_id)
    checkpoint = _get_checkpoint(root)
    human_input = _get_human_input_status(root)
    lessons = _get_recent_lessons(root, current_task_id)

    sections = ["=== SESSION CONTEXT ==="]
    if autonomous_status:
        sections.append("")
        sections.append(autonomous_status)
    sections.append("")
    sections.append(f"[PROGRESS] RECENT PROGRESS:\n{progress}")
    sections.append("")
    sections.append(f"[TASKS] CURRENT TASKS:\n{tasks}")
    if checkpoint:
        sections.append("")
        sections.append(checkpoint)
    if human_input:
        sections.append("")
        sections.append(human_input)
    sections.append("")
    sections.append(f"[TIP] RECENT LESSONS:\n{lessons}")
    if autonomous_rules:
        sections.append("")
        sections.append(f"[AUTONOMOUS RULES]\n{autonomous_rules}")
    sections.append("")
    sections.append("=== END CONTEXT ===")
    output = "\n".join(sections)

    # Output to stdout so Claude sees it
    print(output)
    sys.exit(0)


if __name__ == "__main__":
    run_hook("session_context", main)
