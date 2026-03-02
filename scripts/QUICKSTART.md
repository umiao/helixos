# Quick Start Guide

## Prerequisites

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed
- Git repository initialized

## Setup

### 1. Clone this template

```bash
# Option A: Use as GitHub template (click "Use this template" on GitHub)
# Option B: Clone directly
git clone <template-url> my-project
cd my-project
```

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### 3. Customize CLAUDE.md

Open `CLAUDE.md` and update all `<!-- CUSTOMIZE -->` sections:
- Project overview and tech stack
- File structure description
- Project-specific invariants
- Prohibited actions

### 4. Set up your task backlog

Edit `TASKS.md` to replace the example tasks with your actual tasks.
Follow the existing format for priorities, complexity, and acceptance criteria.

### 5. Customize hooks (optional)

- **`file_watch_warn.py`**: Update `WATCHED_PATHS` with your critical file paths
- **`lint_check.py`**: Update `LINT_COMMAND` and `LINT_PATHS` if not using ruff
- **`test_check.py`**: Update `TEST_COMMAND` and `TEST_PATHS` if not using pytest
- **`input-reviewer.md`**: Add task-specific validation checks

### 6. Verify hooks work

```bash
# Start a Claude Code session -- the SessionStart hook should fire
claude

# Inside the session, try /sanity-check to verify all hooks work
```

## Usage

### Interactive mode (default)

```bash
claude
```

The SessionStart hook will display your task status, recent progress, and relevant
lessons. Work on tasks, and the Stop hooks will enforce quality gates when you finish.

### Autonomous mode

```bash
bash scripts/autonomous_run.sh 10  # Run up to 10 sessions
```

Each session picks up one task, completes it, commits, and stops. The orchestrator
launches fresh sessions until all tasks are done or the limit is reached.

### Common slash commands

| Command | Purpose |
|---------|---------|
| `/sanity-check` | Run exit gate checks without stopping |
| `/review` | Code review against CLAUDE.md invariants |
| `/e2e-test` | Run full test suite with analysis |
| `/improve` | Scan for quality improvements |
| `/collect-input status` | Check human input task status |

## File Structure

```
your-project/
  CLAUDE.md              # Project rules (customize this first)
  TASKS.md               # Task backlog (add your tasks here)
  PROGRESS.md            # Session log (auto-populated)
  LESSONS.md             # Knowledge base (auto-populated)
  .claude/
    settings.json        # Hook wiring (works out of the box)
    settings.local.json  # Permission allowlist for autonomous mode
    hooks/               # Quality enforcement scripts
    agents/              # Sub-agent definitions
    skills/              # Slash command definitions
  docs/
    workflow/            # Autonomous mode + exit protocol docs
    human_input/         # Human input collection specs
  scripts/
    autonomous_run.sh    # Autonomous orchestrator
```

## Permissions

The `settings.local.json` file pre-approves common safe operations (python, git,
ruff, pytest, etc.) to reduce permission prompts during autonomous mode. Review
and adjust the allowlist for your needs.

## Troubleshooting

### Hooks not firing
- Verify `.claude/settings.json` exists and has the correct hook paths
- Check that Python scripts have correct shebang / are executable
- Run a hook manually: `echo '{}' | python .claude/hooks/lint_check.py`

### Stop hook blocks unexpectedly
- The prompt-based stop hook evaluates 5 rules (work completeness, sanity check,
  PROGRESS.md, TASKS.md, LESSONS.md). Check which rule is blocking.
- Run `/sanity-check` to see the current state before trying to stop.

### Encoding errors on Windows
- All hooks use UTF-8 via `hook_utils.py`. If you add custom hooks, always use
  `encoding="utf-8"` in file operations and subprocess calls.
