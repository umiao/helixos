# Task Backlog

<!-- Auto-generated from .claude/tasks.db. Do not edit directly. -->
<!-- Use: python .claude/hooks/task_db.py --help -->

## In Progress

## Active Tasks

### P0 -- Must Have (core functionality)

### P1 -- Should Have (agentic intelligence)

### P2 -- Nice to Have

### P3 -- Stretch Goals

## Blocked

## Completed Tasks

> 16 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

- [x] **2026-03-12** -- T-P3-177: Persist filter state to localStorage. Filter state (filterStatus, filterPriorities, filterComplexities, searchQuery) resets on page reload. Users must re-appl
- [x] **2026-03-12** -- T-P2-179: Add busy_timeout to task_store.py for concurrent hook safety
- [x] **2026-03-12** -- T-P2-176: Add browser notification for needs-human review state. When review pipeline transitions task to review_needs_human, users are not proactively notified. They must check REVIEW 
- [x] **2026-03-12** -- T-P2-175: Add review sub-status badges to task cards. TaskCard currently shows generic "REVIEW" badge for all 3 review sub-states (review, review_auto_approved, review_needs_
- [x] **2026-03-12** -- T-P0-178: Implement DB-as-source-of-truth for task management. Replace regex-based TASKS.md parsing with SQLite-backed task store
- [x] **2026-03-11** -- T-P2-174: Add atomic review submission endpoint. - Added POST /api/tasks/{id}/submit-for-review endpoint that atomically updates title/description and transitions to REV
