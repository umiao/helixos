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

#### T-P0-137: Execution decomposition gate (backend + frontend)
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-134
- **Description**: Dragging a task to execution currently ignores undecomposed proposed tasks. Need a gate at both scheduler level (Layer 3 in `_can_execute()` using `has_proposed_tasks` boolean) and manual transition level (`update_status()` with `force_decompose_bypass` flag). Frontend needs a modal in KanbanBoard when dragging to RUNNING.
- **Acceptance Criteria**:
  1. Scheduler `_can_execute()` in `src/scheduler.py` returns False when `task.has_proposed_tasks and task.plan_status == "ready"`. Uses boolean field, no JSON parsing.
  2. `update_status()` in `src/task_manager.py` raises `DecompositionRequiredError` when transitioning to RUNNING with `has_proposed_tasks=True and plan_status="ready"`, unless `force_decompose_bypass=True`.
  3. When `force_decompose_bypass=True`, a warning is logged: "Decomposition gate bypassed for task {id} by user action".
  4. Status PATCH endpoint accepts `force_decompose_bypass: bool` in request body, passes to `update_status()`.
  5. New `DecomposeRequiredModal.tsx` following `BackwardDragModal.tsx` pattern: shows task title, number of proposed subtasks, with actions: "Go to Plan Review" (primary green), "Cancel" (secondary), "Execute Anyway" (small danger text link at bottom, deliberately de-emphasized).
  6. `KanbanBoard.tsx` `handleDragEnd` intercepts forward drag to RUNNING when `task.plan_status === "ready" && task.proposed_tasks?.length > 0`, shows DecomposeRequiredModal.
  7. "Go to Plan Review" navigates to plan tab for the task. "Execute Anyway" calls `updateTaskStatus` with `force_decompose_bypass: true`. "Cancel" closes modal.
  8. `frontend/src/api.ts` `updateTaskStatus` accepts `force_decompose_bypass?: boolean`.
  9. Manual verification: Drag undecomposed task to RUNNING -> modal appears -> each button works correctly.
  10. Backend test: scheduler skips task with has_proposed_tasks=True in _can_execute(). update_status raises without force flag, succeeds with force flag.

#### T-P0-138: Clean up T-P0-124 dirty state + plan integrity check
- **Priority**: P0
- **Complexity**: S (< 1 session)
- **Depends on**: T-P0-134, T-P0-136
- **Description**: T-P0-124 has dirty DB state where stale plan data overwrites user-generated plans. After the state machine is deployed, run an integrity check for all tasks with inconsistent plan fields, fix via the new DELETE endpoint or set_plan_state. Also rewrite the T-P0-124 task spec in TASKS.md with proper fields.
- **Acceptance Criteria**:
  1. Run SQL query to find all tasks with inconsistent plan state: `(plan_status='none' AND (plan_json IS NOT NULL OR description != ''))` OR `(plan_status='generating')` (orphaned) OR `(plan_status='ready' AND plan_json IS NULL)`.
  2. If only a few rows: fix via DELETE /plan endpoint. If widespread: write `scripts/repair_plan_state.py` using `set_plan_state("none")`.
  3. T-P0-124 specifically has its plan state reset to "none".
  4. T-P0-124 entry in TASKS.md is rewritten with proper task schema fields (priority, complexity, depends_on, description, ACs).
  5. Verification: SQL query returns 0 inconsistent rows after fix.

#### T-P0-124: UI improvements -- conversation folding, plan MD rendering, log highlighting, running status indicators
- **Priority**: P0
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P0-138 (state cleanup first)
- **Description**: Multiple UI issues need correction: (1) Conversation view shows nothing -- need to fold all content except AI replies (tool_use inputs collapsed, tool_result outputs hidden entirely). (2) Plan tab generated MD not rendered properly -- needs ReactMarkdown rendering. (3) Plain Log needs syntax-aware highlighting with distinct colors for AI output, tool_use, and tool_result. (4) Running tasks need animated status dots (red/blue/green) on conversation, log, and plan tabs.
- **Acceptance Criteria**:
  1. Conversation view: AI text responses shown expanded. tool_use blocks collapsed by default (show tool name + summary). tool_result blocks completely hidden. User can expand tool_use blocks on click.
  2. Plan tab: task.description rendered via ReactMarkdown (not raw `<pre>` tag). Code blocks syntax-highlighted. Headers, lists, tables render correctly.
  3. Plain Log: AI output lines styled with distinct foreground color. tool_use lines styled differently (e.g., muted blue). tool_result lines styled differently (e.g., gray). Clear visual hierarchy.
  4. When a task has status=running: conversation tab shows animated dot (e.g., pulsing green). log tab shows animated dot. plan tab shows animated dot (if plan is generating).
  5. Manual verification: Open a running task -> see animated dots on tabs -> conversation shows folded tool blocks -> plan shows rendered markdown -> log has color-coded entries.
  6. TypeScript clean. Vite build clean.

### P1 -- Should Have (agentic intelligence)

### P2 -- Nice to Have

#### T-P2-139: Test suite consolidation -- shared fixtures, file splitting, runtime baseline
- **Priority**: P2
- **Complexity**: M (1-2 sessions)
- **Depends on**: None
- **Description**: Test suite has 1,596 tests across 62 files. `_make_task()` fixture duplicated in 10+ files (violates CLAUDE.md). `test_enrichment.py` (2,606 lines) and `test_review_pipeline.py` (2,601 lines) are bloated. Need consolidation for maintainability and runtime control. Current runtime ~33s.
- **Acceptance Criteria**:
  1. `_make_task()` and similar factory helpers centralized in `tests/conftest.py`. Zero duplication across test files.
  2. Files exceeding 1,500 LOC split by concern: `test_enrichment.py` -> 2-3 files, `test_review_pipeline.py` -> 2-3 files.
  3. `pytest --durations=20` output captured as baseline in this task's PROGRESS entry.
  4. No test regressions: all tests still pass with same count.
  5. Ruff clean.

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
T-P0-137 depends on T-P0-134
T-P0-138 depends on T-P0-134, T-P0-136
T-P0-124 depends on T-P0-138
T-P2-140 depends on T-P0-134

### Historical (completed)
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
