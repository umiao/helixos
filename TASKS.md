# Task Backlog

> **Convention**: Pick tasks from top of Active (highest priority first).
> Move to In Progress when starting. Move to Completed when done.
> PRD reference: helixos_prd_v0.3.md (single source of truth for architecture)
>
> **Task Schema Template** (required fields for every new task):
> ```
> #### T-PX-NN: Title
> - **Priority**: P0 | P1 | P2 | P3
> - **Complexity**: S (< 1 session) | M (1-2 sessions) | L (3+ sessions)
> - **Depends on**: T-XX-NN | None
> - **Description**: What and why (2-4 sentences)
> - **Acceptance Criteria**:
>   1. Specific, verifiable outcome
>   2. At least one full user journey AC
>   3. Manual smoke test AC for UX tasks
> ```
>
> **Size invariant**: Active TASKS.md must stay under 300 lines. Completed tasks
> are archived to [archive/completed_tasks.md](archive/completed_tasks.md).
> PROGRESS.md follows the same pattern (archive to `archive/progress_log.md`).

## In Progress
<!-- Only ONE task here at a time. Focus. -->

## Active Tasks

### P0 -- Must Have (core functionality)


### P1 -- Should Have (agentic intelligence)




### P2 -- Nice to Have

#### Add atomic review submission endpoint
- **Priority**: P2
- **Complexity**: S
- **Depends on**: None
- **Description**: ReviewSubmitModal currently makes 2 separate API calls: PATCH /api/tasks/{id} to update title/description, then PATCH /api/tasks/{id}/status to transition to REVIEW. This creates a race condition where concurrent updates or SSE events between calls can cause data loss or stale review plans. Add backend endpoint POST /api/tasks/{id}/submit-for-review accepting optional {title, description} to ensure atomic transactional consistency.
- **Acceptance Criteria**:
  1. Backend endpoint POST /api/tasks/{id}/submit-for-review created in src/routes/reviews.py
  2. Endpoint accepts optional {title, description} in request body
  3. Single DB transaction: update title/description if provided, then transition status to REVIEW
  4. ReviewSubmitModal.tsx updated to call new endpoint instead of 2 separate calls
  5. User journey: User edits task plan in ReviewSubmitModal → clicks "Submit for Review" → single API call updates description AND transitions to REVIEW atomically → task appears in REVIEW column with updated description
  6. Manual smoke test: Edit task description in ReviewSubmitModal, submit for review, verify REVIEW column shows task with updated description (no race condition)

#### Add review sub-status badges to task cards
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

#### Add browser notification for needs-human review state
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

#### Persist filter state to localStorage
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


## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
T-P0-160, T-P0-161, T-P0-162 -- parallel, no dependencies
T-P1-163 through T-P1-167 -- no dependencies

### Historical (completed)
T-P2-140 depends on T-P0-134 (completed)
T-P0-138 depends on T-P0-134, T-P0-136 (completed)
T-P0-124 depended on T-P0-138 (completed, dependency cleared)
T-P0-137 depends on T-P0-134 (completed)
T-P1-115 depends on T-P1-113, T-P1-120 (both completed)
T-P1-116 depends on T-P1-114 (completed)
T-P1-125 depends on T-P1-124 (completed)
T-P1-126 depends on T-P1-124 (completed)
T-P1-127 depends on T-P1-123 (completed)


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks




> 57 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P1-173: Add Cancel Execution button to ExecutionLog -- 2026-03-11
- Added "Cancel Execution" button to ExecutionLog header when task status is "running"
- Button shows confirmation dialog before calling POST /api/tasks/{id}/cancel
- Success/error toasts via onSuccess/onError callbacks threaded through BottomPanelContainer

#### [x] T-P1-172: Add P3 priority support to UI and enrichment -- 2026-03-11
- Added P3 option to NewTaskModal dropdown, enrichment prompt, EnrichmentResult model, and JSON schema
- Added P3 color (blue) to priorityColor in PlanComponents.tsx
- Updated enrichment test to validate P3 priority

#### [x] T-P1-171: Auto-sync Claude Code additionalDirectories on project import -- 2026-03-11
- Created `src/settings_sync.py` syncing non-primary project paths from orchestrator_config.yaml to .claude/settings.local.json additionalDirectories
- Called from import endpoint, api.py lifespan, and autonomous_run.sh pre-launch
- Atomic write with backup, JSON validation, preserves existing allow rules. 9 unit tests.

#### [x] T-P0-168: Investigate blog_proj TASKS.md access and propose onboarding improvements -- 2026-03-11
- Root cause: Claude Code tool-level permissions scoped to working directory. External projects unreachable from helixos-centric sessions. Solution: auto-sync additionalDirectories. Implemented as T-P1-171.

#### [x] T-P0-167: Fix task workflow data flow after review completion -- 2026-03-11
- Auto-approved tasks now transition REVIEW -> QUEUED directly (no REVIEW_AUTO_APPROVED intermediate state)
- request_changes/reject auto-trigger replan with semantic differentiation (targeted-fix vs fundamental-rework framing)
- reject does not increment replan counter; falls back to BACKLOG at max attempts
- approve forces immediate debounced scheduler tick via force_tick()
- MAX_REPLAN_ATTEMPTS raised from 2 to 4
- 14 regression tests in test_review_workflow.py

#### [x] T-P0-166: Fix plan summary being cleared after review completion -- 2026-03-10
- Removed `row.description = ""` from GENERATING state in `set_plan_state()` to preserve plan summary during regeneration
- Updated docstring and added inline comments explaining the behavior
- Updated tests to reflect new behavior where description is preserved during all GENERATING transitions

#### [x] T-P0-165: Recover conversation from plain log after page refresh -- 2026-03-10
- Implemented localStorage persistence for selected task ID with automatic restore after page refresh. Added two useEffect hooks in useTaskState.ts: sync selectedTask to localStorage (cleared on deselect/deleted task), restore selection after tasks load. Enhanced ConversationView error handling (log to console instead of silent fail). Improved backend stream-log endpoint with OSError handling for concurrent JSONL reads. Fixed pre-existing TypeScript errors (toolInput unknown check, missing "decomposed" PlanStatus value).

#### [x] T-P0-164: Audit findings review and propose corrective tasks -- 2026-03-10
- Reviewed all findings in docs/audits against current codebase. Corrected 2 inaccuracies (LOW-019 Clear button exists, MEDIUM-003 backend endpoint exists). Updated audit docs with verification notes and Known Omissions section. Updated race condition audit with T-P1-169 and RACE-4 mitigation notes. Proposed 6 fix tasks in TASKS.md (2 P1, 3 P2, 1 P3) with full ACs including user journey and smoke test criteria.

#### [x] T-P1-168: Write-back UI title edits to TASKS.md -- 2026-03-10
- Added `update_task_title()` to TasksWriter + wired into PATCH handler. Prevents sync from overwriting UI title edits.

#### [x] T-P1-169: Auto-review transitions task to REVIEW status (race-safe) -- 2026-03-10
- Added `expected_status` param to `update_status()` for atomic conditional transitions. Auto-review trigger now does BACKLOG->REVIEW before enqueuing pipeline.

#### [x] T-P0-163: test sample task -- 2026-03-10
- Completed comprehensive UI journey audit covering 9 user flows (Project Import, Task Creation, Kanban Drag-Drop, Review Gate, Plan Generation, Execution Monitoring, Review Pipeline, Filtering & Search, LLM Prompts). Identified 5 MEDIUM risks (P3 priority gap, review submission race condition, missing cancel-execution button, needs-human notification gap, review sub-state differentiation) and 11 LOW risks. Created 66KB audit report in docs/audits/ui-journey-audit-T-P0-163.md with full user journey traces, conditional behavior documentation, risk summary table, and actionable recommendations.

#### [x] T-P1-114: Add plan output pydantic validation with retry and error feedback -- 2026-03-08
- Added `PlanValidationConfig` to config.py with configurable hard/soft limits. `ProposedTask` gains `files` field. `generate_task_plan()` retries up to N times on validation failure with error feedback in prompt. `_validate_plan_structure()` detects dependency cycles via `detect_cycles()`. Soft limits emit warnings. Hard ceiling: max 10 proposed tasks. 25 new tests. 1407 pass, ruff clean.

#### [x] T-P1-113: Extract agent prompts into config template files -- 2026-03-08
- Created `config/prompts/` with 9 .md template files, `src/prompt_loader.py` with `load_prompt()`/`render_prompt()` (cached, UTF-8). Replaced inline prompt constants in `enrichment.py`, `review_pipeline.py`, `code_executor.py`. 20 new tests. 1383 pass, ruff clean.

#### [x] T-P1-109: Add cost/usage dashboard endpoint and frontend panel -- 2026-03-08
- Added `GET /api/dashboard/costs` endpoint with single GROUP BY query (review_history JOIN tasks). `CostDashboard.tsx` component with formatted USD table, "Costs" tab in bottom panel. 4 new tests. 1363 pass, TS clean, Vite build clean.

#### [x] T-P1-106: Decompose App.tsx into container components and custom hooks -- 2026-03-08
- Extracted App.tsx (1131 lines) into 4 custom hooks (`useToasts`, `useTaskState`, `useProjectState`, `useSSEHandler`) and `BottomPanelContainer` component. App.tsx is now ~280 lines of pure composition. 1359 tests pass, TS clean, Vite build clean, 12 Playwright tests discovered.

#### [x] T-P1-108: Add Playwright E2E smoke test infrastructure -- 2026-03-08
- Added Playwright E2E test infrastructure with `@playwright/test`, `playwright.config.ts` (Chromium headless, CI-compatible), and 4 test files (12 tests) in `frontend/e2e/`: page-load (Kanban columns + header), task-card (card rendering + status badges), task-click (bottom panel activation), project-filter (filter bar + search + priority chips). Added `npm run e2e` script. 1359 Python tests pass, TS clean, Vite build clean.
