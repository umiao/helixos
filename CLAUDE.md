<!-- Auto-generated: CLAUDE.md.local + shared. Do not edit directly. -->
# Project Context

## Project Overview
HelixOS is a task management and AI workflow orchestration platform with a FastAPI backend and React frontend.

## Tech Stack
- Python 3.11+
- FastAPI (backend API)
- React + TypeScript (frontend)
- SQLAlchemy (ORM)
- pytest (testing)
- ruff (linting)

## Key Constraints
- **Dependency source-of-truth**: Both `pyproject.toml` `[project].dependencies` and
  `requirements.txt` list dependencies. Keep them in sync manually. When adding a new
  dependency, add it to BOTH files. `pyproject.toml` is the canonical spec;
  `requirements.txt` exists for `pip install -r` convenience.

## File Structure
- `src/` - Source code
- `tests/` - Test files
- `config/` - Configuration files
- `data/` - Runtime data (not in git)

## Invariants (must always hold, violation = bug)
1. .env file never tracked by git
2. No hardcoded secrets in code

## Project-Specific Code Style
- **Windows asyncio subprocess**: Any use of `asyncio.create_subprocess_exec` or
  `create_subprocess_shell` requires `WindowsProactorEventLoopPolicy` at app startup.
  Guard with `sys.platform == "win32"`.
- **Schema changes require migration**: When adding a column to an existing
  SQLAlchemy model, ensure `init_db()` handles the case where the table
  already exists without the new column.  Never assume users will delete
  their database.

## Smoke Test Enforcement

These rules prevent the class of bugs found in T-P0-57/T-P0-59, where UX
tasks were marked DONE with "build succeeds + tests pass" verification, but
three critical bugs (T-P0-66) were found on first real use.

1. **UX DONE gate**: A UX task cannot be marked DONE unless the PROGRESS.md
   entry includes a "Smoke test performed" line describing what was manually
   verified (e.g., "clicked Generate Plan on card -> spinner appeared ->
   plan populated after 30s").  "TypeScript clean, Vite build clean" is
   necessary but NOT sufficient for UX tasks.

2. **Cross-component regression check**: When modifying a component that is
   rendered inside other components (e.g., TaskCard inside KanbanBoard inside
   SwimLane), verify that the change works in ALL rendering contexts, not
   just the one you are focused on.  Common miss: popover works but card
   face does not, or vice versa.

3. **Autonomous mode exception**: In autonomous mode (no browser available),
   UX tasks may substitute a build + TypeScript type-check + grep-based
   wiring verification (e.g., confirm event handler is connected, prop is
   threaded through component tree).  Document what was verified and tag
   with "[AUTO-VERIFIED]" in PROGRESS.md.  These tasks should be flagged
   for human smoke test on next manual session.

## Key Constraints
- All API keys and cookies from .env, never hardcoded
- Every function must have type hints and docstring

## Git Conventions
- **Commit message format**: `[T-XX-N] Brief English description of what was done`
  - Describe the IMPLEMENTATION (what was done), not the task spec verbatim
  - If the task title is in Chinese, translate/summarize to English
  - Use the same brief-title style as PROGRESS.md entries
  - Example: Task "刷新页面后conversation会丢失" -> `[T-P0-165] Recover conversation from plain log after page refresh`
- **Language**: All commit messages in English. No CJK characters.
- **Force-push**: Always use `--force-with-lease`, never `--force`.

## Code Style
- Use ruff for linting
- Type checking: mypy
- Test: pytest
- **Regression tests**: When fixing a bug, always add a regression test
- **No emoji**: Never use emoji characters in code, docs, configs, or hook output.
  Use ASCII text tags (e.g., [DONE], [FAIL], [WARN]) instead.
- **Explicit UTF-8**: All file I/O and subprocess calls must specify `encoding="utf-8"`.
  Never rely on locale defaults (cp1252 on Windows).
- **Windows-compatible docs**: Shell commands in documentation must work on both
  bash and Windows PowerShell 5.x. Use separate lines instead of `&&` chaining.
  For bash-only commands (`source`, `rm -rf`, `~` paths), provide a labeled
  PowerShell alternative.

## Prohibited Actions
- Never hardcode API keys, cookies, or personal info
- Never use emoji characters anywhere in the project
- Never use subprocess.run(text=True) without encoding="utf-8"
- Never read/write files without explicit encoding="utf-8"
- **Never use `os.kill(pid, 0)` for process liveness checks.** On Windows,
  `signal.CTRL_C_EVENT == 0`, so this sends Ctrl+C to the target process
  instead of probing it.  Use `ctypes.windll.kernel32.OpenProcess()` on
  Windows, `os.kill(pid, 0)` only on Unix, behind a `sys.platform` guard.
- **Never duplicate utility functions across files.** If the same helper
  exists in >1 file, extract it to a shared module and import it.
- **TASKS.md is read-only** -- auto-generated from `.claude/tasks.db`. Never edit directly.
  Use `python .claude/hooks/task_db.py <command>` for all task operations.
  A PreToolUse hook blocks any Write/Edit targeting TASKS.md.
- **Task IDs are auto-generated.** Never invent IDs manually.
  Use `task_db.py add --title "..." --priority P0` and the system assigns the next ID.
- **For batch operations**: use `task_db.py batch --commands '[...]'` to wrap multiple
  commands atomically. Use flat keys: `{"cmd": "add", "title": "...", "priority": "P0"}`,
  NOT nested `{"cmd": "add", "args": {"title": "..."}}`. Validate required fields.

## Behavior Rules
- **Fix violations immediately**: When a check you run (lint, emoji scan, tests) discovers
  violations in project files, fix them immediately.

### Verification Requirements
- **"Tests pass" is necessary but not sufficient.** If your task changes a
  server entry point, subprocess launcher, or configuration loader, you MUST
  also run the actual code (not just mocked tests) and verify it produces
  expected output.
- **Smoke test rule**: After creating or modifying a script that users will
  invoke directly (e.g. `run_server.py`, `start.ps1`), run it for real and
  verify it reaches the expected state (e.g. "Application startup complete").
  A crash during dry-run is a blocker, not an "unrelated issue."
- **Mock tests verify arguments. Real tests verify behavior.** Both are
  needed for subprocess-based code.
- **Platform-sensitive code needs platform-specific review.** Before using
  any `os.*`, `signal.*`, or `subprocess.*` API, check the Python docs for
  Windows behavior differences.  If a function has `sys.platform` branches,
  test both branches.  Common traps: `os.kill` signal semantics, `os.getpgid`
  not existing, `signal.SIGTERM` vs `CTRL_BREAK_EVENT`.
- **Diff First rule for investigation tasks.** When given a working example
  (user-provided command, docs snippet, or reference implementation) and a
  broken implementation, the FIRST step is a mechanical diff of flags, args,
  and config between the two.  Every delta is a finding.  Do NOT skip to
  output-format analysis or external doc research before completing this diff.
  Analysis of "why" comes AFTER identifying "what's different."
- **Schema migration rule**: `SQLAlchemy create_all()` only creates NEW tables,
  never ALTERs existing ones. Any new column on an existing model needs a versioned
  migration (idempotent ALTER TABLE). In-memory test DBs always start fresh and
  will NOT catch missing migrations.

### Task Planning Mode
Use the `/task-planning` skill for structured planning sessions. It activates plan mode
(via `plan_mode.py activate`), which blocks all mutating tools via a PreToolUse hook,
ensuring only read-only operations and `task_db.py` commands are allowed.

Manual activation: `python .claude/hooks/plan_mode.py activate`
Check status: `python .claude/hooks/plan_mode.py status`
Deactivate: `python .claude/hooks/plan_mode.py deactivate`
Validate output: `python .claude/hooks/plan_validate.py`

## Task Planning Rules

These rules prevent the class of bugs found in T-P0-24 (review gate UX), where
the task was marked DONE but the drag-to-REVIEW workflow was broken because
planning missed entire branches of behavior.

1. **Scenario matrix**: Before writing code for any conditional UX task, list
   ALL condition branches with their expected outcome in the task spec.
   Check: every `if` in the AC has a corresponding `else`.
   Example: "Gate ON: modal appears. Gate OFF: direct transition + pipeline
   starts automatically."

2. **Journey-first ACs**: At least one AC per task must be a full user journey:
   "User does X -> system does Y -> user observes Z." Unit-level ACs
   ("endpoint returns 200") are necessary but not sufficient.

3. **Cross-boundary integration**: When a task spans backend + frontend, at
   least one AC must verify end-to-end wiring: API call triggers expected
   backend behavior AND result appears in UI. Verifying each piece exists
   in isolation is not enough.

4. **"Other case" gate**: Every conditional AC ("when X is enabled...") must
   explicitly specify what happens when the condition is false. If the inverse
   case is not specified, add it before starting work. Missing inverse =
   missing requirement.

5. **Manual smoke test AC**: Every UX task must include an AC of the form
   "Manually verify: [exact browser action] -> [expected visual result]."
   "Build succeeds" and "tests pass" do not catch wiring failures.

6. **New-field consumer audit**: When a task introduces a new model field
   (e.g., `plan_status`) that existing UI components might display, list
   ALL components that render related data and verify each uses the correct
   source of truth.  A new field that no consumer reads yet is dead code;
   a consumer that reads the new field before it is populated shows stale data.
   (Post-mortem: T-P0-57/T-P0-59 -> T-P0-66 -- `hasNoPlan` used `plan_status`
   instead of `description`, showing wrong state for all existing tasks.)

## State Machine Rules

1. **Document transitions completely**: Any workflow with status transitions
   must document in the task spec: (a) all valid states, (b) the trigger for
   each transition, (c) side-effects attached to each transition.
   Side-effects on transitions (e.g., "entering REVIEW starts the review
   pipeline") are the backend's responsibility -- the frontend only initiates
   the status change, never the side-effect directly.

## Hook Development Rules
- **Never use bare `json.load(sys.stdin)`** -- always use `hook_utils.safe_read_stdin()`
- **Hooks must never crash** -- infrastructure errors must exit 0, never a raw traceback
- **Use `hook_utils.run_hook()`** as the entry point for all hooks
- **New hooks**: copy `.claude/hooks/_template.py` and fill in the logic

## Human Input Protocol
- Tasks requiring human-provided files are tagged `[NEEDS-INPUT: description]` in TASKS.md
- `docs/human_input/` contains the master checklist and per-task spec files
- Use `/collect-input` to check status, guide input, validate, and unblock tasks

---

## Session Workflow

The **SessionStart hook** provides authoritative startup context including task status,
recent progress, and lessons. Trust its output at session start.

### During Work
- Work on **one task at a time**. Move it to "In Progress" via `task_db.py update T-XX-N --status in_progress`.
- Refer to the task's **Acceptance Criteria** as your definition of done.
- If you discover new work, add it via `task_db.py add`. Don't silently absorb scope.
- For **L-complexity tasks**, maintain `.claude/checkpoint.json` with sub-task progress:
  ```json
  {"task": "T-XX-N", "subtasks": [{"name": "...", "done": false}],
   "last_working_file": "src/...", "last_working_line": 42}
  ```

### Autonomous Mode
When triggered via `scripts/autonomous_run.sh`, read `docs/workflow/autonomous.md` for
the full ruleset.

---

## Exit Protocol

Before stopping, complete these steps (the **Stop hook** enforces them):

0. **Run checks**: `bash scripts/check.sh` (primary defense -- Stop hooks don't fire on pure text exits)
1. **Verify**: Run code, check outputs exist, run tests if applicable
2. **PROGRESS.md**: Append a session entry (format below)
3. **TASKS.md**: Update task status via `task_db.py update T-XX-N --status completed`
4. **LESSONS.md**: Only if bug >10 min, surprising behavior, or effective pattern

```
## YYYY-MM-DD HH:MM -- [TASK-XXX] Brief Title
- **What I did**: 1-3 sentences
- **Deliverables**: Files created/modified
- **Sanity check result**: What was verified
- **Status**: [DONE] / [PARTIAL] (what remains) / [BLOCKED] (why)
- **Request**: `task_db.py update T-XX-N --status completed` / No change
```

Full protocol details: `docs/workflow/exit-protocol.md`

---

## File Conventions

| File | Purpose | Update frequency | Size invariant |
|------|---------|-----------------|----------------|
| `TASKS.md` | Auto-generated from `.claude/tasks.db` | Auto-regenerated by task_db.py | Read-only. Use `task_db.py` for all changes. |
| `PROGRESS.md` | Chronological session log | Every session (append-only) | Under ~300 lines. Archive older sessions to `archive/progress_log.md` when exceeded. Keep ~40-50 most recent sessions. |
| `LESSONS.md` | Critical knowledge and mistakes | Only when a lesson is learned | N/A |

**`.claude/tasks.db`** is the runtime source of truth for task state.
**TASKS.md** is the git-tracked projection (auto-generated, read-only).

**PROGRESS.md** archival convention: When the file exceeds ~300 lines, move older session entries (keeping the most recent ~40-50 sessions) to `archive/progress_log.md`. The archive file uses chronological order (oldest first) matching PROGRESS.md structure. New content is appended to the archive file on subsequent archivals.
