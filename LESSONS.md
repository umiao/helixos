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
