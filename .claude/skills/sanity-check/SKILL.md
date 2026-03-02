# /sanity-check -- Pre-Exit Quality Gate

Manually trigger the full exit gate checks without stopping the session.

## Steps

1. **Lint Check**: Run `ruff check src/ tests/` and report any violations
2. **Test Suite**: Run `python -m pytest tests/ -x -q --tb=short` and report pass/fail
3. **PROGRESS.md Check**: Verify that PROGRESS.md has a recent entry for the current session
   - Read the last entry and check if its date matches today
   - If missing, remind to add one before stopping
4. **TASKS.md Check**: Verify TASKS.md reflects current work status
   - Check that the current task is properly tracked (In Progress / Completed)
   - If stale, remind to update
5. **Output Summary**:

```
## Sanity Check Results
- **Lint**: [DONE] Clean / [FAIL] N violations
- **Tests**: [DONE] N passed / [FAIL] N failed
- **PROGRESS.md**: [DONE] Up to date / [WARN] Needs update
- **TASKS.md**: [DONE] Current / [WARN] Needs update
- **Overall**: READY TO STOP / NEEDS ATTENTION
```

## Notes
- This performs the same checks as the Stop hooks, but without blocking
- Useful mid-session to verify you're on track before wrapping up
