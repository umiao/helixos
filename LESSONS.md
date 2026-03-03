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