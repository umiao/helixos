# Task Backlog

<!-- Auto-generated from .claude/tasks.db. Do not edit directly. -->
<!-- Use: python .claude/hooks/task_db.py --help -->

## In Progress

## Active Tasks

### P0 -- Must Have (core functionality)

### P1 -- Should Have (agentic intelligence)

### P2 -- Nice to Have

#### T-P2-175: Add review sub-status badges to task cards
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: TaskCard currently shows generic "REVIEW" badge for all 3 review sub-states (review, review_auto_approved, review_needs_human). Users cannot distinguish between "under review", "auto-approved awaiting queue", and "needs human decision" without clicking the task. Add color-coded sub-status badges: gray "Under Review" for review, green "Auto-Approved" for review_auto_approved, orange "Needs Human" for review_needs_human.

- **Acceptance Criteria**:
  1. TaskCard.tsx updated to show sub-status badge based on task.status
  2. review status → gray badge "Under Review"
  3. review_auto_approved status → green badge "Auto-Approved"
  4. review_needs_human status → orange badge "Needs Human"
  5. Badge styles consistent with existing STATUS_COLORS palette
  6. User journey: User drags task to REVIEW → task card shows gray "Under Review" badge → review pipeline completes with auto-approval → badge changes to green "Auto-Approved" → user drags to QUEUED
  7. User journey: Review pipeline returns needs_human → task card shows orange "Needs Human" badge → user clicks task → ReviewPanel shows decision UI
  8. Manual smoke test: Create task, drag to REVIEW, verify gray badge appears, wait for review completion, verify badge changes to green/orange based on result

#### T-P2-176: Add browser notification for needs-human review state
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: When review pipeline transitions task to review_needs_human, users are not proactively notified. They must check REVIEW column manually to discover tasks awaiting human decision. Add browser notification + toast when task transitions to review_needs_human to improve visibility.

- **Acceptance Criteria**:
  1. useSSEHandler.ts detects task_status_changed event with new_status="review_needs_human"
  2. Browser notification triggered with title "Review Needs Human" and body "Task {local_task_id}: {title}"
  3. Toast shown with orange theme: "Task {local_task_id} needs human review decision"
  4. Notification only fires if user has granted browser notification permission
  5. User journey: User creates task → drags to REVIEW → review pipeline runs → pipeline determines needs_human → browser notification appears with task title → user clicks notification → browser focuses HelixOS tab → task auto-selected in ReviewPanel
  6. Manual smoke test: Trigger needs_human review result (e.g., by creating task with ambiguous requirements), verify browser notification appears, verify toast shows, verify clicking notification focuses correct task

### P3 -- Stretch Goals

#### T-P3-177: Persist filter state to localStorage
- **Priority**: P3
- **Complexity**: S
- **Depends on**: None
- **Description**: Filter state (filterStatus, filterPriorities, filterComplexities, searchQuery) resets on page reload. Users must re-apply filters every session. KanbanBoard already persists DONE column sort order to localStorage. Add filter persistence using same pattern to improve UX for users who regularly use specific filter combinations.

- **Acceptance Criteria**:
  1. useTaskState.ts saves filterStatus, filterPriorities, filterComplexities, searchQuery to localStorage on change
  2. On mount, useTaskState.ts loads persisted filter state from localStorage
  3. Clear Filters button also clears localStorage
  4. User journey: User sets filters (Priority=P0,P1, Status=review_needs_human) → searches "auth" → closes browser → reopens HelixOS → filters and search query restored → same filtered view appears
  5. Manual smoke test: Apply multiple filters, reload page, verify filters persist and correct tasks shown

## Blocked

## Completed Tasks

- [x] **2026-03-12** -- T-P2-179: Add busy_timeout to task_store.py for concurrent hook safety
- [x] **2026-03-12** -- T-P0-178: Implement DB-as-source-of-truth for task management. Replace regex-based TASKS.md parsing with SQLite-backed task store
- [x] **2026-03-11** -- T-P2-174: Add atomic review submission endpoint. - Added POST /api/tasks/{id}/submit-for-review endpoint that atomically updates title/description and transitions to REV
- [x] **2026-03-11** -- T-P1-173: Add Cancel Execution button to ExecutionLog. - Added "Cancel Execution" button to ExecutionLog header when task status is "running"
- [x] **2026-03-11** -- T-P1-172: Add P3 priority support to UI and enrichment. - Added P3 option to NewTaskModal dropdown, enrichment prompt, EnrichmentResult model, and JSON schema
- [x] **2026-03-11** -- T-P1-171: Auto-sync Claude Code additionalDirectories on project import. - Created `src/settings_sync.py` syncing non-primary project paths from orchestrator_config.yaml to .claude/settings.loc
- [x] **2026-03-11** -- T-P0-168: Investigate blog_proj TASKS.md access and propose onboarding improvements. - Root cause: Claude Code tool-level permissions scoped to working directory. External projects unreachable from helixos
- [x] **2026-03-11** -- T-P0-167: Fix task workflow data flow after review completion. - Auto-approved tasks now transition REVIEW -> QUEUED directly (no REVIEW_AUTO_APPROVED intermediate state)
- [x] **2026-03-10** -- T-P1-169: Auto-review transitions task to REVIEW status (race-safe). - Added `expected_status` param to `update_status()` for atomic conditional transitions. Auto-review trigger now does BA
- [x] **2026-03-10** -- T-P1-168: Write-back UI title edits to TASKS.md. - Added `update_task_title()` to TasksWriter + wired into PATCH handler. Prevents sync from overwriting UI title edits.
- [x] **2026-03-10** -- T-P0-166: Fix plan summary being cleared after review completion. - Removed `row.description = ""` from GENERATING state in `set_plan_state()` to preserve plan summary during regeneratio
- [x] **2026-03-10** -- T-P0-165: Recover conversation from plain log after page refresh. - Implemented localStorage persistence for selected task ID with automatic restore after page refresh. Added two useEffe
- [x] **2026-03-10** -- T-P0-164: Audit findings review and propose corrective tasks. - Reviewed all findings in docs/audits against current codebase. Corrected 2 inaccuracies (LOW-019 Clear button exists, 
- [x] **2026-03-10** -- T-P0-163: test sample task. - Completed comprehensive UI journey audit covering 9 user flows (Project Import, Task Creation, Kanban Drag-Drop, Revie
- [x] **2026-03-08** -- T-P1-114: Add plan output pydantic validation with retry and error feedback. - Added `PlanValidationConfig` to config.py with configurable hard/soft limits. `ProposedTask` gains `files` field. `gen
- [x] **2026-03-08** -- T-P1-113: Extract agent prompts into config template files. - Created `config/prompts/` with 9 .md template files, `src/prompt_loader.py` with `load_prompt()`/`render_prompt()` (ca
- [x] **2026-03-08** -- T-P1-109: Add cost/usage dashboard endpoint and frontend panel. - Added `GET /api/dashboard/costs` endpoint with single GROUP BY query (review_history JOIN tasks). `CostDashboard.tsx` 
- [x] **2026-03-08** -- T-P1-108: Add Playwright E2E smoke test infrastructure. - Added Playwright E2E test infrastructure with `@playwright/test`, `playwright.config.ts` (Chromium headless, CI-compat
- [x] **2026-03-08** -- T-P1-106: Decompose App.tsx into container components and custom hooks. - Extracted App.tsx (1131 lines) into 4 custom hooks (`useToasts`, `useTaskState`, `useProjectState`, `useSSEHandler`) a
