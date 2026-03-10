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

(No active P0 tasks -- T-P0-145 in progress above)

### P1 -- Should Have (agentic intelligence)

(No active P1 tasks)

### P2 -- Nice to Have


## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
All 8 new tasks (T-P0-144 through T-P1-151) have no dependencies -- can be worked in any order.
Suggested execution order: 144 -> 145 -> 146 -> 147/148/149 (parallel) -> 150 -> 151

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
