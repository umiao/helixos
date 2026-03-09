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


#### T-P2-140: Document dirty state lesson in LESSONS.md
- **Priority**: P2
- **Complexity**: S (< 1 session)
- **Depends on**: T-P0-134
- **Description**: Document the plan regeneration dirty state bug and the architectural fix (state machine with invariants + generation_id) as a lesson for future reference.
- **Acceptance Criteria**:
  1. LESSONS.md entry covers: context (stale plan display), root cause (no state machine, scattered UI patches), fix (set_plan_state + generation_id + planStatePatch), principle (backend owns state consistency, async pipelines need generation IDs).
  2. References T-P0-134 and T-P0-124.

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
T-P2-140 depends on T-P0-134

### Historical (completed)
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


> 21 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P2-139: Test suite consolidation -- shared fixtures, file splitting, runtime baseline -- 2026-03-09
- Created `tests/factories.py` with `make_task`, `make_config`, `make_review_pipeline_config`, SDK event builders. Migrated 21 test files to use shared factories. Split `test_enrichment.py` (2606->973 LOC) into `test_plan_generation.py` (1295) and `test_plan_models.py` (417). Split `test_review_pipeline.py` (2601->801 LOC) into `test_review_scoring.py` (1035) and `test_review_models.py` (875). All files under 1500 LOC. 1560 tests pass, 35s baseline, ruff clean.

#### [x] T-P0-124: UI improvements -- conversation folding, plan MD rendering, log highlighting, running status indicators -- 2026-03-09
- ConversationView: orphaned tool_results hidden entirely, tool_use collapsed by default with expand on click. PlanReviewPanel: plan summary rendered via ReactMarkdown (not `<pre>`). ExecutionLog: source-based color coding (review=purple, plan=violet, scheduler=cyan, executor=blue) with source badges. BottomPanelContainer: animated pulsing green dots on Conversation/Log tabs for running tasks. TypeScript clean, Vite build clean.

#### [x] T-P0-138: Clean up T-P0-124 dirty state + plan integrity check -- 2026-03-09
- Created `scripts/repair_plan_state.py` to detect and fix plan state invariant violations. Found 151 inconsistent rows (148 with plan_status='none' but stale description, 3 with plan_status='ready' but no plan_json). All fixed and verified with 0 remaining. Rewrote T-P0-124 task spec with proper ACs including journey AC and inverse cases.

#### [x] T-P0-137: Execution decomposition gate (backend + frontend) -- 2026-03-09
- Added `DecompositionRequiredError` and Layer 3 decomposition gate in `update_status()` blocking QUEUED->RUNNING when `has_proposed_tasks=True` and `plan_status="ready"`, with `force_decompose_bypass` flag. Scheduler `_can_execute()` also checks. PATCH endpoint accepts `force_decompose_bypass` in body. Frontend `DecomposeRequiredModal.tsx` with "Go to Plan Review" (green), "Cancel", "Execute Anyway" (danger link). KanbanBoard intercepts forward drag to RUNNING. `api.ts` updated. 8 new tests. 1560 pass, ruff clean, TS clean, Vite build clean.

#### [x] T-P0-136: Plan deletion with confirmation dialog (all plan states) -- 2026-03-09
- Added `DELETE /api/tasks/{task_id}/plan` endpoint using `set_plan_state("none")` from any non-none state. Frontend `deletePlan()` API function. PlanReviewPanel updated with inline confirmation delete buttons for all states (generating=Cancel, failed=Delete alongside Retry, decomposed=Delete with subtask warning, ready=Delete in header). 7 new backend tests. TypeScript clean, Vite build clean.

#### [x] T-P0-135: Frontend plan staleness fix with shared utility and generation_id filtering -- 2026-03-09
- Created `planStatePatch()` shared utility in `frontend/src/utils/planState.ts`. Added `plan_generation_id` and `has_proposed_tasks` to Task interface. Migrated TaskCard, TaskCardPopover, PlanReviewPanel to use shared utility. SSE handler filters stale events via generation_id comparison. TypeScript clean, Vite build clean.

#### [x] T-P0-134: Backend plan state machine with transition rules and field invariants -- 2026-03-09
- Added `VALID_PLAN_TRANSITIONS` + `set_plan_state()` to TaskManager with per-state invariant enforcement. New `plan_generation_id` and `has_proposed_tasks` columns on TaskRow with auto-migration. Migrated all call sites (generate-plan, reject-plan, confirm-tasks, replan, zombie reset) to use `set_plan_state()`. SSE events include `generation_id`. 73 new tests. 1590 pass, ruff clean.

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
