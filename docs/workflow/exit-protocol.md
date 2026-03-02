# Exit Protocol (Reference)

> The Stop hook enforces these checks automatically. This file documents the full
> protocol for reference. You do not need to memorize this -- the hook will tell you
> what's missing.

## 1. Sanity Check
- What are the output files or artifacts of this session? Do they exist?
- Run or compile the code -- does it work without errors?
- Inspect output file headers (`head -20`) -- does the format look right?
- If there are tests, run them. If there aren't, consider if a quick smoke test is warranted.

## 2. Update PROGRESS.md
Append an entry at the bottom using this exact format:
```
## YYYY-MM-DD HH:MM -- [TASK-XXX] Brief Title
- **What I did**: 1-3 sentences on concrete actions taken
- **Deliverables**: List of files created/modified
- **Sanity check result**: What I verified and the outcome
- **Status**: [DONE] Done / [PARTIAL] Partial (what remains) / [BLOCKED] Blocked (why)
- **Request**: Cross off TASK-XXX / Move TASK-XXX to In Progress / No change
```
Keep it brief. 5-8 lines max.

## 3. Update TASKS.md
- Done: move to "Completed Tasks" with `[x]` and date
- Partial: leave in "In Progress", add a brief note on what remains
- Blocked: move to "Blocked" with reason
- New work discovered: add new task entries

## 4. Update LESSONS.md (only when applicable)
Only log if: bug >10 min to debug, surprising behavior, effective pattern, non-obvious gotcha.

## 5. Final Self-Check
> "If a new Claude session picked up this project tomorrow with only these files for
> context, would it know exactly where things stand?"
