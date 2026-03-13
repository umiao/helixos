# State Machine Transition Race Condition Audit

**Date**: 2026-03-06
**Task**: T-P1-76

## Overview

This document enumerates all race condition windows in the HelixOS task
orchestrator's status transition system, evaluates severity, and proposes
mitigations. The audit covers: task status transitions, review pipeline
lifecycle, scheduler finalization, SSE broadcast consistency, and
concurrent user actions.

---

## State Machines

### Task Status (src/models.py)

```
BACKLOG --> REVIEW --> REVIEW_AUTO_APPROVED --> QUEUED --> RUNNING --> DONE
                  |-> REVIEW_NEEDS_HUMAN --^       |         |
                                                   v         v
                                                BLOCKED   FAILED
```

All backward transitions also exist (e.g., DONE -> BACKLOG, FAILED -> QUEUED).
See `VALID_TRANSITIONS` in `src/task_manager.py:79-98`.

### Review Lifecycle (src/models.py)

```
NOT_STARTED --> RUNNING --> APPROVED
                       |--> PARTIAL
                       |--> FAILED
                       |--> REJECTED_SINGLE
                       |--> REJECTED_CONSENSUS
```

Terminal states can transition back to RUNNING or NOT_STARTED for retries.
See `REVIEW_LIFECYCLE_TRANSITIONS` in `src/models.py:82-102`.

---

## Race Condition Windows

### RACE-1: Concurrent Drag vs Scheduler Finalization

**Severity**: MEDIUM
**Components**: Frontend drag (API PATCH), Scheduler `_execute_task()`

**Scenario**: User drags task to DONE (or BACKLOG) while scheduler is
about to mark it FAILED after execution error.

**Current Mitigation**: State guard in `scheduler.py:616-627` re-fetches
task status before FAILED transition. If no longer RUNNING, skips update.

**Residual Risk**: TOCTOU gap between state guard check and
`update_status()` call. Task could be moved again in that window.

**Recommended Fix**: Use `expected_updated_at` optimistic lock in
scheduler's `update_status()` call (T-P1-77 epoch ID).

**Update 2026-03-10**: T-P1-169 implemented `expected_status` parameter
(task_manager.py:321,362). The scheduler and auto-review pipeline can now
use atomic conditional transitions via `update_status(..., expected_status=TaskStatus.RUNNING)`.
This significantly reduces the TOCTOU window. Residual risk: epoch ID
verification (T-P1-77) provides additional protection against stale finalization.

**Test Coverage**: `test_race_scheduler_vs_drag` (added in this task)

---

### RACE-2: Duplicate Review Pipeline Enqueue

**Severity**: MEDIUM
**Components**: `_enqueue_review_pipeline()`, `retry_review()` endpoint

**Scenario**: Two rapid clicks on "Retry Review" or rapid drag-to-REVIEW.

**Current Mitigation**: `retry_review()` checks `review_status == "running"`
and returns HTTP 409. The transition-driven trigger in `update_task_status()`
only fires when `existing.status != REVIEW` and `body.status == REVIEW`.

**Residual Risk**: TOCTOU gap in `retry_review()` -- between the
`review_status` read (line 1577) and the background task setting it to
"running" (inside `_run_review_bg`), a second request could slip through.
The `update_status()` path sets `review_status = "running"` atomically in
the same DB transaction (line 385), so the drag path is safer.

**Recommended Fix**: Set `review_status = "running"` in the
`retry_review()` handler BEFORE creating the background task (already
partially done at line 1605 for the REVIEW-already case, but not for the
status-transition case). Additionally, the background task should
re-check `review_status` at start.

**Test Coverage**: `test_race_duplicate_review_enqueue` (added in this task)

---

### RACE-3: Scheduler Success vs Timeout Path

**Severity**: LOW
**Components**: `code_executor.py` timeout logic, scheduler finalization

**Scenario**: Executor's SDK query returns a successful result at the
exact moment the session timeout fires.

**Current Mitigation**: In `code_executor.py`, timeout is checked at the
top of the queue-read loop. If timed out, the loop breaks with
`timed_out = True`. The result is classified based on flags after the loop,
so the last event wins. Scheduler success path has an idempotent guard
(`scheduler.py:563-572`): re-fetches task, skips if already DONE.

**Residual Risk**: Minimal. The queue-based design serializes events.
The timeout flag is checked before each queue read, so a result arriving
before timeout will be processed normally.

**Recommended Fix**: No immediate action needed. Epoch ID (T-P1-77)
will add a second safety layer.

**Test Coverage**: Existing tests in `test_code_executor.py`

---

### RACE-4: Review Pipeline Completion vs User Backward Drag

**Severity**: MEDIUM
**Components**: `_run_review_bg()` background task, user drag to BACKLOG

**Scenario**: Review pipeline completes (sets lifecycle_state = APPROVED,
transitions to REVIEW_AUTO_APPROVED). Meanwhile, user drags task from
REVIEW back to BACKLOG.

**Current Mitigation**: `_cleanup_on_backward()` resets `review_status`
to "idle" on backward to BACKLOG. ~~However:~~
~~1. `review_lifecycle_state` is NOT reset (stale APPROVED/REJECTED visible)~~
**Update 2026-03-10**: Fixed. `_cleanup_on_backward()` now resets
`review_lifecycle_state = "not_started"` on `-> BACKLOG` transitions
(task_manager.py:480).

2. Background pipeline still runs; its `update_status()` call after
   completion will try REVIEW -> REVIEW_AUTO_APPROVED on a task that is
   now BACKLOG, raising ValueError (silently caught by exception handler,
   setting review to FAILED).

**Residual Risk**: ~~Stale `review_lifecycle_state` after backward drag.~~
Background pipeline wastes resources running to completion.

**Recommended Fix**:
~~1. Reset `review_lifecycle_state = NOT_STARTED` in `_cleanup_on_backward()`
   for `-> BACKLOG` transitions (implemented in this task).~~ [DONE] Complete.
2. Future: cancel background review task on backward drag.

**Test Coverage**: `test_race_review_completion_vs_backward_drag` (added),
`test_cleanup_resets_review_lifecycle_state` (added)

---

### RACE-5: SSE Event Ordering vs DB State

**Severity**: LOW
**Components**: EventBus, API endpoints, scheduler

**Scenario**: SSE `status_change` event arrives at frontend before the
DB transaction commits (or vice versa), causing brief UI inconsistency.

**Current Mitigation**: SSE events are emitted AFTER `update_status()`
returns (DB committed). Frontend fetches fresh state on reconnect.

**Residual Risk**: If network delays SSE but frontend polls API, state
is consistent. If SSE arrives but poll is stale (caching), brief flicker.
EventBus bounded queues (1000) drop oldest on overflow, but this is
unlikely in practice.

**Recommended Fix**: No action needed. Current architecture is sound.

---

### RACE-6: Concurrent Task Creation with Same ID

**Severity**: LOW
**Components**: `task_manager.create_task()`, TASKS.md parser

**Scenario**: Two API calls try to create tasks with the same ID.

**Current Mitigation**: TaskRow primary key constraint on `id` field.
Second insert raises IntegrityError. TASKS.md parser generates IDs
from file content deterministically.

**Residual Risk**: None. DB constraint is authoritative.

---

### RACE-7: Plan Generation vs Review Submission

**Severity**: LOW
**Components**: `generate_plan()` endpoint, drag-to-REVIEW

**Scenario**: User triggers plan generation (plan_status = "generating"),
then immediately drags to REVIEW before plan completes.

**Current Mitigation**: Layer 2 plan validity gate (`task_manager.py:350-362`)
blocks BACKLOG -> REVIEW when plan is missing or too short. If plan is
still generating, description hasn't been updated yet, so gate blocks.

**Residual Risk**: If plan generation completes between the gate check
and the user's retry, the plan may be stale. This is acceptable behavior.

**Recommended Fix**: No action needed.

---

### RACE-8: Startup Crash Recovery vs In-Flight Requests

**Severity**: LOW
**Components**: `scheduler.startup_recovery()`, API endpoints

**Scenario**: Server restarts. `startup_recovery()` marks all RUNNING
tasks as FAILED. A late API request (from before restart) tries to
update a task that was just marked FAILED.

**Current Mitigation**: `startup_recovery()` runs during lifespan startup,
before the server accepts requests. API endpoints are not available until
startup completes.

**Residual Risk**: None in single-server deployment.

---

## Summary Table

| ID | Race Window | Severity | Mitigated? | Fix |
|----|------------|----------|------------|-----|
| RACE-1 | Drag vs Scheduler finalization | MEDIUM | Partial | Epoch ID (T-P1-77) |
| RACE-2 | Duplicate review enqueue | MEDIUM | Partial | Guard + re-check |
| RACE-3 | Timeout vs success | LOW | Yes | -- |
| RACE-4 | Review done vs backward drag | MEDIUM | Partial | Cleanup + cancel |
| RACE-5 | SSE vs DB ordering | LOW | Yes | -- |
| RACE-6 | Duplicate task creation | LOW | Yes | -- |
| RACE-7 | Plan gen vs review submit | LOW | Yes | -- |
| RACE-8 | Startup recovery vs requests | LOW | Yes | -- |

## Actionable Items

1. **T-P1-77** (already planned): Add epoch ID to scheduler finalization
   to close RACE-1 TOCTOU gap.
2. **This task**: Reset `review_lifecycle_state` on backward transitions
   (fixes part of RACE-4). Add test coverage for RACE-1, RACE-2, RACE-4.
3. **Future**: Cancel in-flight review background tasks on backward drag
   (RACE-4 resource waste).
4. **Future**: Add `review_status` re-check at `_run_review_bg()` start
   (RACE-2 belt-and-suspenders).
