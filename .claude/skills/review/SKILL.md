# /review -- Code Review

Trigger a code review on recent changes using the reviewer agent.

## Steps

1. Run `git diff --stat` to see which files changed recently
2. If no uncommitted changes, use `git diff HEAD~1` to review the last commit
3. Spawn the **reviewer** agent (`.claude/agents/reviewer.md`) using the Task tool with `subagent_type: "general-purpose"` and `model: "sonnet"`:
   - Pass the diff output and instruct it to review against CLAUDE.md invariants
   - The agent will check: type hints, docstrings, secrets, test coverage
4. Report the agent's findings to the user in structured format:

```
## Code Review Results
### Critical [FAIL]
### Warning [WARN]
### Suggestion [TIP]
### Verdict: APPROVE / NEEDS CHANGES
```

5. If there are Critical findings, list specific actions needed to resolve them

## Arguments
- No arguments: reviews uncommitted changes (or last commit if clean)
- `HEAD~N`: reviews the last N commits
- `<branch>`: reviews diff against specified branch

## Notes
- This is a non-blocking review -- it suggests but doesn't auto-fix
- Use `/e2e-test` after fixing any issues found by review
