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

#### T-P0-153: Fix plan edit persistence (description/plan_json desync)

- **Priority**: P0
- **Complexity**: M
- **Depends on**: None
- **Description**: When user edits plan text via ReviewPanel "Edit Plan", `handleSavePlan()`
  calls `updateTask(task.id, { description: editDraft })`. The PATCH endpoint updates
  `task.description` but leaves `plan_json` stale. Architecture fix: `plan_json` is the
  single source of truth. Edits write to `plan_json.plan`, then `description` is derived
  via `format_plan_as_text(plan_json)`. The PATCH endpoint should accept plan text, update
  `plan_json.plan`, and regenerate `description`. Also audit all `plan_json` write paths.
- **Acceptance Criteria**:
  1. Plan text edits write to `plan_json.plan` (canonical source)
  2. `task.description` is re-derived from `plan_json` via `format_plan_as_text()` after edit
  3. `plan_json` structural fields (steps, acceptance_criteria, proposed_tasks) preserved
  4. PlanReviewPanel proposed tasks section reflects current `plan_json` after save
  5. All `plan_json` write paths audited: plan generation, replan, PATCH -- no orphan writers
  6. Reloading page shows edited plan text everywhere (ReviewPanel, PlanReviewPanel)
  7. Manually verify: edit plan text -> save -> switch to Plan tab -> plan summary updated
- **Regression areas**: plan generation pipeline, plan persistence, replan flow,
  ReviewPanel, PlanReviewPanel
- **Files**: `src/routes/tasks.py`, `src/task_manager.py`, `src/enrichment.py`,
  `frontend/src/components/ReviewPanel.tsx`

### P1 -- Should Have (agentic intelligence)

#### T-P1-155: Add Edit button to PlanReviewPanel
- **Priority**: P1
- **Complexity**: S
- **Depends on**: Benefits from T-P0-153
- **Description**: PlanReviewPanel in "ready" state shows plan summary and proposed tasks
  as read-only. No edit button exists (unlike ReviewPanel which has "Edit Plan"). Users
  need to edit plan content before confirming decomposition. Add edit mode with textarea
  + Save/Cancel, reusing the pattern from ReviewPanel's `handleEditPlan`.
- **Acceptance Criteria**:
  1. "Edit Plan" button visible in PlanReviewPanel when plan_status is "ready"
  2. Clicking enters edit mode with textarea pre-filled with current plan text
  3. Save persists via PATCH and calls `onTaskUpdated` to refresh parent state
  4. Cancel discards changes and returns to read-only view
  5. Proposed tasks section refreshes after save (if T-P0-153 is done)
  6. Manually verify: click Edit Plan -> modify text -> Save -> plan summary updates
- **Regression areas**: PlanReviewPanel state transitions, plan persistence
- **Files**: `frontend/src/components/PlanReviewPanel.tsx`

#### T-P1-156: Verify and fix inline task edit across all statuses
- **Priority**: P1
- **Complexity**: S
- **Depends on**: None
- **Description**: User reports cannot edit title/description when hovering over cards
  in backlog/plan/review status. TaskCardPopover has edit functionality (lines 151-258)
  but may be blocked by status-dependent rendering or missing prop threading. Investigate
  which statuses are affected and fix.
- **Acceptance Criteria**:
  1. Hovering any task card (backlog, review, queued, running, done) shows popover
  2. Pencil icon appears on hover for title and description in all statuses
  3. Clicking pencil opens inline editor, Enter/Ctrl+Enter saves, Esc cancels
  4. Edits persist via PATCH for all statuses
  5. Manually verify: hover backlog card -> click pencil on title -> edit -> save -> title updated
- **Regression areas**: TaskCard rendering, popover portal positioning, drag-and-drop
- **Files**: `frontend/src/components/TaskCard.tsx`, `frontend/src/components/TaskCardPopover.tsx`

#### T-P1-157: Investigate T-P0-139 decomposition failure (RCA)
- **Priority**: P1
- **Complexity**: S
- **Type**: Investigation
- **Depends on**: None
- **Description**: T-P0-139 was executed as a single large task instead of being decomposed
  into subtasks. Need root cause analysis. Hypotheses: (1) task complexity was "S" so planner
  skipped decomposition, (2) plan was generated before T-P1-151 enforcement, (3) task was
  manually moved bypassing decomposition gate. Deliverable is RCA document + fix plan.
- **Acceptance Criteria**:
  1. RCA document contains: root cause, reproduction steps, impact scope, fix recommendation
  2. Root cause is verified (not hypothetical) -- backed by log evidence or code trace
  3. If systemic issue found, follow-up fix task created in TASKS.md
  4. If no code fix needed (e.g. user error or pre-T-P1-151 legacy), document why and close
- **Regression areas**: N/A (investigation only)
- **Files**: `src/enrichment.py`, `src/routes/tasks.py`, `src/scheduler.py`

### P2 -- Nice to Have

#### T-P2-158: Design and implement clarifying question workflow for review
- **Priority**: P2
- **Complexity**: L
- **Depends on**: T-P0-153, T-P1-155
- **Description**: Users need both (a) inline Q&A fields for reviewer-raised questions
  and (b) direct plan editing during review. Design a `ReviewQuestion` data model
  (id, text, answer, timestamp) stored in `review_json.questions`. Surface unanswered
  questions prominently in ReviewPanel UI. User answers get injected into replan prompt
  alongside edited plan text. Large feature spanning data model, backend API, frontend UI,
  and prompt engineering.
- **Sub-tasks** (to be decomposed during planning):
  1. Data model + storage (ReviewQuestion schema, migration)
  2. Backend API (extract questions from review, store answers, inject into replan)
  3. Frontend UI (question list, answer fields, integration with existing ReviewPanel)
- **Acceptance Criteria**:
  1. Reviewer suggestions containing questions are extracted and surfaced as distinct Q&A items
  2. User can type answers inline in ReviewPanel
  3. Answers are included in replan prompt when user triggers replan
  4. Plan text remains directly editable alongside Q&A
  5. Answered questions persist across page reloads
  6. Manually verify: review with questions -> answer inline -> replan -> new plan addresses answers
- **Regression areas**: review pipeline, replan flow, ReviewPanel UI, plan generation prompts
- **Files**: `frontend/src/components/ReviewPanel.tsx`, `src/review_pipeline.py`,
  `src/routes/reviews.py`, `src/enrichment.py`, `src/models.py`, `src/db.py`


## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
T-P0-153, T-P1-156, T-P1-157: no dependencies
T-P1-155: benefits from T-P0-153 (plan_json sync)
T-P2-158: depends on T-P0-153, T-P1-155
Suggested execution order: 153 -> 155 -> 156 -> 157 -> 158

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



> 37 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P0-154: Set agent cwd for plan/review on imported projects -- 2026-03-09
- Plan agent (`enrichment.py`) and review agent (`review_pipeline.py`) now use `cwd=repo_path` instead of `add_dirs`. Review pipeline threads `repo_path` through `review_task` -> `_call_reviewer` -> `_call_claude_sdk`. Added `_resolve_repo_path()` helper in `routes/reviews.py`. All 4 `_enqueue_review_pipeline` call sites pass `repo_path`. 276 related tests pass, ruff clean.

#### [x] T-P0-152: Fix ConversationView event normalization (invisible content) -- 2026-03-09
- Fixed `normalizeStreamEvents` to handle backend `sdk_adapter` event types (`text`, `init`, `error`) and field names (`tool_name`/`tool_input`/`tool_use_id`/`tool_result_content`/`tool_result_for_id`). Added red error bubble rendering. TS clean, Vite build clean, 1576 tests pass.

#### [x] T-P2-143: Rewrite historical non-English commit messages -- 2026-03-09
- Already completed as part of T-P2-142. Both commits (`f31a013`, `5ea7b4c`) already have correct English messages. No non-ASCII commit messages remain in history.

#### [x] T-P1-151: Enforce subtask decomposition in planner prompt + review validation -- 2026-03-09
- Updated planner prompt (plan_system.md) with explicit M: 2-4 and L: 3-8 subtask requirements. Added `min_proposed_tasks_m`/`min_proposed_tasks_l` to PlanValidationConfig. `_validate_plan_structure` now enforces minimum subtasks for M/L complexity (S exempt). Review prompt updated to flag missing decomposition. 11 new tests, all 1578 pass, ruff clean.

#### [x] T-P1-150: Add inline description editing to TaskCardPopover -- 2026-03-09
- Added editable description to TaskCardPopover with pencil icon, textarea (Ctrl+Enter save, Esc cancel), Save/Cancel buttons. Empty descriptions show "No description" placeholder. Persists via PATCH /api/tasks/{id}. TS clean, Vite build clean, 1643 Python tests pass.

#### [x] T-P1-149: Collapse consecutive tool_use blocks in ConversationView -- 2026-03-09
- Grouped 2+ consecutive tool_use blocks into collapsible container showing count and tool name summary (e.g. "3 tool calls: Read, Grep, Read"). Individual tools still expandable within the group. Single tool_use blocks render unchanged. Vite build clean, 188 tests pass.

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
