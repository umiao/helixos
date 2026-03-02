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