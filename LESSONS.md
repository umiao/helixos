# Lessons Learned

> Only log if: bug >10 min to debug, surprising behavior, effective pattern, non-obvious gotcha.

<!-- ENTRY FORMAT:

### [YYYY-MM-DD] Short descriptive title
- **Context**: What I was trying to do
- **What went wrong / What I learned**: The core insight
- **Fix / Correct approach**: How to do it right
- **Related task**: T-XX-N (if applicable)
- **Tags**: #tag1 #tag2 (for grep-based lookup)

-->

1. Windows UTF-8 (universal gotcha)
  - Python defaults to cp1252 on Windows. Non-ASCII paths/content break silently.
  - Rule: Force encoding="utf-8" on all open(), subprocess.run(), Path.read_text(). Force UTF-8 on sys.stdin/stdout/stderr in hooks.

  2. Stop hooks MUST output JSON to stdout (mentioned twice - both prompt and command types)
  - Exit codes alone = "JSON validation failed". Empty stdout = crash.
  - Rule: Every Stop hook prints {"ok": true} or {"ok": false, "reason": "..."} on every exit path (success, failure, timeout, error).
  Diagnostics go to stderr only.

  3. Hooks must never crash on bad stdin
  - /clear and other commands send unexpected input to hooks.
  - Rule: Never use bare json.load(sys.stdin). Always try/except with diagnostics. On parse failure: warn to stderr, exit 0.

  4. Shared hook_utils.py pattern
  - DRY boilerplate: UTF-8 init, JSON parsing, exception catching.
  - Rule: Use a single run_hook(name, main_fn) entry point for all hooks. Hooks become pure business logic.

  5. Rename/replace = reverse-reference scan
  - Plans list what to create, not what references the old thing.
  - Rule: grep -r "old_name" before and after. Add all referencing files to work list.

  6. Debug philosophy: check the contract before blaming the LLM
  - "Validation failed" = schema mismatch (deterministic). Not "LLM non-determinism".
  - Rule: (1) read exact error, (2) read docs for expected schema, (3) compare actual vs expected, (4) fix minimal delta. Never rewrite
  architecture on first failure.

  7. Windows asyncio subprocess requires ProactorEventLoop
  - `asyncio.create_subprocess_exec` raises `NotImplementedError` on Windows because the default `SelectorEventLoop` does not support subprocesses.
  - Root cause: Python on Windows defaults to SelectorEventLoop. ProactorEventLoop is required for subprocess support.
  - Fix: `if sys.platform == "win32": asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())` at module level, before any async code runs.
  - Also broaden `except FileNotFoundError` to catch `NotImplementedError` and `OSError` as defensive fallback.
  - Lesson: Always test the full startup path on the target platform. Unit tests with mocked subprocesses do not catch event loop policy issues.

  8. Pin linter versions exactly
  - Broad rule categories (e.g. `"UP"` in ruff) + loose version bounds (`ruff>=0.1.0`) = silent CI drift when new rules are promoted to stable.
  - Root cause: CI ran `pip install ruff` (latest), while local had ruff 0.1.14. New ruff promoted UP041 and UP042 to stable, activating them automatically under the `"UP"` category.
  - Fix: Pin exactly in requirements.txt (`ruff==0.15.4`). CI uses `pip install -r requirements.txt` instead of `pip install ruff`. Add a git pre-commit hook running `ruff check` on staged files.
  - Rule: Always pin linter/formatter versions with `==`. Use a pre-commit hook to catch lint errors before they reach CI.

  9. uvicorn internal API vs CLI mismatch for loop="none"
  - `uvicorn.run(loop="none")` works (LOOP_SETUPS contains "none"), but the CLI `--loop none` is rejected (`click.Choice` explicitly excludes it).
  - Root cause: `uvicorn/main.py` defines `LOOP_CHOICES = click.Choice([k for k in LOOP_SETUPS if k != "none"])`.
  - String-match tests (`assert "--loop none" in content`) verified the bug was PRESENT, not absent. They proved the broken command was in the file.
  - Fix: Use `scripts/run_server.py` with `uvicorn.run(loop="none")` instead of CLI invocation. Add behavioral tests that mock `uvicorn.run` and assert kwargs.
  - Rule: When testing CLI flags, write a behavioral test (mock the target function, assert it receives correct args) rather than just grepping for the flag in a script. String-match tests catch presence, not correctness.

  9b. Entry point change = concept-level reverse-reference scan
  - When replacing HOW users invoke something (CLI command, script, flag), grep for the ENTIRE old invocation pattern, not just the broken part.
  - Example: changing "uvicorn --loop none" to "run_server.py" requires scanning ALL "uvicorn" references, not just "--loop none".
  - Check: all .md files, project structure trees, design docs, test fixtures.
  - Rule: after making changes, run `grep -ri "old_entry_point" **/*.md` and verify zero unexpected hits.
  - Add an automated guard test (e.g. scan powershell code blocks for bare uvicorn) to catch future regressions.

  10. Mock-only tests hide integration failures
  - All run_server.py tests mocked uvicorn.run(), so 648 tests passed but the script crashed on real invocation with ModuleNotFoundError.
  - Root cause: uvicorn CLI does `sys.path.insert(0, ".")` in its main(), but `uvicorn.run()` does NOT. When running `python scripts/run_server.py`, Python puts `scripts/` on sys.path[0], not the project root. So `import src` fails.
  - Fix: Add `sys.path.insert(0, project_root)` in run_server.py before calling uvicorn.run(). Add a smoke test that verifies `src` is importable after main() runs.
  - Rule: When writing a launcher script, always include at least one smoke test that exercises the real import path (e.g. `importlib.util.find_spec("src") is not None`). Mock tests verify kwargs but not environment setup.

  18. Task IDs must match the parser's grammar -- never invent new formats
  - Context: T-P0-68 created 14 tech debt tasks with `T-TD-XX` IDs. The entire parsing system (tasks_parser.py, session_context.py, task_dedup_check.py) uses `T-P\d+-\d+` regex. The T-TD format was invisible to all tooling -- tasks couldn't be synced to DB, detected in session context, validated for orphans, or tracked in autonomous mode.
  - Root cause: AI invented a categorization prefix (T-TD for "tech debt") outside the established ID grammar. The format looked reasonable to a human but was structurally incompatible with every regex in the codebase.
  - Fix: Renamed all 14 tasks from T-TD-01..T-TD-14 to T-P1-70..T-P3-83 (using actual priority in the ID). Added prohibited action to CLAUDE.md: "Never invent new task ID formats."
  - Rule: Task IDs are a machine-parseable contract, not a human categorization tool. Use the Priority field inside the task spec for categorization. The ID format is `T-P{priority}-{number}` and nothing else.
  - Tags: #task-ids #parsing #naming-convention #tooling-compatibility

  11. "Tests pass" != "it works" -- verify what you ship
  - 650 mock tests passed but `python scripts/run_server.py` crashed on real invocation. DB schema crash (missing review_gate_enabled column) was dismissed as "unrelated" during a dry run that showed a full traceback.
  - Rule: if the dry run shows ANY crash, it is a bug -- not "unrelated." Fix it or explicitly document it as a known issue requiring user action.
  - Rule: for scripts/services, always run the actual code once and confirm it reaches its expected ready state (e.g. "Application startup complete"). Mock tests verify implementation; real tests verify environment.
  - Rule: SQLAlchemy create_all() only creates missing TABLES, not missing COLUMNS. Any column added to an existing model needs a migration path (see _migrate_missing_columns in db.py).

  12. T-P0-24 postmortem: task marked DONE but core workflow was broken
  - Context: T-P0-24 (review gate UX) added a modal for editing tasks before review submission. The task's ACs covered the modal UI, the PATCH endpoint, and the 428 detection flow. All 15 tests passed, frontend built clean, task was marked DONE.
  - What went wrong: Dragging a task to the REVIEW column did nothing visible -- the review pipeline never started. The task only covered the "gate ON" branch (modal appears) but never specified what happens on "gate OFF" (direct transition). More critically, no AC required the review pipeline to auto-start when a task enters REVIEW status. The pipeline trigger was a manual POST /api/tasks/{id}/review call, but nothing in the drag-drop flow called it. This required T-P0-26 (L complexity, 25 new tests) to fix by making the pipeline transition-driven.
  - Root causes:
    (a) Missing scenario matrix: the task spec listed what happens with gate ON but not gate OFF. The "other case" was invisible.
    (b) No journey-first AC: ACs verified components in isolation (modal renders, endpoint returns 200) but never specified "user drags to REVIEW -> sees spinner -> sees results."
    (c) Cross-boundary gap: frontend ReviewSubmitModal existed, backend PATCH endpoint existed, but the wiring between "status becomes REVIEW" and "pipeline starts" was never tested as an integrated flow.
    (d) No manual smoke test: the task was verified via unit tests and build checks. Actually dragging a card in the browser would have caught the issue immediately.
  - Fix: Added 6 rules to CLAUDE.md (Task Planning Rules + State Machine Rules) requiring scenario matrices, journey-first ACs, cross-boundary integration checks, inverse-case specification, manual smoke test ACs, and complete transition documentation.
  - Related tasks: T-P0-24, T-P0-26, T-P0-27
  - Tags: #planning #ux #state-machine #scenario-matrix #integration

  14. os.kill(pid, 0) is a Ctrl+C bomb on Windows
  - Context: `_is_process_alive(pid)` used `os.kill(pid, 0)` to probe liveness -- standard Unix idiom. On Windows, `signal.CTRL_C_EVENT == 0`, so `os.kill(pid, 0)` calls `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)` -- sending Ctrl+C to the target process. When `test_alive_process` called `_is_process_alive(os.getpid())`, it sent Ctrl+C to the pytest process itself, causing a `KeyboardInterrupt` ~0.5s later (always at test #503).
  - Why it was hard to catch: (a) The function worked correctly on Unix. (b) The signal is delivered asynchronously, so the crash appeared in a completely unrelated test. (c) In some execution contexts (e.g. sandboxed subprocess), the signal was caught/ignored, so the bug was intermittent.
  - Compounding factor: The function was copy-pasted into 3 files (port_registry, process_manager, subprocess_registry). Same bug x3, same fix x3.
  - Fix: On Windows, use `ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)` instead of `os.kill(pid, 0)`. Keep Unix path unchanged.
  - Rules derived:
    (a) **Never use `os.kill(pid, 0)` on Windows.** Add to Prohibited Actions.
    (b) **Never duplicate utility functions.** Extract to a shared module, import everywhere.
    (c) **Platform-sensitive code needs platform-specific tests.** If a function has a `sys.platform` branch, test BOTH branches.
    (d) **Suspicious test symptoms**: if a KeyboardInterrupt appears without user input, or a test "randomly" fails at a consistent position, suspect signal/platform bugs -- not flakiness.
  - Tags: #windows #signals #os-kill #copy-paste #platform-compat

  13. "Surface X to user" requires semantic distinctness verification
  - Context: T-P0-28 (raw_response) had 8 passing tests but the surfaced data was identical to existing fields. Decision reason had UI/schema/API support but was never persisted to DB.
  - Root cause: Tests verified plumbing (data flows through pipe) not value (pipe carries useful water). raw_response stored the same parsed result JSON already shown in summary/suggestions. human_reason was wired in UI and API schemas but write_review_decision never persisted it to the DB column.
  - Fix: For any "display X to user" task, verification must assert that X contains at least one field not already visible in the existing UI. For any user input field, trace the full path: UI -> API -> DB -> retrieval -> display. A broken link at ANY point = bug.
  - Related tasks: T-P0-28, T-P0-33
  - Tags: #data-path #e2e-verification #testing #review-panel
  15. Preflight checks bypass test mocks -- mock ALL early-return paths
  - Context: 22 tests in test_code_executor.py mocked `asyncio.create_subprocess_exec` but failed in CI because `_preflight_checks()` called `shutil.which("claude")` before reaching the mock. Returned `None` in CI (no CLI installed), causing early return with CLI_NOT_FOUND.
  - Why it passed locally: Developer machine has `claude` CLI on PATH, so preflight passed and the mock subprocess was reached.
  - Fix: Added autouse fixture patching `shutil.which` to return a path. General rule: when adding preflight/validation checks that call system APIs (`shutil.which`, `os.path.isdir`, etc.), also update test mocks -- any new early-return path will bypass existing mocks targeting later code.
  - Rule: CI is the authority, not local. If tests depend on tools being installed locally, they will fail in clean environments.
  - Tags: #testing #mocking #ci #preflight #environment-dependency
  16. Persist-first principle: never parse/format/truncate before saving raw results
  - Context: Plan generation ran 10 min, cost $1.27, produced valid plan -- then silently lost everything. Three failure layers compounded: (1) history_writer truncated to 2048 chars, (2) JSON parse failed on truncated output -> _parse_plan returned empty, (3) empty result marked as "ready" with no validation.
  - Fix: Raw CLI output persisted immediately after proc.wait() via write_raw_artifact() (no truncation). Structural validation added (_validate_plan_structure) to reject empty data. Atomic DB update for description + plan_status + plan_json.
  - Rule: Raw results must be persisted BEFORE any parsing, formatting, or truncation. Silent success on empty data is worse than loud failure.
  - Tags: #data-persistence #plan-generation #silent-failure
  17. `--permission-mode plan` + `--json-schema` are incompatible Claude CLI flags
  - Context: `--permission-mode plan` causes ExitPlanMode to be denied when `--json-schema` is also specified. The subprocess is non-interactive (`claude -p`) with structured output already enforced, so permission-mode plan adds no value and causes failures.
  - Fix: Removed `--permission-mode plan` from plan generation CLI args.
  - Tags: #claude-cli #plan-generation #incompatible-flags

  19. Mocked tests hide CLI output format issues -- verify with real CLI
  - Context: Stream-json pipeline (T-P0-87) passed all 26 tests but production showed: review `result: null` (1989 tokens generated, content discarded), ConversationView dead for plan/review, 47/57 log files empty. Mocked tests verified argument passing but never tested against real CLI output.
  - Root cause: Tests mocked subprocess output in the expected format, so parser bugs (schema validation failure, format mismatch) were invisible. Empty log files went undetected because nothing checked file sizes post-run.
  - Rule: Subprocess features need at least one real-CLI verification before marking DONE. "Tests pass" for subprocess code means mock tests + at least one real integration test.
  - Tags: #testing #mocking #subprocess #stream-json #integration

  20. Claude CLI `result` vs `structured_output` field -- the --json-schema trap
  - Context: T-P0-91 investigation. All 5 callsites in enrichment.py and review_pipeline.py read `cli_output.get("result", "")`, but every one of them passes `--json-schema`. Per official docs (code.claude.com/docs/en/headless), when `--json-schema` is used, the structured output goes in the `structured_output` field and `result` is null.
  - Root cause: Our code was written assuming `result` always contains the answer. Documentation clearly states two different output contracts depending on whether `--json-schema` is present.
  - Affected locations (all read `result` but should read `structured_output`):
    (a) `enrichment.py:224` -- enrichment parsing
    (b) `enrichment.py:461` -- plan generation parsing
    (c) `review_pipeline.py:629` -- reviewer result parsing
    (d) `review_pipeline.py:639` -- raw_response builder
    (e) `review_pipeline.py:731` -- synthesis result parsing
  - Fix: When `--json-schema` is used, read `structured_output` instead of `result`. The value is already a parsed JSON object (not a string), so no `json.loads()` needed on it.
  - Rule: Always check CLI docs for output field names. When using `--json-schema`, the result is in `structured_output` (object), not `result` (string/null).
  - Tags: #claude-cli #json-schema #structured-output #parsing #root-cause

  21. Claude CLI stream-json event types -- parser coverage gaps
  - Context: T-P0-91 investigation. `_simplify_stream_event` in code_executor.py handles 5 event types (assistant, content_block_delta, tool_use, tool_result, result) but real CLI output includes 6+ types.
  - Real stream-json event types (from docs + captured output):
    (a) `system` -- subtypes: hook_started, hook_response, init (session/model info)
    (b) `assistant` -- message with content blocks (thinking + text)
    (c) `stream_event` -- token-level deltas (requires --include-partial-messages)
    (d) `result` -- final result (with structured_output when --json-schema used)
    (e) `rate_limit_event` -- rate limit info
    (f) `user` -- synthetic user messages (from hooks, isSynthetic=true)
  - Parser gap: `system`, `stream_event`, `rate_limit_event`, `user` are silently dropped (return None). This is mostly harmless for logging but means the JSONL files miss simplified representations.
  - Note: `content_block_delta` in our parser maps to `stream_event.event.delta` in actual CLI output. The event nesting is different than assumed.
  - Tags: #stream-json #event-types #parser #code-executor

  29. Atomic multi-field completion: guard ALL writes, not just the transition
  - Context: Review pipeline completion wrote 4 fields in separate DB sessions (set_review_result, set_review_status, set_review_lifecycle_state, update_status). Only 2 of 4 calls had expected_status guards. If the task moved between the guarded set_review_result (passes) and the guarded update_status, the unguarded set_review_status and set_review_lifecycle_state wrote orphaned metadata to a task no longer in REVIEW.
  - Root cause: Multi-field completion treated as sequential independent writes rather than one atomic operation. The TOCTOU fix (expected_status guards) was applied to some calls but not all, leaving windows for partial writes.
  - Fix: Created `finalize_review()` in TaskManager that bundles review_json, review_status, review_lifecycle_state, and status transition into one DB session with one precondition check. If the task has moved, ALL writes are skipped atomically.
  - Second fix: Moved `set_review_lifecycle_state(RUNNING)` to AFTER the pre-flight check, so a non-REVIEW task never gets lifecycle_state=RUNNING written to it.
  - Rule: When a background pipeline writes N fields on completion, bundle them into one method with one precondition check. N separate guarded calls still have N-1 TOCTOU windows between them.
  - Tags: #toctou #atomic #review-pipeline #multi-field #state-consistency

  22. `--verbose` is REQUIRED for stream-json to emit intermediate events
  - Context: T-P0-91 addendum. Official docs say: "Use `--output-format stream-json` with `--verbose` and `--include-partial-messages` to receive tokens as they're generated."
  - Our code_executor.py (line 271-280) uses `--output-format stream-json` and `--include-partial-messages` but does NOT include `--verbose`. Without `--verbose`, stream-json likely only emits the final `result` event -- no `system`, `assistant`, `stream_event` events appear during execution.
  - This explains why 47/57 log files were effectively empty: only a single result line was captured.
  - Fix: Add `--verbose` to the CLI args in code_executor.py (and review_pipeline/enrichment when they switch to stream-json).
  - Tags: #claude-cli #stream-json #verbose #missing-flag

  30. Claude Code hooks only guard Claude's tool calls -- use real git hooks for project-wide enforcement
  - Context: 4 commits had Chinese messages made directly from terminal, bypassing the PreToolUse commit_msg_guard.py hook entirely. The hook only intercepts Claude Code's Bash tool calls containing `git commit`.
  - Root cause: PreToolUse hooks are Claude Code-specific middleware. Any commit made outside Claude Code (terminal, IDE, scripts) is invisible to them.
  - Fix: Installed `.git/hooks/commit-msg` that validates format (`[T-P{0-3}-{N}]` prefix) and rejects CJK characters at the git level. Added `scripts/commit-msg` (tracked) + updated `scripts/install-hooks.sh` for re-setup after clone. Added Git Conventions section to CLAUDE.md with explicit format template.
  - Rule: Implicit expectations produce inconsistent output. If you want a specific format, enforce it at the lowest possible level (git hooks > Claude Code hooks > documentation). Template it explicitly (like PROGRESS.md's Exit Protocol format).
  - Rule: Hook error messages must be actionable: show what was received, what was expected, and the rules.
  - Tags: #git-hooks #commit-messages #enforcement #claude-code

  28. Async background tasks must verify preconditions (TOCTOU)
  - Context: Review pipeline runs asynchronously (LLM call takes seconds-minutes). Pipeline completes and tries `update_status(BACKLOG -> REVIEW_AUTO_APPROVED)` -- ValueError because the task was moved away from REVIEW during the async gap. Five interacting bugs: pipeline enqueued outside status guard, full DB overwrite from stale closure, no expected_status on completion transition, no replan status check, no pre-flight check.
  - Root cause: Time-Of-Check-To-Time-Of-Use -- the task status is checked at enqueue time but changes before the pipeline completes. Full-object overwrites (`task.model_copy(update=...)` + `update_task()`) silently revert concurrent status changes.
  - Fix: (1) Move pipeline enqueue inside status guard. (2) Replace full `update_task()` with targeted `set_review_result()` that only writes `review_json` + `expected_status` guard. (3) Add `expected_status=REVIEW` on completion `update_status()`. (4) Check status before replan enqueue. (5) Pre-flight status check before expensive LLM work.
  - Rules:
    (a) Any background task that modifies state on completion MUST verify the precondition still holds.
    (b) Use `expected_status` for atomic conditional transitions.
    (c) Never do full-object overwrites (`update_task`) from async closures -- use targeted field updates.
    (d) Targeted writes should also include status guards to prevent writing stale results to moved tasks.
    (e) Pre-flight checks reduce wasted LLM compute but don't eliminate races -- completion guards are the real safety net.
  - Related task: T-P0-164
  - Tags: #toctou #async #state-machine #review-pipeline #race-condition

  23. Investigation tasks: diff the working example against our code FIRST
  - Context: T-P0-91 had a user-provided working example with `--verbose` in the command. Our code_executor.py was missing `--verbose`. This was the most obvious finding but was missed on the first pass because the investigation focused on output format analysis (field names, event types) without first doing a mechanical flag-by-flag diff.
  - Root cause: "answer is in the problem statement" blindness. The investigation went straight to external docs and output format analysis, skipping the simplest check: compare working example flags vs our code flags.
  - Process fix -- **"Diff First" rule for CLI investigation tasks**:
    Step 1: Extract exact command from working example (user-provided or docs).
    Step 2: Extract exact command from our code.
    Step 3: Diff flags/args mechanically. Every delta is a finding.
    Step 4: THEN investigate output format, event types, field names, etc.
  - Generalized rule: When given a working reference and a broken implementation, the FIRST action is a mechanical diff between the two. Analysis of "why" comes after identifying "what's different."
  - Tags: #investigation #process #diff-first #cli #root-cause-analysis

  24. Always set limit= on asyncio.create_subprocess_exec with PIPE + readline()
  - Context: Claude CLI with --output-format stream-json can emit single JSON lines >64KB (large result events with review text, code diffs, tool output). asyncio.create_subprocess_exec defaults its internal StreamReader buffer to 64KB. When readline() encounters a line exceeding this limit, it raises LimitOverrunError (surfaced as ValueError: "Separator is found, but chunk is longer than limit"), crashing the pipeline.
  - Root cause: Three create_subprocess_exec calls (review_pipeline.py, enrichment.py, code_executor.py) never set the limit= parameter, inheriting the 64KB default.
  - Fix: Added SUBPROCESS_STREAM_LIMIT = 8 MiB constant to src/config.py and passed limit=SUBPROCESS_STREAM_LIMIT to all three call sites. 8 MiB is generous but memory impact is trivial (one buffer per subprocess).
  - Rule: Any asyncio.create_subprocess_exec using PIPE + readline() must set limit= to a value larger than the maximum expected line size. The 64KB default is a landmine for LLM streaming pipelines where single JSON lines can be arbitrarily large.
  - Tags: #asyncio #subprocess #streaming #buffer-limit #readline

  25. "Move to Completed" means reformat, not just relocate
  - Context: 15 tasks (T-P0-121 through T-P2-133) were moved from Active to Completed as bare `- T-XX-N: title` lines, skipping the required `#### [x] T-XX-N: title -- date` format with summary. Discovered during cleanup on 2026-03-09.
  - Root cause: Rapid autonomous batch completion (~15 tasks in 4 hours). Each session's exit protocol said "Move to Completed" but the actual edit just relocated the line without reformatting.
  - Fix: Reformatted all 15 entries with proper `#### [x]` heading, date, and one-line summary from PROGRESS.md.
  - Rule: Moving a task to Completed requires: (1) `#### [x] T-XX-N: Title -- YYYY-MM-DD` heading, (2) one-line summary of what was done. A bare bullet line is NOT a valid completed entry.
  - Tags: #tasks-md #formatting #exit-protocol #autonomous

### [2026-03-18] Stop hooks don't fire when Claude ends with pure text (no tool call)
- **Context**: A ruff F401 error slipped through because the session ended with a pure text response, and the Stop hook only fires after tool calls.
- **What went wrong / What I learned**: The Stop hook (lint_check.py) is not guaranteed to run on every session exit. If Claude's final response is pure text with no tool call, the hook infrastructure never triggers. Additionally, lint cache could produce false passes if files changed between cache write and next session.
- **Fix / Correct approach**: (1) Added `scripts/check.sh` as unified ruff+pytest runner. (2) Made running `bash scripts/check.sh` Step 0 in Exit Protocol -- primary defense. (3) Removed lint cache from lint_check.py so every Stop hook invocation runs fresh.
- **Tags**: #hooks #lint #ruff #exit-protocol #cache

### [2026-03-20] batch command doc/code mismatch caused silent data loss
- **Context**: `task_db.py batch` created tasks with empty title and description because batch call used nested `{"cmd": "add", "args": {"title": "..."}}` format but code reads flat keys: `cmd_dict.get("title", "")`.
- **What went wrong / What I learned**: Documentation showed `args` nesting format that never existed in implementation. Batch add had no validation -- empty title silently accepted. The `{"ok": true}` response gave no signal of data loss.
- **Fix / Correct approach**: (1) Support BOTH flat and nested-args formats. (2) Added title-non-empty validation. (3) Fixed docs to show correct flat format. Key takeaway: any CLI command returning success must validate required fields.
- **Tags**: #task-db #batch #validation #docs-code-mismatch

### [2026-03-15] SQLAlchemy create_all() does not ALTER existing tables
- **Context**: Added new column to model. Tests passed (in-memory DBs start fresh), but production crashed with missing column error.
- **What went wrong / What I learned**: `Base.metadata.create_all()` only creates NEW tables, never ALTER TABLE for existing ones. In-memory test DBs always start from scratch, hiding this gap.
- **Fix / Correct approach**: Added versioned auto-migration system that tracks applied versions. Each migration is idempotent. Added file-based migration tests and schema audit tests.
- **Tags**: #sqlalchemy #migration #sqlite #schema-drift

  27. Security hygiene: personal paths, accidental files, and local settings in git
  - Context: Security audit of all 252 commits found no real secret leaks, but 3 privacy/hygiene issues: (1) `orchestrator_config.yaml` had hardcoded `C:\Users\<username>\...` paths leaking user identity, (2) `=0.1.40` -- accidental pip output file tracked in git, (3) `.claude/settings.local.json` tracked, exposing personal WebFetch domain allowlist.
  - Root cause: No gitignore rules for pip output files (`=*`), local Claude settings, or private keys. `secret_guard.py` hook only checked .env files and API key patterns, not personal paths or sensitive file targets.
  - Fix: (1) Replaced hardcoded Windows paths with `~/` relative paths. (2) git rm'd accidental files + untracked local settings. (3) Expanded `secret_guard.py`: added PEM block and Windows user path patterns to SECRET_PATTERNS, renamed `_is_env_file` to `_is_sensitive_file` covering .env, *.cookie, *.pem, *.key, credentials*, settings.local.json. (4) Added .gitignore rules for all three categories. (5) Used `git filter-repo` to purge files and personal paths from all history.
  - Rule: Config files must use `~/` or env vars for paths, never absolute user paths. Hook checks should cover file targets (not just content patterns). gitignore should include `=*` (pip output), `*.pem`, `*.key`, and per-user settings files.
  - Tags: #security #gitignore #personal-paths #git-history #hooks

  26. Plan regeneration dirty state: backend must own state consistency
  - Context: After generating a plan, if the user triggered regeneration (or the async pipeline completed out of order), the UI displayed stale plan data -- old proposed tasks, wrong status badges, or a "ready" plan from a previous generation overlaying a new "generating" state. The repair script (T-P0-138) found 151 rows with inconsistent plan state in production data.
  - Root cause: No state machine governed plan_status transitions. Each call site (generate-plan, reject-plan, confirm-tasks, replan, zombie reset) independently set fields like plan_status, plan_json, and description with ad-hoc clearing logic. Field invariants (e.g., plan_status="none" implies plan_json=NULL) were not enforced. On the frontend, each component (TaskCard, TaskCardPopover, PlanReviewPanel) had its own inline field-clearing logic on plan status changes, leading to inconsistent partial updates. Async SSE events from a previous generation could overwrite the current state because there was no way to distinguish which generation an event belonged to.
  - Fix (T-P0-134 backend + T-P0-135 frontend + T-P0-124 UI):
    (a) Backend state machine: `VALID_PLAN_TRANSITIONS` dict + `set_plan_state()` single entry point in TaskManager. Each state (none, generating, ready, failed, decomposed) has enforced field invariants (e.g., NONE clears plan_json/description/has_proposed_tasks; READY requires plan_json+description and computes has_proposed_tasks). Invalid transitions raise ValueError.
    (b) Generation ID: `plan_generation_id` (uuid4) assigned at generation start, included in all SSE events. Frontend SSE handler compares incoming generation_id against the task's current plan_generation_id and drops stale events.
    (c) Shared frontend utility: `planStatePatch()` in `frontend/src/utils/planState.ts` returns the correct partial Task update for each plan status, replacing scattered inline clearing logic across 3+ components.
  - Principles:
    (1) Backend owns state consistency -- the frontend should never compute derived state or enforce invariants. A single `set_plan_state()` entry point with per-state field rules eliminates scattered ad-hoc updates.
    (2) Async pipelines need generation IDs -- any long-running async operation (plan generation, review pipeline) that emits events must tag them with a generation/attempt ID so consumers can discard stale results.
    (3) Shared utilities over inline logic -- when 3+ components need the same state-clearing logic, extract it to a shared utility and import it. Inline duplication guarantees drift.
  - Related tasks: T-P0-134, T-P0-135, T-P0-138, T-P0-124
  - Tags: #state-machine #dirty-state #plan-generation #async #generation-id #frontend-consistency

### [2026-03-20] [PROPAGATED] Claude Code Bash tool ignores .bashrc
- **Source**: MLInterviewPrep (propagated via cross-project review 2026-03-21)
- **What I learned**: The Bash tool runs non-login, non-interactive shells. `.bashrc` and `.bash_profile` are NOT sourced. The only way to inject env vars is `$CLAUDE_ENV_FILE` (written by a SessionStart bash hook). All hook commands in `settings.json` must use absolute paths.
- **Tags**: #windows #bash-tool #path #hooks #claude-code #propagated
