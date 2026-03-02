# /collect-input -- Human Input Collection

Check status, guide input, validate, and unblock tasks that require human-provided files.

## Usage

```
/collect-input                    # Show status of all input tasks
/collect-input status             # Same as above
/collect-input T-XX-N             # Guided walkthrough for a specific task
/collect-input validate T-XX-N    # Validate files for a specific task
/collect-input validate all       # Validate all tasks marked complete
/collect-input unblock T-XX-N     # Remove [NEEDS-INPUT] tag after validation passes
```

## Steps

### Status (default or `status`)

1. Read `docs/human_input/README.md`
2. For each task section, parse the `**Status**:` line:
   - `[ ]` = Not started
   - `[x]` = Complete (needs validation)
3. For each task, check if target files exist
4. Display summary table:

```
## Human Input Status

| Task | Description | Status | Files Found |
|------|------------|--------|-------------|
| T-XX-1 | Description | Not started | 0/N minimum |
| T-XX-2 | Description | Complete | Present |

Run `/collect-input <task-id>` for guidance on a specific task.
```

### Guided Walkthrough (`T-XX-N`)

1. Read the per-task spec file from `docs/human_input/`
2. Check which target files already exist
3. Show what's present and what's missing
4. Display the key requirements from the spec
5. Point to the template file if one exists

### Validate (`validate T-XX-N`, `validate all`)

1. Spawn the `input-reviewer` agent via the Task tool with `subagent_type: "general-purpose"`:
   - Pass the task ID(s) to validate
   - Agent reads `.claude/agents/input-reviewer.md` for validation logic
   - Agent performs all checks and returns [PASS]/[FAIL]/[WARN] per check
2. Report the agent's findings
3. If all checks pass: suggest running `/collect-input unblock <task-id>`
4. If any check fails: show specific failures with fix instructions

### Unblock (`unblock T-XX-N`)

1. First, run validation (same as `validate` above)
2. If validation fails: report failures, do NOT unblock
3. If validation passes:
   a. Update `docs/human_input/README.md`: change `[ ]` to `[x]` for the task's Status line
   b. Update `TASKS.md`: remove the `[NEEDS-INPUT: ...]` tag from the task entry
   c. Report success

## Notes

- Never unblock a task without passing validation first
- Template files are format references, not real data
- No emoji in output -- use [PASS], [FAIL], [WARN], [DONE] text tags
