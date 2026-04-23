# Task Backlog

<!-- Auto-generated from .claude/tasks.db. Do not edit directly. -->
<!-- Use: python .claude/hooks/task_db.py --help -->

## In Progress

## Active Tasks

### P0 -- Must Have (core functionality)

### P1 -- Should Have (agentic intelligence)

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

#### T-P1-194: [SYNC] Propagate 2 improvements to helixos from MLInterviewPrep
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: Source: MLInterviewPrep (2026-03-20 bash-tool lesson propagated). Changes needed: (1) settings.json: replace bare python with /c/Anaconda/python.exe absolute path in ALL hook commands (PreToolUse, PostToolUse, Stop, SessionStart). (2) settings.json: add setup_python_env.sh as first SessionStart hook (writes Anaconda PATH to $CLAUDE_ENV_FILE -- needed for env var injection since .bashrc is not sourced). (3) test_check.py: remove deprecated stop cache (check_stop_cache/write_stop_cache) per 2026-03-18 lesson -- MLInterviewPrep already removed these. Note: helixos currently has broken hooks on Windows because bare python resolves to the Windows Store stub.

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

#### T-P2-204: [SYNC] Propagate 4 new MLInterviewPrep lessons to helixos LESSONS.md
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: 4 lessons from MLInterviewPrep (2026-04-10 to 2026-04-15) not yet in helixos. Tags: #validation #production-path #react-markdown #custom-scheme #orchestration #autonomous #sticky-flag #markdown #latex #regex-scoping. (1) 2026-04-10: Validation must happen on a surface isomorphic to the production path. (2) 2026-04-13: react-markdown v10 urlTransform strips custom schemes -- helixos uses react-markdown. (3) 2026-04-13: Orchestrator all_done flag is sticky -- new batch launches silently bail if session_state.json has all_done:true. (4) 2026-04-15: Auto-bolding inside LaTeX/code leaks ** into rendered output. Source: MLInterviewPrep/LESSONS.md entries 2026-04-10 through 2026-04-15.

#### T-P2-205: [SYNC] Propagate dual tasks.db scoping lesson (2026-04-16) from MLInterviewPrep to helixos
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: MLInterviewPrep LESSONS.md has a new lesson [2026-04-16] not yet propagated to helixos.

Lesson: Dual tasks.db scoping: task_db.py adds go to cwd nearest CLAUDE.md.
Summary: task_db.py resolves project root from cwd -- tasks added from root Gen_AI_Proj go into root tasks.db, not the sub-project db. autonomous_run.sh cds into sub-project, sees 0 tasks, 10 sessions no-op. Fix: always cd into sub-project before task_db.py add.
Tags in source: #orchestrator #task-db #multi-repo #session-scoping #silent-failure -- all universal.

Action: Append a [PROPAGATED] version of this lesson to helixos/LESSONS.md.
Source: MLInterviewPrep/LESSONS.md, section [2026-04-16].

#### T-P2-207: [SYNC] Propagate 2 lessons from MLInterviewPrep to helixos LESSONS.md
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: 2 universal lessons from MLInterviewPrep not yet in helixos:

(1) 2026-04-16 Dual task.db scoping: task_db.py adds go to cwd nearest CLAUDE.md. Sub-project autonomous runs see a different tasks.db from root. Fix: always cd into target sub-project before task_db.py add; orchestrator checks has-unblocked before starting. Tags: #orchestrator #task-db #multi-repo #session-scoping #silent-failure

(2) 2026-04-17 Claude Code usage limits: claude -p is subject to daily subscription cap; batches of 90-130+ calls can exhaust it; 429 returned as rc=1 JSON with api_error_status:429 and result containing You have hit your limit. Detection: check for that string in result. Fix: detect 429 and fail-fast with clear message; split large batches across days; use idempotency to safely resume. Tags: #claude-code #usage-limits #batch-scripts #429-retry

AC: Both lessons added to helixos/LESSONS.md with [PROPAGATED] tag and source attribution.

#### T-P2-208: [SYNC] Propagate 3 MLInterviewPrep lessons (2026-04-16..04-19) to helixos LESSONS.md
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: 3 universal lessons from MLInterviewPrep LESSONS.md that are not yet propagated to helixos LESSONS.md:

1. [2026-04-16] Dual tasks.db scoping: task_db.py add resolves project root via cwd nearest CLAUDE.md walk-up, so adding tasks from Gen_AI_Proj root goes to the root .claude/tasks.db, not the sub-project one. Running autonomous_run.sh with sub-project then finds no tasks. Fix: always cd into sub-project before adding tasks. Tags: #autonomous #task-db #sub-project

2. [2026-04-18] Background runner visibility: nohup...& + Bash run_in_background=true is dangerous. The Bash tool tracks the short-lived launcher (exits in <1s via &), not the real long-running runner. Either: (a) use run_in_background=true WITHOUT & and nohup so Bash owns the real PID, or (b) keep nohup & but pair with Monitor on tail -f of the log file. Never combine both. Tags: #orchestration #autonomous #bash #monitor #visibility

3. [2026-04-19] Human-approval-gate language in task specs is sticky: autonomous sessions re-read task spec verbatim each session. Gate prose like does NOT auto-start... waits for Discord approval stays sticky even after the gate is cleared externally. Fix: use a separate blocking dependency task for gates (mark it completed when human approves), or make gate prose self-cancelling: If T-P0-NNN status=completed this gate is cleared. Update the description via task_db.py update --description after clearing gates. Tags: #autonomous #task-spec #approval-gate #gotcha

AC: Each lesson appears in helixos LESSONS.md tagged [PROPAGATED] with Source: MLInterviewPrep.

#### T-P2-210: [SYNC] Propagate 4 universal lessons from MLInterviewPrep (04-16..04-20) to helixos LESSONS.md
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Append 4 propagatable lessons from MLInterviewPrep/LESSONS.md to helixos/LESSONS.md with [PROPAGATED] tags. (1) 2026-04-16 Dual tasks.db scoping: task_db.py add uses cwd nearest CLAUDE.md -- tasks for sub-project must be added from sub-project dir. (2) 2026-04-17 Claude Code usage limit breaks claude -p batch scripts (~90-130 calls/day cap, 429 rc=1 JSON, need idempotency). (3) 2026-04-18 Background runner visibility: never combine nohup+& with run_in_background; use Monitor for detached runners. (4) 2026-04-19 Approval-gate language in task specs is sticky -- write it self-cancelling or use a blocking dependency task instead. Source: MLInterviewPrep/LESSONS.md entries 2026-04-16 through 2026-04-19.

#### T-P2-212: [DEBT] mlinterviewprep: Audit 9 hook files for missing encoding=utf-8 on file I/O
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: 9 MLInterviewPrep hook files have no encoding= keyword and may do file I/O without explicit UTF-8 (fails with cp1252 on Windows). Files: _template.py, block_dangerous.py, commit_msg_guard.py, file_watch_warn.py, plan_mode_hook.py, secret_guard.py, task_dedup_check.py, task_store.py, tasks_md_guard.py. Audit each file -- hooks that only read sys.stdin and write sys.stdout need no change. Focus on secret_guard.py (reads file content) and file_watch_warn.py (checks file paths) as highest risk. Add encoding='utf-8' to any open() calls that lack it. Rule: all file I/O must specify encoding='utf-8' per helixos CLAUDE.md.

#### T-P2-213: [SYNC] Propagate 4 MLInterviewPrep lessons (2026-04-16..04-19) to LESSONS.md
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Cross-project sync: 4 universal lessons from MLInterviewPrep LESSONS.md not yet in helixos LESSONS.md (last sync was 2026-04-16).

Lessons to propagate (copy from MLInterviewPrep/LESSONS.md, add [PROPAGATED] tag):
1. [2026-04-16] Dual tasks.db scoping -- task_db.py adds go to cwd nearest CLAUDE.md. Tags: #task-db #multi-repo #session-scoping #silent-failure
2. [2026-04-17] Claude Code usage limit breaks long claude -p batch scripts (429 JSON response, not stderr). Tags: #claude-code #usage-limits #batch-scripts #429-retry
3. [2026-04-18] Background runner visibility: nohup ... & vs Bash run_in_background -- do NOT mix them. Tags: #orchestration #autonomous #bash #monitor #visibility #gotcha
4. [2026-04-19] Human-approval-gate language in task specs is sticky -- write self-cancelling gates or use separate blocking tasks. Tags: #autonomous #task-spec #approval-gate #gotcha #workflow

Action: Append each as a [PROPAGATED] entry to helixos/LESSONS.md. Source: MLInterviewPrep/LESSONS.md.
Verification: grep -c PROPAGATED helixos/LESSONS.md should increase by 4.

#### T-P2-214: [SYNC] Propagate 2 universal lessons from MLInterviewPrep to helixos
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: Propagate 2 new universal lessons from MLInterviewPrep/LESSONS.md to helixos/LESSONS.md (not yet present in helixos):
1. [2026-04-18] Background runner visibility: nohup+& vs Bash run_in_background — explains when to use each pattern to maintain Bash tool process visibility. Source: search "Background runner visibility" in MLInterviewPrep/LESSONS.md. Tags: #orchestration #autonomous #bash #monitor.
2. [2026-04-19] Human-approval-gate language in task specs is sticky — embed self-cancelling gate prose or use a separate blocking task; never leave standing-rule gate language after condition clears. Source: search "approval-gate" in MLInterviewPrep/LESSONS.md. Tags: #autonomous #task-spec #approval-gate.
Both match universal propagation tags. Append to helixos/LESSONS.md as [PROPAGATED] entries with source attribution. Read-only check: helixos LESSONS.md at 329 lines — may need archival before appending if threshold is reached.

### P3 -- Stretch Goals

## Blocked

## Completed Tasks

> 16 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

- [x] **2026-04-23** -- T-P1-211: [DEBT] helixos: Fix bare python -> /c/Anaconda/python.exe in settings.json (11 hook commands). Closed as duplicate of T-P1-185.
- [x] **2026-04-23** -- T-P1-209: [DEBT] helixos settings.json: replace bare python with /c/Anaconda/python.exe + add setup_python_env.sh. Closed as duplicate of T-P1-185.
- [x] **2026-04-23** -- T-P1-206: [DEBT] helixos: Replace bare python with /c/Anaconda/python.exe in settings.json (11 hooks) + add setup_python_env.sh. Closed as duplicate of T-P1-185.
- [x] **2026-04-23** -- T-P1-201: [DEBT] helixos: Fix bare python in settings.json and add setup_python_env.sh. Closed as duplicate of T-P1-185.
- [x] **2026-04-23** -- T-P1-197: [SYNC] helixos: Replace bare python with /c/Anaconda/python.exe in settings.json. Closed as duplicate of T-P1-185.
- [x] **2026-04-23** -- T-P1-189: [SYNC] Fix helixos: add setup_python_env.sh and use absolute python path in hooks. Closed as duplicate of T-P1-185 (bundled helixos python-stub fix + setup_python_env.sh).
- [x] **2026-04-23** -- T-P1-185: [SYNC] Fix Python stub (exit 49): update helixos settings.json to use absolute python path. helixos/.claude/settings.json uses bare `python` in all hook commands. On this Windows machine, bare `python` resolves t
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
