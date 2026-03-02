# /e2e-test -- Run Full Test Suite

Run the full pytest suite and report structured results.

## Steps

1. Run `python -m pytest tests/ -v --tb=short` to execute all tests
2. Parse the output to extract:
   - Total tests run
   - Passed / Failed / Skipped / Error counts
   - Names of failing tests with short tracebacks
3. Report results in this format:

```
## Test Report
- **Total**: N tests
- **Passed**: N [DONE]
- **Failed**: N [FAIL] (list each)
- **Skipped**: N [SKIP]
- **Errors**: N [ERROR] (list each)
```

4. If any tests fail, read the failing test file and the source it tests to suggest a fix
5. If all tests pass, confirm with a status line

## Notes
- Always use `--tb=short` for concise tracebacks
- Use `-x` flag if you want to stop at first failure (optional, not default)
- Run from the project root directory
