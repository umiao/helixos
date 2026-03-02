# Autonomous Mode Rules

> Loaded by session_context.py when mode=autonomous in session_state.json.
> These rules override the default single-task workflow.

## One Task Per Session
Each session works on **exactly one task** from TASKS.md. Pick the highest-priority
unblocked task, complete it, then **stop**. The external orchestrator
(`scripts/autonomous_run.sh`) handles launching the next session with fresh context.

## Git Commit Per Task
After completing the task (all acceptance criteria met, tests pass, ruff clean):
1. Stage all changed files relevant to the task.
2. Create a git commit with message format: `[T-XX-N] Brief description of what was done`
3. This gives the user a clean per-task history to review and revert individually.

## Per-Task Retries
Each task gets a maximum of **2 attempts**. If a task fails on the 2nd attempt:
1. Mark it as BLOCKED in TASKS.md with the failure reason.
2. Log the failure to LESSONS.md.
3. Update `.claude/session_state.json` to record the skip.
4. Stop the session.

## State Tracking
Write `.claude/session_state.json` before stopping. Schema:
```json
{
  "mode": "autonomous",
  "current_task": "T-P0-1",
  "retry_count": 0,
  "max_retries": 2,
  "completed_this_session": ["T-P0-5"],
  "skipped_tasks": [{"task": "T-P0-1", "reason": "selenium timeout", "attempts": 2}],
  "all_done": false
}
```

## Continuing Partial Tasks
If TASKS.md shows a task "In Progress" with a PROGRESS.md entry showing [PARTIAL]:
1. Read the partial entry to understand what was done and what remains
2. Check git log for any WIP commits from the previous session
3. Continue from that point -- do NOT restart from scratch
4. For L-complexity tasks, create WIP checkpoint commits every ~100 turns:
   `[T-XX-N WIP] description of partial progress`

## Dependency Enforcement
Never start a task whose dependencies are not yet completed. Check TASKS.md
"Depends on" field and skip tasks with unmet dependencies.

## External Input Requirements
Tasks tagged with `[NEEDS-INPUT]` in TASKS.md require human preparation before
autonomous execution. Treat them as blocked. Skip and log in session_state.json.

## Stop Conditions
Stop the session when ANY of these is true:
- Task completed successfully (commit done, PROGRESS.md/TASKS.md updated).
- Task failed twice (marked BLOCKED, logged to LESSONS.md).
- No unblocked tasks remain -- set `"all_done": true` in session_state.json.
- User says stop (Ctrl+C or explicit instruction).
