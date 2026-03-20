# /cross-project-sync -- Multi-Repo Sync & Tech Debt Audit

Sync lessons, hooks, settings, and CLAUDE.md improvements across all managed projects,
then review each repo for errors and tech debt. Creates tasks via task_db.py -- does NOT
execute any fixes.

## Prerequisites

- All projects listed in `orchestrator_config.yaml` must be accessible locally
- Each project must have `.claude/hooks/task_db.py` and a `CLAUDE.md`
- Run from the **helixos** project (primary orchestration repo)

## Usage

```
/cross-project-sync                  # Full sync + audit
/cross-project-sync --sync-only      # Only sync, skip audit
/cross-project-sync --audit-only     # Only audit, skip sync
```

## Steps

### Step 0: Activate Plan Mode

```bash
python .claude/hooks/plan_mode.py activate
```

This skill is **plan-only**. All findings become tasks via `task_db.py`. No code changes.

### Step 1: Check Activity (gate)

For each project in `orchestrator_config.yaml`, run:

```bash
git -C <repo_path> log --since="24 hours ago" --oneline
```

**Skip** any project with zero commits in the last 24 hours. Print the skip reason.
If ALL projects are skipped, print "[SYNC] No projects had recent commits. Nothing to do." and stop.

### Step 2: Collect Lessons (per active project)

For each active project, read:
- `LESSONS.md` -- extract entries by date header `### [YYYY-MM-DD]`
- `CLAUDE.md` -- note any project-specific rules that could be universal
- `.claude/hooks/` -- list hook files, compare against template inventory
- `.claude/settings.json` -- compare hook entries against template

Build a **diff report** for each project:
```
=== <project> ===
[LESSONS] New since last sync: <count>
  - <date>: <title> (tags: ...)
[HOOKS] Missing from template: <list>
[HOOKS] Template missing from project: <list>
[SETTINGS] Divergences: <list>
[CLAUDE.MD] Project-specific rules worth promoting: <list>
```

### Step 3: Identify Sync Actions

Compare each project's harness files against the **template** (`claude-code-project-template`):

| File | Sync direction | Check |
|------|---------------|-------|
| `LESSONS.md` | project -> template (new entries only) | Match by title, skip duplicates |
| `.claude/hooks/*.py` | template -> project (missing hooks) | File existence check |
| `.claude/hooks/lint_check.py` | template -> project (if template is newer) | Compare `_CODE_EXTENSIONS`, `scan_emoji` signature |
| `.claude/settings.json` | template -> project (missing hook entries) | JSON structure diff |
| `CLAUDE.md` sections | bidirectional | Diff shared sections only |
| `.claude/skills/*/SKILL.md` | template -> project (missing skills) | Directory listing |
| `scripts/check.sh` | template -> project (if missing) | File existence |

For each action, note:
- Source file path
- Target file path
- What specifically needs to change (add entry, copy file, update section)

### Step 4: Code Review & Tech Debt Audit (per active project)

For each project with recent commits, run these checks:

**4a. Lint**
```bash
cd <repo_path> && ruff check src/ tests/ 2>&1
```
Record violations (if any).

**4b. Unused imports / dead code**
```bash
cd <repo_path> && ruff check --select F401,F841 src/ tests/ 2>&1
```

**4c. Hook health**
For each `.py` file in `.claude/hooks/`:
- Verify it imports from `hook_utils` (not bare `json.load(sys.stdin)`)
- Verify it uses `encoding="utf-8"` on file I/O
- Check for `check_stop_cache` / `write_stop_cache` (should be removed per latest pattern)

**4d. CLAUDE.md staleness**
- Check if CLAUDE.md references files/functions that no longer exist (grep for backtick-quoted paths)
- Check for duplicate sections (known issue: "Key Constraints" appearing twice)

**4e. Git hygiene**
```bash
cd <repo_path> && git remote -v
```
- Flag if remote points to wrong repo (e.g., homestead -> template)
- Flag untracked sensitive files (`.env`, `*.pem`, `settings.local.json`)

**4f. Dependency check** (Python projects only)
- Compare `requirements.txt` pins (exact `==` vs loose `>=`)
- Flag any dependency in `pyproject.toml` not in `requirements.txt` (or vice versa)

### Step 5: Generate Task Specs

For every finding from Steps 2-4, write a task spec:

**Sync tasks** (one per project that needs updates):
```
Title: [SYNC] Propagate <N> improvements to <project>
Priority: P2
Complexity: S
Description:
  ## Summary
  Sync harness improvements from template/other projects.
  ## Items
  - [ ] Copy <hook> from template
  - [ ] Add <lesson> to LESSONS.md
  - [ ] Update <section> in CLAUDE.md
  ## Acceptance Criteria
  - All items checked off
  - `python .claude/hooks/plan_mode_hook.py < /dev/null` exits 0
  - `python -c "import json; json.load(open('.claude/settings.json'))"` passes
```

**Tech debt tasks** (grouped by project, one task per category):
```
Title: [DEBT] <project>: Fix <category> issues (<count> items)
Priority: P2 (P1 if lint errors or broken hooks)
Complexity: S/M depending on count
Description:
  ## Summary
  Address <category> findings from cross-project audit.
  ## Items
  - [ ] <specific fix 1>
  - [ ] <specific fix 2>
  ## Acceptance Criteria
  - ruff check clean
  - All hooks exit 0 on empty stdin
```

### Step 6: Write Tasks to DB

Use `task_db.py` to create all tasks. For multiple tasks, use batch:

```bash
python .claude/hooks/task_db.py batch --commands '[
  {"cmd": "add", "title": "[SYNC] ...", "priority": "P2", "complexity": "S", "description": "..."},
  {"cmd": "add", "title": "[DEBT] ...", "priority": "P2", "complexity": "M", "description": "..."}
]'
```

### Step 7: Validate and Deactivate

```bash
python .claude/hooks/plan_validate.py
python .claude/hooks/plan_mode.py deactivate
```

Print final summary table:

```
## Cross-Project Sync Summary

| Project | Recent Commits | Sync Items | Debt Items | Tasks Created |
|---------|---------------|------------|------------|---------------|
| helixos | 3 | 0 | 2 | 1 |
| blog    | 1 | 3 | 0 | 1 |
| ...     | ... | ... | ... | ... |

Total tasks created: N
Next step: Run the sync tasks (P2 priority) in each project.
```

## Anti-patterns (DO NOT do these)

- Writing or editing source code files
- Copying files between projects (only plan what to copy)
- Running `ruff --fix` or auto-fixing anything
- Modifying CLAUDE.md, LESSONS.md, settings.json, or any hook
- Creating or deleting files in any project
- Executing any sync action -- only plan and create tasks
- Committing or pushing in any project

## Decision Rules

- **Lesson is universal** if its tags include: #hooks, #lint, #ruff, #exit-protocol, #cache,
  #sqlalchemy, #migration, #task-db, #batch, #validation, #windows, #utf-8, #ci, #testing,
  #mocking, #security, #gitignore
- **Lesson is project-specific** if its tags include: #tailwind, #scraper, #hexo, #theme,
  #react (keep in source project only)
- **Hook divergence is OK** if the project has a CUSTOMIZE comment explaining why
- **CLAUDE.md section is promotable** if it applies to >1 project and is not already in template
