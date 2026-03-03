# Design: Review Gate + asyncio Fix + Resizable Divider

Three issues surfaced during T-P0-17 task creation. This document provides
root cause analysis, proposed fixes, and verification plans for each.

---

## Issue 1: No Review Gate Before Execution

### Problem

A user creates a task (via NewTaskModal, InlineTaskCreator, or enrichment API),
it enters BACKLOG, and can immediately be moved to QUEUED without any human
confirmation of name, description, or intent. The scheduler's 5-second tick
then auto-executes it.

### Root Cause

The state machine in `src/task_manager.py:25-26` allows BACKLOG -> QUEUED
directly:

```python
VALID_TRANSITIONS = {
    TaskStatus.BACKLOG: {TaskStatus.REVIEW, TaskStatus.QUEUED},  # QUEUED allowed
    ...
}
```

Review is entirely opt-in. No invariant was ever established:
"no task shall execute without human or automated review."

### Why This Design Failure Occurred

- The PRD designed review as "opt-in background advisory" -- appropriate for
  hand-curated tasks
- Once task creation was democratized (InlineTaskCreator, NewTaskModal,
  enrichment API), no one re-evaluated whether the opt-in model was still safe
- No invariant enforced review before execution

### Proposed Fix: Two-Layer Defense

**Layer 1: Transition-level gate** (in `TaskManager.update_status()`)

- Per-project `review_gate_enabled: bool` (default `true`) on `ProjectConfig`
  + `ProjectSettingsRow`
- When enabled: rejects BACKLOG -> QUEUED with clear error message
- Configurable toggle (opt-out, not opt-in) -- some projects/workflows need
  the shortcut
- Follows exact pattern of existing `execution_paused` in
  `src/project_settings.py`

**Layer 2: Execution-entry invariant** (in `Scheduler._execute_task()`)

- New `_can_execute(task) -> bool` check at the scheduler execution entry point
- Validates: `task.status == QUEUED and not paused and has_been_reviewed`
- `has_been_reviewed` definition: exists a `review_history` record for this
  task with `verdict = "approve"` (either auto-approved or human-approved).
  A rejected review record does NOT satisfy this -- the task must have been
  positively approved. When `review_gate_enabled=false` for the project, this
  check is skipped entirely.
- This is the last line of defense -- even if some future code path bypasses
  the transition gate (e.g., direct DB write, new API endpoint), the scheduler
  will not execute an unreviewed task
- Lightweight: one DB lookup, no architectural rewrite. Seeds a `can_execute()`
  interface for future extension

**Frontend + API:**

- API toggle: `PATCH /api/projects/{id}/review-gate` + SSE
  `review_gate_changed` event
- SwimLaneHeader: shield toggle (like existing Pause toggle)
- Drag rejection: BACKLOG -> QUEUED with gate on shows toast "Review required
  before execution"

### Files to Modify

| File | Change |
|------|--------|
| `src/config.py` | Add `review_gate_enabled` to `ProjectConfig` |
| `src/db.py` / `src/project_settings.py` | Add DB column + get/set methods (follows `execution_paused` pattern) |
| `src/task_manager.py` | Gate BACKLOG->QUEUED transition based on project setting |
| `src/scheduler.py` | Add `_can_execute(task)` check in `_execute_task()` with `has_been_reviewed` validation |
| `src/api.py` + `src/schemas.py` | Toggle endpoint, pass gate to status transitions |
| `frontend/src/components/SwimLaneHeader.tsx` | Shield toggle button |
| `frontend/src/App.tsx` | SSE handler, drag rejection logic |

### Invariant (post-implementation)

> When `review_gate_enabled=true` for a project, no task can transition from
> BACKLOG to QUEUED without passing through REVIEW, and no task can be
> executed by the scheduler without an approved review_history record.

---

## Issue 2: asyncio NotImplementedError on Windows

### Problem

Starting the server with `--reload` on Windows crashes with
`NotImplementedError` when any code path calls
`asyncio.create_subprocess_exec()` (review pipeline, code executor).

### Root Cause (confirmed via source inspection)

**Smoking gun** in `uvicorn 0.27.0` at `uvicorn/loops/asyncio.py`:

```python
def asyncio_setup(use_subprocess: bool = False) -> None:
    if sys.platform == "win32" and use_subprocess:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

When `--reload` is used, uvicorn sets `use_subprocess=True`, which explicitly
forces `SelectorEventLoop` on Windows. SelectorEventLoop does NOT support
`asyncio.create_subprocess_exec()`.

The fix at `src/api.py:72-73` (`WindowsProactorEventLoopPolicy`) is dead code
-- uvicorn calls `config.setup_event_loop()` which overrides our policy before
the app module is even imported.

**Execution order**:

1. uvicorn starts -> calls `config.setup_event_loop()` -> sets
   SelectorEventLoopPolicy
2. uvicorn creates event loop (SelectorEventLoop)
3. uvicorn imports `src.api` -> our policy fix runs (too late, loop exists)
4. `create_subprocess_exec()` -> `NotImplementedError`

This is a **dev-only issue** (`--reload` is not used in production), but fixing
it is low cost and essential for the Windows dev workflow.

### Proposed Fix: `--loop none` in startup script

No new files needed. With `loop="none"`, `setup_event_loop()` does nothing.
Python 3.11's default on Windows is already `ProactorEventLoop`, so subprocess
works.

Concrete changes:

1. **`scripts/start.ps1`** -- add `--loop none` to the uvicorn command
2. **`src/api.py:235`** -- split the `except` to distinguish
   `NotImplementedError` from `FileNotFoundError` with accurate log messages
3. **Keep `src/api.py:72-73`** as defense-in-depth with explanatory comment
   (protects non-uvicorn usage like pytest, direct import)
4. **`QUICKSTART.md`** -- document `--loop none` requirement on Windows with
   `--reload`

### Files to Modify

| File | Change |
|------|--------|
| `scripts/start.ps1` | Add `--loop none` to uvicorn command |
| `src/api.py` | Split except clause at line 235; add comment on lines 72-73 |
| `QUICKSTART.md` | Document `--loop none` requirement on Windows |

---

## Issue 3: Resizable Bottom Panel Divider

### Current State

In `App.tsx`, the bottom panel has fixed height `h-56` (224px). The main kanban
area uses `flex-1`. No interactive divider exists.

### Proposed Fix

Create a `ResizableDivider.tsx` component with native mouse drag handling (no
external library):

- **Drag handle**: 4px tall bar between kanban and bottom panel, `cursor:
  row-resize`
- **Pointer capture**: Use `setPointerCapture` on mousedown to prevent event
  loss during fast drags when cursor leaves the handle area
- **Visual**: Subtle grip dots on hover, highlight on active drag
- **State**: `bottomPanelHeight` in App.tsx, replaces fixed `h-56` with inline
  style
- **Bounds**: Min 80px, max 60% viewport
- **Persistence**: `localStorage` with project-namespaced key
  (`helixos_panel_height_{projectId}` or `helixos_panel_height_global` for
  multi-project view)
- **Reset**: Double-click divider restores default 224px
- **No conflict**: Divider uses native pointer events + `setPointerCapture`,
  outside @dnd-kit DndContext

### Files to Modify

| File | Change |
|------|--------|
| `frontend/src/components/ResizableDivider.tsx` (new) | Drag handle component with `setPointerCapture` |
| `frontend/src/App.tsx` | State-managed height, wire divider between kanban and bottom panel |

---

## Proposed TASKS.md Additions

### Under P0 -- Must Have

```
T-P0-18: Configurable review gate before execution (two-layer defense)
  Complexity: M | Depends on: None

T-P0-19: Fix asyncio NotImplementedError on Windows with --reload
  Complexity: S | Depends on: None
```

### Under P3 -- Phase 3: UX + Polish

```
T-P3-12: Resizable bottom panel divider
  Complexity: M | Depends on: None
```

### Recommended Order

1. **T-P0-19** (S) -- unblocks dev workflow immediately
2. **T-P0-18** (M) -- safety-critical, prevents unreviewed execution
3. **T-P3-12** (M) -- UX polish

All three are independent; can be parallelized.

---

## Verification Plan

### T-P0-19 (asyncio fix)

- Start server on Windows with `--loop none --reload`
- Trigger a review or execution
- Confirm no `NotImplementedError`
- Verify accurate error messages for missing CLI vs wrong event loop

### T-P0-18 (review gate)

- Create a task, try to drag from BACKLOG to QUEUED with gate on -> should
  fail with toast
- Try to queue via API -> should fail with 409
- **Layer 2 bypass test**: directly UPDATE task status to QUEUED in the
  database (bypassing TaskManager), then verify scheduler's `_can_execute`
  refuses to execute it because no approved review_history record exists
- Turn gate off -> both layers should allow execution
- Run `pytest` for state machine + scheduler tests

### T-P3-12 (resizable divider)

- Drag the divider up/down, verify bounds (min 80px, max 60% viewport)
- Refresh page and confirm height persists in localStorage
- Double-click to reset to default 224px
- Fast-drag test: move cursor off the handle area during drag -> should
  continue tracking via pointer capture
- Verify no conflict with @dnd-kit drag-drop

### Full Suite

- `pytest` (all existing + new tests pass)
- `ruff check` (no lint violations)
- `npm run build` (no TypeScript or build errors)
