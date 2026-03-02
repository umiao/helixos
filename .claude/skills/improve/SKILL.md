# /improve -- Idle-Time Improvement Runner

Scan the codebase for quality improvements and generate actionable tasks.

## Steps

1. **Lint sweep**: Run `ruff check src/ tests/` and report any new violations
2. **Type check** (if mypy is available): Run `python -m mypy src/ --ignore-missing-imports` and report errors
3. **Test coverage gaps**: Compare files in `src/` against test files in `tests/` to identify untested modules
4. **Dead code scan**: Look for:
   - Unused imports (ruff handles this, but double-check)
   - Functions/classes defined but never referenced
   - Commented-out code blocks
5. **Dependency check**: Read `pyproject.toml` or `requirements.txt` and flag any known issues
6. **Spawn refactor-advisor agent**: Use the Task tool with `subagent_type: "general-purpose"` and `model: "sonnet"` to get a deeper analysis
7. **Generate report and tasks**:

```
## Improvement Report

### Lint
- Status: [DONE] Clean / [FAIL] N violations
- Details: ...

### Type Checking
- Status: [DONE] Clean / [WARN] N errors
- Details: ...

### Test Coverage Gaps
- Untested modules: list
- Suggested tests: list

### Dead Code
- Items found: list

### Dependency Health
- Status: [DONE] OK / [WARN] Issues found

### New TASKS.md Entries
(Auto-generated task entries for discovered issues)
```

8. **Update TASKS.md**: Add any new tasks discovered (under appropriate priority level), with clear acceptance criteria

## Notes
- This skill is designed for idle time -- run it when there's no active feature work
- It doesn't auto-fix anything, just reports and creates tasks
- Pair with `/review` for a complete quality picture
