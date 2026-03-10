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

#### T-P1-163: Redesign Plain Log visual hierarchy with role-based highlighting
- **Priority**: P1 | **Complexity**: M
- **Depends on**: None
- **Description**: Plain Log needs a designed color/font scheme to clearly distinguish
  content roles: AI output, tool invocations, and tool results. Current level-based
  coloring (ERROR/WARN/INFO) is insufficient for comfortable readability. The visual
  design should make the content type immediately apparent at a glance.
- **Files**: `frontend/src/components/ExecutionLog.tsx`
- **Acceptance Criteria**:
  1. Each content role (AI output, tool use, tool result) has visually distinct treatment
  2. Level badges (ERROR/WARN) remain visible as overlay on any content type
  3. Visual hierarchy is comfortable for extended reading
  4. [AUTO-VERIFIED] Build clean, role-based styling applied


#### T-P1-165: Auto-trigger review after plan generation
- **Priority**: P1 | **Complexity**: M
- **Depends on**: None
- **Description**: Review should auto-start immediately when plan_status transitions
  to "ready". Currently review only triggers on explicit status change to REVIEW.
  Wire plan completion to auto-enqueue review pipeline. Must be idempotent --
  concurrent manual + auto trigger must not create duplicate reviews.
- **Files**: `src/routes/reviews.py`, `src/enrichment.py`, `src/routes/tasks.py`
- **Acceptance Criteria**:
  1. When plan_status changes to "ready", review pipeline auto-starts immediately
  2. Idempotent dedup: use plan_version or review lock to prevent duplicate reviews
     if manual trigger and auto-trigger race
  3. Review lifecycle_state updates correctly in auto-triggered flow
  4. SSE events emitted for auto-triggered review
  5. [AUTO-VERIFIED] Trace logs confirm auto-trigger, dedup tested

#### T-P1-166: Verify subtask decomposition is functional end-to-end
- **Priority**: P1 | **Complexity**: S
- **Depends on**: None
- **Description**: Verify the full decomposition flow: plan generates proposed tasks ->
  validation enforces M/L minimums -> decomposition gate blocks execution -> user
  confirms -> subtasks created.
- **Acceptance Criteria**:
  1. Trace plan generation for M/L task confirms proposed_tasks in plan_json
  2. Validation rejects plans with too few subtasks for M/L
  3. Decomposition gate blocks RUNNING transition when has_proposed_tasks
  4. Frontend shows proposed task cards with confirm action
  5. Document findings in PROGRESS.md

#### T-P1-167: Verify task title hover-edit works for all Kanban card statuses
- **Priority**: P1 | **Complexity**: S
- **Depends on**: None
- **Description**: T-P1-156 fixed popover disappearing. Verify the title edit
  (pencil icon -> inline input -> Enter to save) works for cards in ALL statuses
  (pending, planned, running, review, done, blocked).
- **Acceptance Criteria**:
  1. Hover popover appears for cards in every status column
  2. Pencil icon visible and clickable for title edit
  3. Edit saves via PATCH and updates card
  4. [AUTO-VERIFIED] grep confirms no status-based filtering of edit UI



### P2 -- Nice to Have


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



> 37 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P1-164: Add animated status dots to Review tab + unify dot colors -- 2026-03-09
- Added review status dot to BottomPanelContainer: blue pulse for running/partial, green for approved, red for rejected/failed. Unified all tab dots to blue=in-progress (was green for Conversation/Log), green=success, red=failure. TS clean, Vite build clean. [AUTO-VERIFIED]

#### [x] T-P0-162: Verify executor receives reviewer approval and replan feedback (RCA) -- 2026-03-09
- RCA found 3 broken links: (1) blocking_issues missing from replan feedback, (2) blocking_issues not persisted to DB, (3) answered questions not included in execution feedback. Fixed all three: added blocking_issues to _build_replan_feedback(), added blocking_issues_json column to ReviewHistoryRow with migration, added blocking_issues and answered questions to build_review_feedback() in scheduler.py. 1604 tests pass, ruff clean.

#### [x] T-P0-161: Fix markdown rendering in Plan and Review tabs -- 2026-03-09
- Root cause: MarkdownRenderer was missing `rehype-prism-plus` plugin, so code blocks had no syntax highlighting. Content prop wiring was correct (7 usage sites verified). Added rehypePrism import and plugin to MarkdownRenderer. TS clean, Vite build clean. [AUTO-VERIFIED]

#### [x] T-P0-160: Redesign Conversation tab -- collapse tool use, show only AI replies -- 2026-03-09
- Merged consecutive assistant text events into single bubble (text_group in displayEntries). tool_use blocks already collapsed by default, tool_results already hidden unless expanded, thinking blocks already collapsed. TS clean, Vite build clean. [AUTO-VERIFIED]

#### [x] T-P0-159: Fix session_factory not stored on app.state -- 2026-03-09
- `GET/PUT /api/ui-preferences/{key}` crashed with `AttributeError` because `session_factory` was never assigned to `app.state` in lifespan. Added missing assignment in `api.py`, defensive guards in `projects.py`, wired `session_factory` into test fixture, added 3 preference endpoint tests. 67 test_api tests pass, ruff clean.

#### [x] T-P2-158: Design and implement clarifying question workflow for review -- 2026-03-09
- Added `ReviewQuestion` model (id, text, answer, source_reviewer, timestamps) to `models.py`. Updated `ReviewState` with `questions` field. Review pipeline extracts questions from explicit LLM `questions` field and falls back to `?`-ending sentences in suggestions/blocking_issues. New `POST /api/tasks/{task_id}/review/answer` endpoint persists answers. Answered questions injected into replan feedback via `_build_replan_feedback()`. Frontend: `ReviewPanel.tsx` shows unanswered questions prominently with inline answer textarea, answered questions in compact green cards. DB migration-safe via existing `_migrate_missing_columns()`. 16 new tests, 1568 pass, TS clean, Vite build clean. [AUTO-VERIFIED]

#### [x] T-P1-157: Investigate T-P0-139 decomposition failure (RCA) -- 2026-03-09
- Root cause: T-P0-139 was an ad-hoc task created and completed in a single session, bypassing the planning pipeline entirely. No task spec existed in TASKS.md before execution. Additionally, decomposition enforcement (T-P1-151) was committed ~2.75 hours later the same day. Task ID also collided with existing T-P2-139. No code fix needed -- enforcement exists via T-P1-151, issue was process bypass.

#### [x] T-P1-156: Fix inline task edit across all statuses -- 2026-03-09
- Fixed popover disappearing when mouse moves from card to portal-rendered popover. Added 150ms delayed close with onMouseEnter/onMouseLeave props on popover to cancel/trigger close. Edit functionality (pencil icon, inline editor, PATCH save) now accessible for all statuses. TS clean, Vite build clean, 1570 tests pass.

#### [x] T-P1-155: Add Edit button to PlanReviewPanel -- 2026-03-09
- Added "Edit Plan" button to PlanReviewPanel header (ready state). Clicking enters edit mode with textarea pre-filled with plan text, Edit/Preview tabs, Save/Cancel buttons. Save persists via PATCH `updateTask` and calls `onTaskUpdated` to refresh parent state (including proposed tasks via T-P0-153). Cancel discards changes. Header buttons disabled during edit mode. TS clean, Vite build clean, 302 tests pass.

#### [x] T-P0-153: Fix plan edit persistence (description/plan_json desync) -- 2026-03-09
- PATCH endpoint now routes description edits through `plan_json["plan"]` when plan exists, then re-derives `description` via `format_plan_as_text()`. Added `plan_json` to `TaskResponse`. All plan_json write paths audited (generate, replan, PATCH). 8 new regression tests. 302 tests pass, ruff clean.

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
