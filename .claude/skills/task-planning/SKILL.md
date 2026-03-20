# /task-planning -- Enforced Task Planning Mode

Decompose work into well-specified tasks with hard enforcement against code execution.

## Usage

```
/task-planning                  # Start a planning session
/task-planning <scope>          # Plan tasks for a specific area
```

## Steps

### Step 0: Activate Plan Mode

Run immediately -- this enables the PreToolUse hook that blocks mutating tools:

```bash
python .claude/hooks/plan_mode.py activate
```

Confirm activation succeeded before proceeding. If it fails, stop and report.

### Step 1: Understand Scope

- Ask the user what area/feature needs planning (if not already specified)
- Read relevant source files to understand the current state
- Read TASKS.md to understand existing tasks and avoid duplication
- Read PROGRESS.md for recent context
- Ask clarifying questions if the scope is ambiguous

**Do NOT write any code or modify any files. Only read and discuss.**

### Step 2: Decompose into Tasks

For each task, write a full spec using this template:

```
## Summary
One-sentence description of what this task delivers.

## Context
Why this task exists. What user-facing or system problem it solves.
Reference related tasks if applicable.

## Acceptance Criteria
- [ ] AC1: Specific, testable condition
- [ ] AC2: Include at least one full user journey AC
- [ ] AC3: For conditional behavior, specify BOTH branches (if X then Y, else Z)
- [ ] AC4: For UX tasks, include manual smoke test AC

## Technical Approach
- Implementation strategy (which files, what changes)
- Key design decisions and trade-offs
- Integration points with existing code

## Edge Cases
- What could go wrong
- Platform-specific concerns (Windows/Unix)
- Error handling requirements

## Complexity
S / M / L -- with brief justification

## Dependencies
- List task IDs this depends on, or "None"
```

### Step 3: Preview (for 5+ tasks)

If decomposition produces 5 or more tasks, present a summary table before writing:

```
| # | Title | Priority | Complexity | Dependencies |
|---|-------|----------|------------|--------------|
| 1 | ...   | P0       | M          | None         |
```

Ask the user to confirm or adjust before proceeding to Step 4.

### Step 4: Write to DB

Use `task_db.py` to create tasks. For multiple tasks, use batch mode:

```bash
python .claude/hooks/task_db.py batch --commands '[
  {"cmd": "add", "title": "...", "priority": "P0", "complexity": "M", "description": "..."},
  {"cmd": "add", "title": "...", "priority": "P1", "complexity": "S", "description": "..."}
]'
```

For single tasks:
```bash
python .claude/hooks/task_db.py add --title "..." --priority P0 --complexity M --description "..."
```

Set dependencies after creation:
```bash
python .claude/hooks/task_db.py depend T-P0-XX --on T-P0-YY
```

### Step 5: Validate

Run the plan validator to check completeness:

```bash
python .claude/hooks/plan_validate.py
```

Fix any failures (missing sections, missing regeneration) before proceeding.

### Step 6: Deactivate and Summarize

```bash
python .claude/hooks/plan_mode.py deactivate
```

Print a summary table of all tasks created/updated, then **STOP**. Do not begin implementation.

## Anti-patterns (DO NOT do these)

- Writing or editing source code files
- Running tests or linters
- Creating implementation files "to test the approach"
- Modifying any file outside of task_db.py operations
- Starting implementation of any planned task
- Using Write/Edit tools on any file
- Running Bash commands that modify files (mkdir, touch, cp, mv, etc.)

## Quality Checklist

Before finishing, verify each task has:
- [ ] A clear Summary that a new developer could understand
- [ ] Context explaining WHY, not just WHAT
- [ ] At least one user-journey AC (User does X -> system does Y -> user sees Z)
- [ ] For conditional ACs: both branches specified (if/else)
- [ ] Technical Approach with specific file paths
- [ ] Edge Cases section (even if brief)
- [ ] Correct Complexity rating with justification
- [ ] Dependencies that match the implementation order
