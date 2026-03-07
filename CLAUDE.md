# Project Context

<!-- CUSTOMIZE: Replace this section with your project's overview -->

## Project Overview
<!-- Describe what your project does in 2-3 sentences -->

## Tech Stack
<!-- List your core technologies -->
- Python 3.11+
- pytest (testing)
- ruff (linting)

## Key Constraints
<!-- CUSTOMIZE: Add your project-specific constraints -->
- All API keys and cookies from .env, never hardcoded
- Every function must have type hints and docstring
- **Dependency source-of-truth**: Both `pyproject.toml` `[project].dependencies` and
  `requirements.txt` list dependencies. Keep them in sync manually. When adding a new
  dependency, add it to BOTH files. `pyproject.toml` is the canonical spec;
  `requirements.txt` exists for `pip install -r` convenience.

## File Structure
<!-- CUSTOMIZE: Describe your project's directory layout -->
- `src/` - Source code
- `tests/` - Test files
- `config/` - Configuration files
- `data/` - Runtime data (not in git)

## Invariants (must always hold, violation = bug)
<!-- CUSTOMIZE: List your project's invariants. These are checked by /review -->
1. .env file never tracked by git
2. No hardcoded secrets in code
3. <!-- Add your domain-specific invariants here -->

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
- **Windows asyncio subprocess**: Any use of `asyncio.create_subprocess_exec` or
  `create_subprocess_shell` requires `WindowsProactorEventLoopPolicy` at app startup.
  Guard with `sys.platform == "win32"`.
- **Schema changes require migration**: When adding a column to an existing
  SQLAlchemy model, ensure `init_db()` handles the case where the table
  already exists without the new column.  Never assume users will delete
  their database.

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
- **Never invent new task ID formats.** Task IDs must match `T-P{priority}-{number}`
  (e.g., T-P0-1, T-P1-42). Do not create alternative prefixes like T-TD, T-BUG, etc.
  Use the Priority field inside the task spec for categorization instead.
<!-- CUSTOMIZE: Add your project-specific prohibitions -->

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

### Task Planning Mode
When the user says "plan tasks" / "edit TASKS.md only" / contains keyword "TASKS.md":
- **ONLY** read code and edit TASKS.md (add/reorder/restructure tasks, set dependencies)
- Do **NOT** execute any task, write code, create files, or run tests
- Do **NOT** use TaskCreate/TaskUpdate/TaskList tools (session-only, not persistent)
- Write clear task specs with acceptance criteria, complexity, and dependencies
- End by summarizing what changed in TASKS.md

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
- Work on **one task at a time**. Move it to "In Progress" in TASKS.md when you begin.
- Refer to the task's **Acceptance Criteria** as your definition of done.
- If you discover new work, add it to TASKS.md. Don't silently absorb scope.
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

1. **Verify**: Run code, check outputs exist, run tests if applicable
2. **PROGRESS.md**: Append a session entry (format below)
3. **TASKS.md**: Update task status
4. **LESSONS.md**: Only if bug >10 min, surprising behavior, or effective pattern

```
## YYYY-MM-DD HH:MM -- [TASK-XXX] Brief Title
- **What I did**: 1-3 sentences
- **Deliverables**: Files created/modified
- **Sanity check result**: What was verified
- **Status**: [DONE] / [PARTIAL] (what remains) / [BLOCKED] (why)
- **Request**: Move TASK-XXX to Completed (REMOVE spec block from Active/In Progress, ADD summary line to Completed Tasks) / No change
```

Full protocol details: `docs/workflow/exit-protocol.md`

---

## File Conventions

| File | Purpose | Update frequency |
|------|---------|-----------------|
| `TASKS.md` | Task backlog and status tracking | Every session |
| `PROGRESS.md` | Chronological session log | Every session (append-only) |
| `LESSONS.md` | Critical knowledge and mistakes | Only when a lesson is learned |

TASKS.md is the **single source of truth** for what needs to be done.
