# Self-Hosting Guardrails Design Document

> **Status**: DRAFT
> **Author**: HelixOS autonomous session
> **Date**: 2026-03-02
> **Task**: T-P3-8

## 1. Problem Statement

HelixOS is registered as project P0 in its own `orchestrator_config.yaml`. The
scheduler can pick up P0 tasks, spawn a Claude CLI executor inside the HelixOS
repo, and auto-commit results -- meaning HelixOS can modify its own source code,
tests, configuration, and database schema.

Without guardrails, self-modification introduces risks:

- **Crash loops**: A bad code change breaks the scheduler, which cannot recover
  because the broken code is now committed and running.
- **Configuration corruption**: A task edits `orchestrator_config.yaml` or DB
  schema, causing the running server to malfunction.
- **Concurrent conflicts**: Although per-project concurrency is capped at 1,
  auto-commit races or manual edits during execution can still create conflicts.
- **Recursive execution**: A Claude CLI agent running inside HelixOS spawns
  another HelixOS task, creating unbounded nesting.
- **Silent breakage**: Self-modifications that pass within the agent's session
  may break integration tests or other subsystems discovered only later.

This document defines safety boundaries, isolation mechanisms, and lifecycle
controls for self-hosting.

---

## 2. Design Principles

1. **Isolation by default** -- Self-modification never touches the live codebase
   directly. All changes go through a git worktree branch.
2. **Validation before merge** -- No branch merges to main without a passing
   test suite (`pytest`).
3. **Human gate for unsafe changes** -- Code and test changes are "safe"; DB
   schema, config, and scheduler changes require human approval.
4. **No auto-restart** -- HelixOS never restarts itself. Restart is always
   human-triggered to prevent crash loops.
5. **Auditability** -- Every self-modification attempt is logged with full
   context (task ID, branch, diff summary, test result).

---

## 3. Worker Isolation via Git Worktree

### 3.1 Current behavior (no guardrails)

Today, `CodeExecutor` runs `claude -p "..."` with `cwd=repo_path`. For P0, that
is the live HelixOS checkout. The agent writes directly to the same files the
running server uses.

### 3.2 Proposed behavior

When the scheduler dispatches a P0 (self-hosted) task:

1. **Create a worktree branch**:
   ```
   git worktree add .worktrees/<task-id> -b self/<task-id> HEAD
   ```
   The `.worktrees/` directory lives inside the HelixOS repo root but is
   git-ignored.

2. **Set executor cwd** to the worktree path instead of the main repo:
   ```
   cwd = repo_path / ".worktrees" / task_id
   ```

3. **Execute the task** in the worktree. The agent reads/writes files in the
   isolated copy. The live server is unaffected.

4. **On completion**, the worktree branch (`self/<task-id>`) contains the
   agent's changes as commits.

5. **Validation gate** (Section 4) runs against the worktree before any merge.

6. **Cleanup**: After merge or rejection, remove the worktree:
   ```
   git worktree remove .worktrees/<task-id>
   git branch -d self/<task-id>   # only if merged
   ```

### 3.3 Configuration

Add a new field to `ProjectConfig`:

```python
self_hosted: bool = False  # True only for P0
```

When `self_hosted=True`, the scheduler uses the worktree-based executor path.
Projects with `self_hosted=False` (the default) are unaffected.

### 3.4 Gitignore

Add to `.gitignore`:

```
.worktrees/
```

---

## 4. Commit Serialization and Validation

### 4.1 Validation pipeline

After the executor finishes in the worktree, and before merging to main:

```
[Agent completes in worktree]
        |
        v
  Run pytest in worktree     ---FAIL---> Mark task FAILED
        |                                 Log test output
      PASS                                Keep branch for debugging
        |
        v
  Run ruff check in worktree ---FAIL---> Mark task FAILED
        |
      PASS
        |
        v
  Safety classification      ---UNSAFE--> Queue for human review
        |                                  (see Section 6)
      SAFE
        |
        v
  Fast-forward merge to main
        |
        v
  Cleanup worktree + branch
```

### 4.2 Test execution

Tests run in the worktree directory with the worktree's virtualenv (or the
shared one if not isolated):

```bash
cd .worktrees/<task-id>
python -m pytest --tb=short -q 2>&1
```

The full test output is captured and stored via `HistoryWriter` as an execution
log entry with `level=INFO` or `level=ERROR`.

### 4.3 Merge strategy

Use fast-forward merge only to keep history linear:

```bash
git checkout main
git merge --ff-only self/<task-id>
```

If fast-forward is not possible (main has diverged), the merge is rejected and
the task is marked FAILED with reason "main diverged during execution". The
branch is kept for manual resolution.

### 4.4 Concurrent merge protection

Even though per-project concurrency is 1 (only one P0 task runs at a time),
manual commits to main during execution can cause merge conflicts. The
fast-forward-only policy handles this: if main has moved, the merge fails
safely.

---

## 5. Log Isolation

### 5.1 Separate log stream

Self-hosted task execution logs are tagged with `source=self-host` in the
`execution_logs` table. This allows filtering in both the API and the frontend:

```sql
SELECT * FROM execution_logs
WHERE task_id = :task_id AND source = 'self-host';
```

### 5.2 Additional log entries for self-host lifecycle

Beyond standard execution logs, the self-host pipeline writes:

| Event                    | Level | Message                                        |
|--------------------------|-------|------------------------------------------------|
| Worktree created         | INFO  | `Worktree created at .worktrees/<task-id>`     |
| Validation started       | INFO  | `Running pytest in worktree`                   |
| Validation passed        | INFO  | `pytest passed: N tests in Xs`                 |
| Validation failed        | ERROR | `pytest failed: N failures (output truncated)` |
| Safety classification    | INFO  | `Changes classified as SAFE/UNSAFE`            |
| Merge completed          | INFO  | `Merged self/<task-id> to main (ff)`           |
| Merge rejected           | WARN  | `Fast-forward not possible, manual merge needed` |
| Worktree cleaned up      | INFO  | `Removed worktree .worktrees/<task-id>`        |
| Human gate triggered     | WARN  | `Unsafe changes detected, awaiting human review` |

### 5.3 Frontend display

The ExecutionLog panel already supports `source` tags per T-P3-6b. Self-host
logs display with a `[SELF-HOST]` tag to distinguish them from standard
execution logs.

---

## 6. Safety Boundary Classification

### 6.1 Safe vs. unsafe changes

After the agent completes and tests pass, the diff is classified:

| Category | File patterns | Classification |
|----------|--------------|----------------|
| Source code | `src/**/*.py` (excluding below) | SAFE |
| Tests | `tests/**/*.py` | SAFE |
| Frontend source | `frontend/src/**` | SAFE |
| Documentation | `docs/**`, `*.md` (not CLAUDE.md) | SAFE |
| DB schema | `src/db.py` (alembic migrations) | UNSAFE |
| Scheduler logic | `src/scheduler.py` | UNSAFE |
| Executor logic | `src/executors/**` | UNSAFE |
| Configuration | `orchestrator_config.yaml` | UNSAFE |
| Project instructions | `CLAUDE.md` | UNSAFE |
| Git hooks | `.claude/hooks/**` | UNSAFE |
| Dependencies | `requirements.txt`, `pyproject.toml` | UNSAFE |
| Frontend config | `package.json`, `vite.config.ts`, `tsconfig*.json` | UNSAFE |

### 6.2 Classification algorithm

```python
UNSAFE_PATTERNS: list[str] = [
    "src/db.py",
    "src/scheduler.py",
    "src/executors/*",
    "orchestrator_config.yaml",
    "CLAUDE.md",
    ".claude/hooks/*",
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "vite.config.ts",
    "tsconfig*.json",
]

def classify_diff(changed_files: list[str]) -> str:
    """Return 'SAFE' or 'UNSAFE' based on changed file paths."""
    for f in changed_files:
        if any(fnmatch(f, pattern) for pattern in UNSAFE_PATTERNS):
            return "UNSAFE"
    return "SAFE"
```

### 6.3 Human gate for unsafe changes

When changes are classified as UNSAFE:

1. Task status transitions to `REVIEW_NEEDS_HUMAN` (existing state).
2. The ReviewPanel shows the diff summary and classification reason.
3. A toast notification alerts the user: "Self-host task requires human review".
4. The human can:
   - **Approve**: Merge proceeds (fast-forward to main).
   - **Reject**: Branch is kept, task marked FAILED.
   - **Defer**: Task stays in REVIEW_NEEDS_HUMAN until decided.

The worktree branch is preserved until the human decision is made.

---

## 7. Restart Mechanism

### 7.1 No auto-restart

HelixOS never restarts itself. This is a hard constraint to prevent:

- **Crash loops**: Bad code change -> server crash -> auto-restart -> crash again.
- **State corruption**: Restart during DB migration or partial write.
- **Infinite regress**: A self-modification task that includes "restart server"
  as part of its execution.

### 7.2 Human-triggered restart

After a self-modification merge to main, the user must manually restart:

```powershell
# PowerShell (use run_server.py for correct Windows event loop)
python scripts/run_server.py
```

```bash
# bash
uvicorn src.api:app --reload
```

The `--reload` flag (development mode) will auto-detect file changes, but this
is a development convenience, not a production restart mechanism.

### 7.3 Restart notification

After a successful self-host merge, the system emits an SSE event:

```json
{
  "type": "self_host_merge",
  "data": {
    "task_id": "T-P3-8",
    "branch": "self/T-P3-8",
    "changed_files": 5,
    "message": "Self-host changes merged. Manual restart recommended."
  }
}
```

The frontend displays a persistent banner:
> "HelixOS code updated. Restart the server to apply changes."

The banner persists until the server is restarted (detected by SSE reconnect
with a new server boot timestamp).

---

## 8. State Diagram: Self-Modification Lifecycle

```
                    QUEUED
                      |
                      v
              Create Worktree
                      |
                      v
               RUNNING (in worktree)
                      |
              +-------+-------+
              |               |
           Success         Failure
              |               |
              v               v
         Run pytest       FAILED
              |           (keep branch
         +----+----+       for debug)
         |         |
       PASS      FAIL
         |         |
         v         v
    Classify     FAILED
    changes      (log test
         |        output)
    +----+----+
    |         |
   SAFE    UNSAFE
    |         |
    v         v
  FF-merge  REVIEW_NEEDS_HUMAN
  to main        |
    |       +----+----+
    v       |         |
  DONE   Approve   Reject
    |       |         |
    |       v         v
    |    FF-merge   FAILED
    |    to main    (keep branch)
    |       |
    v       v
  Cleanup worktree + branch
    |
    v
  Emit self_host_merge SSE event
  ("Restart recommended" banner)
```

---

## 9. Recursive Execution Prevention

### 9.1 Problem

When a Claude CLI agent executes in the HelixOS worktree, it could
theoretically:

- Read TASKS.md and attempt to run the scheduler
- Spawn nested `claude` processes that trigger more HelixOS tasks
- Modify the scheduler code to auto-dispatch additional tasks

### 9.2 Mitigation

1. **Prompt boundary**: The CodeExecutor prompt explicitly scopes the agent to
   the current task. It does not include instructions to run the scheduler or
   dispatch other tasks.

2. **Environment isolation**: The worktree does not have access to the running
   server's state DB or port. The agent cannot call HelixOS API endpoints unless
   it discovers them independently.

3. **Per-project concurrency**: Even if a nested task were somehow queued, the
   per-project limit of 1 prevents it from executing while the parent task runs.

4. **Allowed tools restriction**: The executor uses `--allowedTools
   Bash,Read,Write,Edit,MultiTool`. The agent cannot use HelixOS-specific tools
   or API calls.

5. **Future enhancement**: Add `HELIXOS_SELF_HOST=1` environment variable to
   self-host executor sessions. HelixOS startup code checks for this variable
   and refuses to start a server, preventing nested server instances.

---

## 10. Implementation Phases

### Phase 1: Worktree isolation (M complexity)

- Add `self_hosted` field to `ProjectConfig`
- Implement `WorktreeManager` (create, cleanup, list worktrees)
- Modify `CodeExecutor` to use worktree cwd when `self_hosted=True`
- Add `.worktrees/` to `.gitignore`
- Tests: worktree creation, executor cwd override, cleanup

### Phase 2: Validation gate (M complexity)

- Implement post-execution pytest runner in worktree
- Implement ruff check in worktree
- Wire validation into scheduler post-execution hook
- Fail task if validation fails (keep branch)
- Tests: pass/fail scenarios, test output capture

### Phase 3: Safety classification + human gate (S complexity)

- Implement `classify_diff()` with configurable unsafe patterns
- Route UNSAFE results to `REVIEW_NEEDS_HUMAN`
- Add diff summary to ReviewPanel
- Tests: pattern matching, classification edge cases

### Phase 4: Merge + notification (S complexity)

- Implement fast-forward merge with conflict detection
- Add `self_host_merge` SSE event
- Add restart banner in frontend
- Tests: merge success, diverged-main rejection, SSE emission

### Phase 5: Log isolation + tagging (S complexity)

- Add `source` field to execution log entries (or reuse existing)
- Tag self-host logs with `[SELF-HOST]`
- Frontend filter for self-host logs
- Tests: log tagging, filter queries

---

## 11. Open Questions

1. **Shared virtualenv vs. isolated**: Should the worktree use the same
   virtualenv as the main checkout, or create its own? Shared is simpler but
   risks dependency conflicts if `requirements.txt` changes.
   **Recommendation**: Shared virtualenv initially. If a task changes
   `requirements.txt` (classified UNSAFE), the human reviewer can manually
   install before restart.

2. **Worktree retention policy**: How long to keep failed worktree branches for
   debugging? **Recommendation**: Keep for 7 days, then prune via a scheduled
   cleanup.

3. **Multiple self-hosted projects**: Could other projects also be self-hosted
   (e.g., a plugin system)? **Recommendation**: Design for one self-hosted
   project (P0) initially. The `self_hosted` flag per project allows future
   extension.

4. **Rollback mechanism**: If a merged self-host change breaks production, what
   is the rollback path? **Recommendation**: `git revert HEAD` on main,
   followed by manual restart. Document in QUICKSTART.md.

---

## 12. Security Considerations

- **No credential exposure**: Self-host tasks inherit only the `env_keys`
  configured for P0. Currently P0 has no `env_keys`, so no secrets are injected.
- **Git worktree permissions**: Worktrees inherit the parent repo's permissions.
  No elevation occurs.
- **Commit signing**: If the repo requires signed commits, the worktree merge
  must also be signed. This is inherited from git config.
- **File system access**: The Claude CLI agent in the worktree has full
  filesystem access via Bash tool. This is acceptable because HelixOS is a
  single-user local system (per PRD Section 1).

---

## 13. References

- HelixOS PRD v0.3, Section 6 (Configuration), Section 7 (Executor), Section 8
  (Scheduler)
- `src/scheduler.py` -- tick loop, per-project concurrency, auto-commit hook
- `src/executors/code_executor.py` -- Claude CLI subprocess execution
- `src/git_ops.py` -- auto-commit with staged safety check
- `src/config.py` -- ProjectConfig, OrchestratorSettings
- Git worktree documentation: `git worktree --help`
