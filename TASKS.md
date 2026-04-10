# Task Backlog

<!-- Auto-generated from .claude/tasks.db. Do not edit directly. -->
<!-- Use: python .claude/hooks/task_db.py --help -->

## In Progress

## Active Tasks

### P0 -- Must Have (core functionality)

### P1 -- Should Have (agentic intelligence)

#### T-P1-185: [SYNC] Fix Python stub (exit 49): update helixos settings.json to use absolute python path
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: helixos/.claude/settings.json uses bare `python` in all hook commands. On this Windows machine, bare `python` resolves to the Windows Store stub (AppData/Local/Microsoft/WindowsApps/python.exe) which exits with code 49 -- a no-op. All PreToolUse, PostToolUse, and Stop hooks are silently failing on every invocation.

Fix (copy from MLInterviewPrep):
1. Replace all `python` with `/c/Anaconda/python.exe` in .claude/settings.json
2. Add setup_python_env.sh to .claude/hooks/ (copy from MLInterviewPrep/.claude/hooks/setup_python_env.sh)
3. Add SessionStart bash hook entry: `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/setup_python_env.sh"`
4. Append lesson to LESSONS.md: [2026-03-20] Claude Code Bash tool ignores .bashrc -- use CLAUDE_ENV_FILE and absolute paths

Ref: MLInterviewPrep commit bc22e4d. Also clean up .claude/settings.local.json.bak (add to .gitignore).

#### T-P1-186: [SYNC] Fix Python stub (exit 49): update homestead settings.json to use absolute python path
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: homestead/.claude/settings.json uses bare `python` in all hook commands. On this Windows machine, bare `python` resolves to the Windows Store stub which exits code 49 -- all hooks are silently failing.

Fix (copy from MLInterviewPrep):
1. Replace all `python` with `/c/Anaconda/python.exe` in .claude/settings.json
2. Add setup_python_env.sh to .claude/hooks/ (copy from MLInterviewPrep/.claude/hooks/setup_python_env.sh)
3. Add SessionStart bash hook entry: `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/setup_python_env.sh"`
4. Append lesson to LESSONS.md: [2026-03-20] Claude Code Bash tool ignores .bashrc -- use CLAUDE_ENV_FILE and absolute paths

Ref: MLInterviewPrep commit bc22e4d.

#### T-P1-187: [SYNC] Fix Python stub (exit 49): update blog-proj settings.json to use absolute python path
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: blog-proj/.claude/settings.json uses bare `python` in all hook commands. On this Windows machine, bare `python` resolves to the Windows Store stub which exits code 49 -- all hooks are silently failing.

Fix (copy from MLInterviewPrep):
1. Replace all `python` with `/c/Anaconda/python.exe` in .claude/settings.json
2. Add setup_python_env.sh to .claude/hooks/ (copy from MLInterviewPrep/.claude/hooks/setup_python_env.sh)
3. Add SessionStart bash hook entry: `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/setup_python_env.sh"`
4. Append lesson to LESSONS.md: [2026-03-20] Claude Code Bash tool ignores .bashrc -- use CLAUDE_ENV_FILE and absolute paths

Ref: MLInterviewPrep commit bc22e4d.

#### T-P1-189: [SYNC] Fix helixos: add setup_python_env.sh and use absolute python path in hooks
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: CRITICAL: helixos settings.json uses bare python for all hook commands, which resolves to the Windows Store stub (exit code 49). All hooks are silently failing. Fix: (1) Copy setup_python_env.sh from MLInterviewPrep to .claude/hooks/. (2) Add SessionStart bash hook for setup_python_env.sh in settings.json. (3) Update all settings.json hook commands from bare python to /c/Anaconda/python.exe. Reference: MLInterviewPrep LESSONS.md [2026-03-20] entry and MLInterviewPrep .claude/settings.json for the fixed version.

#### T-P1-194: [SYNC] Propagate 2 improvements to helixos from MLInterviewPrep
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: Source: MLInterviewPrep (2026-03-20 bash-tool lesson propagated). Changes needed: (1) settings.json: replace bare python with /c/Anaconda/python.exe absolute path in ALL hook commands (PreToolUse, PostToolUse, Stop, SessionStart). (2) settings.json: add setup_python_env.sh as first SessionStart hook (writes Anaconda PATH to $CLAUDE_ENV_FILE -- needed for env var injection since .bashrc is not sourced). (3) test_check.py: remove deprecated stop cache (check_stop_cache/write_stop_cache) per 2026-03-18 lesson -- MLInterviewPrep already removed these. Note: helixos currently has broken hooks on Windows because bare python resolves to the Windows Store stub.

#### T-P1-197: [SYNC] helixos: Replace bare python with /c/Anaconda/python.exe in settings.json
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: ALL 11 hook commands in helixos/.claude/settings.json use bare `python` which resolves to the Windows Store stub (exit code 49), silently breaking ALL safety hooks (block_dangerous, secret_guard, lint_check, commit_msg_guard, etc.).

Fix:
1. Replace every `python ` with `/c/Anaconda/python.exe ` in .claude/settings.json
2. Copy setup_python_env.sh from MLInterviewPrep/.claude/hooks/ and add it as the first SessionStart hook

Context: MLInterviewPrep fixed this on 2026-03-20. Lesson #bashrc was propagated to helixos LESSONS.md but settings.json was never actually updated -- the propagation was docs-only.

AC:
- All settings.json hook commands use /c/Anaconda/python.exe
- SessionStart includes setup_python_env.sh as first hook
- Verify hooks fire correctly by running a test bash command and confirming plan_mode_hook.py output appears

#### T-P1-201: [DEBT] helixos: Fix bare python in settings.json and add setup_python_env.sh
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: helixos settings.json uses bare python in ALL hook commands, violating the CLAUDE.md rule added in T-P2-185. This means hooks may silently fail on Windows (resolves to Windows Store stub, exit code 49).

Fixes needed:
1. Replace bare python with /c/Anaconda/python.exe in all hook commands in .claude/settings.json (PreToolUse, PostToolUse, Stop, SessionStart hooks).
2. Add setup_python_env.sh SessionStart hook -- MLInterviewPrep has this but helixos is missing it. This injects Anaconda into PATH for Bash tool calls via CLAUDE_ENV_FILE. Copy from MLInterviewPrep/.claude/hooks/setup_python_env.sh.

Verify: After update, run a hook manually to confirm /c/Anaconda/python.exe is used and exits 0.

Source: Diff of helixos vs MLInterviewPrep settings.json during cross-project sync 2026-04-10.

### P2 -- Nice to Have

#### T-P2-188: [SYNC] Propagate security-hygiene lesson from helixos to MLInterviewPrep/homestead/blog
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: helixos LESSONS.md entry 27 (Security hygiene: personal paths, accidental files, local settings in git) covers: (1) avoid hardcoded Windows user paths in config files -- use ~/ or env vars, (2) .gitignore rules for pip output (=*), *.pem, *.key, settings.local.json, (3) secret_guard.py checks for sensitive file targets not just content. Tags: #security #gitignore #personal-paths. This lesson is missing from MLInterviewPrep, homestead, and blog LESSONS.md files. Copy the relevant text and append to each.

#### T-P2-190: [SYNC] Propagate exit protocol Step 0 (check.sh) to MLInterviewPrep
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: MLInterviewPrep CLAUDE.md Exit Protocol is missing Step 0: Run checks: bash scripts/check.sh (primary defense -- Stop hooks do not fire on pure text exits). Helixos has this step. Update MLInterviewPrep CLAUDE.md Exit Protocol to add Step 0 before the current steps. scripts/check.sh already exists in MLInterviewPrep.

#### T-P2-191: [DEBT] MLInterviewPrep: Fix duplicate Key Constraints section in CLAUDE.md
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: CLAUDE.md has two identical ## Key Constraints sections (lines 15-19 and lines 34-37). Remove the duplicate. Both say: All API keys and cookies from .env, never hardcoded. Every function must have type hints and docstring.

#### T-P2-192: [DEBT] MLInterviewPrep: Add .claude/worktrees/ to .gitignore
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: git status shows .claude/worktrees/ as untracked. The .gitignore covers other .claude/ runtime files (state.json, checkpoint.json, tasks.db, etc.) but not worktrees/. Add .claude/worktrees/ to .gitignore.

#### T-P2-193: [DEBT] MLInterviewPrep: Add runtime dependencies to pyproject.toml [project].dependencies
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: pyproject.toml has no [project].dependencies section. All runtime deps (fastapi, sqlalchemy, uvicorn, anthropic, pydantic, pydantic-settings, python-dotenv, httpx, python-docx, edge-tts, beautifulsoup4, playwright, python-multipart, pyyaml) only exist in requirements.txt. Per CLAUDE.md key constraint: both files must be kept in sync. Add [project].dependencies section to pyproject.toml matching requirements.txt runtime deps.

#### T-P2-195: [DEBT] MLInterviewPrep: Remove deprecated stop cache from hook_utils.py
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: hook_utils.py still defines check_stop_cache() (line 129) and write_stop_cache() (line 157) which are deprecated per the 2026-03-18 lesson (cache removed to ensure fresh checks). MLInterviewPrep test_check.py no longer calls them (already cleaned up). These dead definitions should be removed from hook_utils.py. Verify no other callers exist in MLInterviewPrep before removing.

#### T-P2-196: [SYNC] Propagate React scroll-aware lesson to helixos LESSONS.md
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Source: MLInterviewPrep LESSONS.md [2026-03-17]. Lesson: UI component best practices for scroll-aware mode switching (#react #scroll #sticky #hooks). Relevant to helixos React frontend. Key rules: (1) Use explicit refs for scroll containers, never DOM traversal. (2) Encapsulate mode transitions in switchMode() not bare setMode(). (3) Use ResizeObserver+timeout for layout timing, not rAF guessing. (4) Extract shared hooks (useScrollRestore). (5) Guard maxScroll<=0 arithmetic. This lesson is already in MLInterviewPrep LESSONS.md but missing from helixos. Action: append the lesson entry to helixos/LESSONS.md.

#### T-P2-198: [DEBT] MLInterviewPrep: Fix 2 ruff I001 import-sort errors
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: ruff check src/ tests/ reports 2 fixable I001 (unsorted imports) errors:
1. src/backend/main.py:2 - baking_router import out of order at bottom
2. src/backend/models/__init__.py - baking model imports out of order

These were likely introduced by the T-P1-155..162 Baking Studio feature commits.

Fix: Run `ruff check --fix src/` or manually sort the imports in the two files.

AC:
- ruff check src/ tests/ reports no errors

#### T-P2-199: [DEBT] MLInterviewPrep: Add problems.db to .gitignore
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: problems.db appears as an untracked file in MLInterviewPrep. SQLite database files should never be committed to git (contain runtime data, can be large, causes merge conflicts).

Fix: Add `problems.db` and `*.db` (or at least `problems.db`) to MLInterviewPrep/.gitignore

AC:
- problems.db no longer appears in git status
- .gitignore updated to exclude *.db or problems.db

#### T-P2-200: [SYNC] Propagate 5 harness improvements from MLInterviewPrep to helixos
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Propagate improvements discovered in MLInterviewPrep recent commits to helixos.

1. LESSONS.md - add 3 new universal entries not yet propagated:
   a. [2026-04-08] autonomous_run.sh uses sub-project task_db, not root -- #autonomous #task-db
   b. [2026-04-10] Validation must happen on a surface isomorphic to the production path -- #validation #production-path
   c. [2026-04-08] DB-only content must have a recovery path -- #data-loss #backup #sqlite

2. CLAUDE.md Verification Requirements -- add 2 new rules:
   a. Side-effect verification must go through the consumer, not the producer. After DB seed/insert, verify via API curl (consumer), not direct SELECT (producer).
   b. Validation must use the production build path: npm run build (tsc -b && vite build), not tsc --noEmit.

3. .claude/hooks/lint_check.py -- add dist to _SKIP_DIRS (from T-P1-332: compiled output is not source code).

Source: MLInterviewPrep commits 943275f, 6d9fda7, 05f99a3, 07e9b00 from 2026-04-10.

### P3 -- Stretch Goals

## Blocked

## Completed Tasks

> 16 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

- [x] **2026-04-10** -- T-P2-203: orchestrator_config: relocate blog-proj to Gen_AI_Proj nested path. Update orchestrator_config.yaml blog-proj repo_path and claude_md_path from ~/Desktop/blog_proj to ~/Desktop/Gen_AI_Proj
- [x] **2026-04-10** -- T-P2-202: gitignore: add secret patterns and Claude runtime state. Expand helixos .gitignore to cover workspace secret convention (.secrets/, *.secret, *.token, credentials*) and Claude C
- [x] **2026-03-15** -- T-P1-184: Multi-project orchestrator script. Serial multi-project dispatcher that reads orchestrator_config.yaml and runs claude sessions per project
- [x] **2026-03-15** -- T-P1-183: has_unblocked_tasks() in task_store + CLI command. Add has_unblocked_tasks() method and has-unblocked CLI command for orchestrator use
- [x] **2026-03-14** -- T-P2-182: Fix CI test warnings (unawaited coroutines, leaked transports)
- [x] **2026-03-13** -- T-P2-180: Fix ruff lint errors, flaky tests, and emoji violations for CI
- [x] **2026-03-13** -- T-P1-181: Fix 27 CI test failures (API drift, tasks.db migration, missing wrapper). Fix 27 test failures across 6 files caused by API signature drift, tasks.db migration, and missing archive_completed_tas
- [x] **2026-03-12** -- T-P3-177: Persist filter state to localStorage. Filter state (filterStatus, filterPriorities, filterComplexities, searchQuery) resets on page reload. Users must re-appl
- [x] **2026-03-12** -- T-P2-179: Add busy_timeout to task_store.py for concurrent hook safety
- [x] **2026-03-12** -- T-P2-176: Add browser notification for needs-human review state. When review pipeline transitions task to review_needs_human, users are not proactively notified. They must check REVIEW 
- [x] **2026-03-12** -- T-P2-175: Add review sub-status badges to task cards. TaskCard currently shows generic "REVIEW" badge for all 3 review sub-states (review, review_auto_approved, review_needs_
- [x] **2026-03-12** -- T-P0-178: Implement DB-as-source-of-truth for task management. Replace regex-based TASKS.md parsing with SQLite-backed task store
- [x] **2026-03-11** -- T-P2-174: Add atomic review submission endpoint. - Added POST /api/tasks/{id}/submit-for-review endpoint that atomically updates title/description and transitions to REV
