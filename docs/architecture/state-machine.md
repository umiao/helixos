# State Machine Documentation

**Last updated**: 2026-03-06
**Source of truth**: `src/models.py`, `src/task_manager.py`

---

## 1. Task Status State Machine

Defined in `src/models.py:TaskStatus` and `src/task_manager.py:VALID_TRANSITIONS`.

### States

| State | Description |
|-------|-------------|
| `BACKLOG` | Task is planned but not yet submitted for review or execution. |
| `REVIEW` | Task is undergoing automated code review (review pipeline running). |
| `REVIEW_AUTO_APPROVED` | Review pipeline approved the task automatically (consensus >= threshold). |
| `REVIEW_NEEDS_HUMAN` | Review pipeline flagged the task for human decision (rejected or partial). |
| `QUEUED` | Task is approved and waiting for scheduler to pick it up. |
| `RUNNING` | Task is actively being executed by the scheduler/code executor. |
| `DONE` | Execution completed successfully. |
| `FAILED` | Execution failed (error, timeout, or crash recovery). |
| `BLOCKED` | Task cannot proceed due to unmet dependencies or manual block. |

### Transition Diagram

```
                          +------------------+
                          |     BACKLOG      |
                          +--------+---------+
                                   |
                          +--------v---------+
              +---------->|      REVIEW      |<-----------+
              |           +--------+---------+            |
              |                    |                      |
              |        +-----------+-----------+          |
              |        |                       |          |
              |  +-----v----------+  +---------v------+  |
              |  | REVIEW_AUTO_   |  | REVIEW_NEEDS_  |  |
              |  | APPROVED       |  | HUMAN          |--+
              |  +-----+----------+  +---------+------+
              |        |                       |
              |        +-----------+-----------+
              |                    |
              |           +--------v---------+
              +-----------|      QUEUED      |<-----------+
              |           +--------+---------+            |
              |                    |                      |
              |           +--------v---------+   +--------+-------+
              |           |     RUNNING      |-->|    BLOCKED      |
              |           +--------+---------+   +--------+-------+
              |                    |                      |
              |           +--------+---------+            |
              |           |                  |            |
              |     +-----v-----+   +--------v---+       |
              +-----|    DONE   |   |   FAILED   |-------+
                    +-----------+   +------------+
```

### Transition Table

| From | To | Trigger | Side-effects |
|------|----|---------|--------------|
| BACKLOG | REVIEW | User drags to REVIEW column (API PATCH) | Review pipeline starts (`_enqueue_review_pipeline`). Blocked if plan is invalid (< 20 chars). |
| BACKLOG | QUEUED | User drags to QUEUED (gate OFF only) | Blocked if review gate is enabled. |
| REVIEW | REVIEW_AUTO_APPROVED | Review pipeline consensus >= threshold | Set by `_run_review_bg` after pipeline completes. |
| REVIEW | REVIEW_NEEDS_HUMAN | Review pipeline rejects or partial result | Set by `_run_review_bg` after pipeline completes. |
| REVIEW | BACKLOG | User drags back to BACKLOG | Cleanup: reset `review_status`, `review_lifecycle_state`, `execution_epoch_id`. |
| REVIEW_AUTO_APPROVED | QUEUED | Automatic or user-initiated | Task enters scheduler queue. |
| REVIEW_AUTO_APPROVED | BACKLOG | User drags back | Cleanup: reset review/execution fields. |
| REVIEW_NEEDS_HUMAN | QUEUED | Human approves via `/review/decide` | Task enters scheduler queue. |
| REVIEW_NEEDS_HUMAN | BACKLOG | User drags back | Cleanup: reset review/execution fields. |
| REVIEW_NEEDS_HUMAN | REVIEW | User retries review | Review pipeline re-runs. |
| QUEUED | RUNNING | Scheduler dispatches task | Scheduler sets `started_at`, assigns `execution_epoch_id`, starts code executor. |
| QUEUED | BLOCKED | Scheduler detects unmet dependencies | No execution. |
| QUEUED | BACKLOG | User drags back | Cleanup: reset review/execution fields. |
| QUEUED | REVIEW | User drags to REVIEW | Review pipeline starts. |
| RUNNING | DONE | Executor completes successfully | Scheduler sets `completed_at`. Epoch ID verified before finalization. |
| RUNNING | FAILED | Executor error, timeout, or crash | Scheduler sets error summary. Epoch ID verified before finalization. |
| FAILED | QUEUED | User retries task | Re-enters scheduler queue. |
| FAILED | BLOCKED | User blocks task | No execution. |
| FAILED | BACKLOG | User drags back | Cleanup: reset review/execution fields. |
| DONE | BACKLOG | User drags back (re-plan) | Cleanup: reset `completed_at`, review/execution fields. |
| DONE | QUEUED | User re-queues | Re-enters scheduler queue. |
| BLOCKED | QUEUED | User unblocks task | Re-enters scheduler queue. |
| BLOCKED | BACKLOG | User drags back | Cleanup. |

### Guards and Gates

1. **Review gate** (`review_gate_enabled`): When ON, `BACKLOG -> QUEUED` is blocked. Tasks must go through REVIEW first.
2. **Plan validity gate**: `BACKLOG -> REVIEW` is blocked if the task description is missing or < 20 characters.
3. **Optimistic concurrency lock**: All transitions support `expected_updated_at` to prevent TOCTOU races.
4. **Epoch ID guard**: `RUNNING -> DONE/FAILED` verifies `execution_epoch_id` matches to prevent stale scheduler finalization.
5. **RUNNING is locked**: Users cannot drag tasks out of RUNNING. Only the scheduler can transition RUNNING tasks (to DONE or FAILED).

### Backward Transition Cleanup (`_cleanup_on_backward`)

When a task moves to BACKLOG, the following fields are reset:

- `completed_at` = None
- `review_status` = "idle"
- `review_lifecycle_state` = "not_started"
- `execution_epoch_id` = None
- `execution_json.started_at` = removed
- `execution_json.error_summary` = removed

---

## 2. Review Lifecycle State Machine

Defined in `src/models.py:ReviewLifecycleState` and `REVIEW_LIFECYCLE_TRANSITIONS`.

This state machine tracks the internal lifecycle of the review pipeline **within** a single review session. It is orthogonal to the task status -- a task in `REVIEW` status will have a review lifecycle progressing through these states.

### States

| State | Description |
|-------|-------------|
| `NOT_STARTED` | No review pipeline has run (or was reset after backward drag). |
| `RUNNING` | Review pipeline is actively executing (calling LLM reviewers). |
| `APPROVED` | All reviewers reached consensus >= threshold. Terminal (success). |
| `PARTIAL` | Pipeline was interrupted with some reviewers completed. Retryable. |
| `FAILED` | Pipeline encountered an error or timeout. Retryable. |
| `REJECTED_SINGLE` | A single reviewer rejected the task. Retryable after feedback. |
| `REJECTED_CONSENSUS` | Multiple reviewers reached rejection consensus. Retryable after feedback. |

### Transition Diagram

```
+-------------+
| NOT_STARTED |
+------+------+
       |
       | pipeline starts
       v
+------+------+
|   RUNNING   |------+------+------+------+
+------+------+      |      |      |      |
       |             |      |      |      |
       v             v      v      v      v
  +---------+  +--------+ +------+ +----------+ +-------------+
  | APPROVED|  | PARTIAL| | FAILED | REJECTED_ | | REJECTED_   |
  |         |  |        | |      | | SINGLE    | | CONSENSUS   |
  +----+----+  +---+----+ +--+---+ +-----+----+ +------+------+
       |           |          |           |              |
       |           +----------+-----------+--------------+
       |           |          retry / re-review
       |           v
       |     +-----+-----+
       +---->| NOT_STARTED|  (backward drag resets)
             +-----------+
```

All non-NOT_STARTED states can transition back to:
- `RUNNING` -- retry / re-review after feedback
- `NOT_STARTED` -- task moved backward (e.g., REVIEW -> BACKLOG)

### Transition Table

| From | To | Trigger | Side-effects |
|------|----|---------|--------------|
| NOT_STARTED | RUNNING | Review pipeline starts | Pipeline begins calling LLM reviewers. |
| RUNNING | APPROVED | Consensus score >= threshold | Task status transitions to REVIEW_AUTO_APPROVED. Review history written. |
| RUNNING | PARTIAL | Pipeline interrupted (some reviewers completed) | Partial results saved. Can retry. |
| RUNNING | FAILED | Pipeline error or timeout | Error logged. Can retry. |
| RUNNING | REJECTED_SINGLE | Single reviewer rejects | Task status transitions to REVIEW_NEEDS_HUMAN. Suggestions saved. |
| RUNNING | REJECTED_CONSENSUS | Multi-reviewer consensus rejects | Task status transitions to REVIEW_NEEDS_HUMAN. Suggestions saved. |
| PARTIAL | RUNNING | User retries review | Pipeline re-runs with fresh reviewers. |
| PARTIAL | NOT_STARTED | Task dragged backward | Reset via `_cleanup_on_backward`. |
| FAILED | RUNNING | User retries review | Pipeline re-runs. |
| FAILED | NOT_STARTED | Task dragged backward | Reset via `_cleanup_on_backward`. |
| REJECTED_SINGLE | RUNNING | User provides feedback and retries | Pipeline re-runs with human feedback context. |
| REJECTED_SINGLE | NOT_STARTED | Task dragged backward | Reset via `_cleanup_on_backward`. |
| REJECTED_CONSENSUS | RUNNING | User provides feedback and retries | Pipeline re-runs with human feedback context. |
| REJECTED_CONSENSUS | NOT_STARTED | Task dragged backward | Reset via `_cleanup_on_backward`. |
| APPROVED | RUNNING | User retries review (re-review) | Pipeline re-runs. |
| APPROVED | NOT_STARTED | Task dragged backward | Reset via `_cleanup_on_backward`. |

### Invariants

1. When `review_lifecycle_state` is `NOT_STARTED`: `consensus_score`, `verdict`, and `cost_usd` MUST NOT be exposed to the frontend.
2. Only terminal states (`APPROVED`, `REJECTED_SINGLE`, `REJECTED_CONSENSUS`) carry meaningful consensus/verdict data.
3. `RUNNING` is transient -- the pipeline is actively executing.
4. The backend is the single source of truth for lifecycle state. The frontend renders this value directly.

---

## 3. Cross-Machine Interactions

The two state machines interact at these points:

| Task Status Transition | Review Lifecycle Effect |
|------------------------|----------------------|
| `* -> BACKLOG` | Review lifecycle reset to `NOT_STARTED` |
| `* -> REVIEW` | Review lifecycle set to `RUNNING` (pipeline starts) |
| Review completes with APPROVED | Task moves `REVIEW -> REVIEW_AUTO_APPROVED` |
| Review completes with rejection | Task moves `REVIEW -> REVIEW_NEEDS_HUMAN` |
| Review retry requested | Review lifecycle set to `RUNNING` |

---

## 4. Known Race Conditions

See [race-condition-audit.md](race-condition-audit.md) for the full audit. Key races:

- **RACE-1**: Concurrent drag vs scheduler finalization (mitigated by epoch ID)
- **RACE-2**: Duplicate review pipeline enqueue (mitigated by status guard)
- **RACE-4**: Review completion vs backward drag (mitigated by `_cleanup_on_backward` reset)
