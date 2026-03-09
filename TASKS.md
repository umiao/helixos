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

## In Progress
<!-- Only ONE task here at a time. Focus. -->


## Active Tasks

### P0 -- Must Have (core functionality)

### P1 -- Should Have (agentic intelligence)

### P2 -- Nice to Have

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
T-P1-115 depends on T-P1-113, T-P1-120 (both completed -- T-P1-115 now unblocked)
T-P1-116 depends on T-P1-114 (completed -- T-P1-116 unblocked)
T-P1-125 depends on T-P1-124 (completed -- T-P1-125 now unblocked)
T-P1-126 depends on T-P1-124 (both completed)
T-P1-127 depends on T-P1-123 (completed -- T-P1-127 now unblocked)


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 120 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

- T-P2-133: Remove unused generate-tasks-preview endpoint
- T-P2-132: Fix misleading enrichment prompt text about plan context
- T-P2-131: Move reviewer personas from Python to config templates
- T-P1-130: Parallelize review pipeline reviewer calls
- T-P1-129: Remove dead synthesis code from review pipeline
- T-P1-128: Add pass/fail calibration example to review prompt
- T-P1-127: Add specific structural check items to review prompt
- T-P1-126: Rewrite plan_system.md with phased thinking and strict output contract
- T-P0-121: Fix complexity parameter not passed to review pipeline
- T-P1-125: Align plan and review prompt rule coverage
- T-P1-124: Extract shared prompt rules into includable fragment
- T-P1-123: Pass structured plan_json to reviewers instead of formatted text
- T-P0-122: Fix replan review_attempt reset to 1 instead of incrementing
- T-P1-116: Unified plan review before batch task decomposition
- T-P1-115: Upgrade agent prompts to production-grade (Phase 3: quality)

#### [x] T-P1-118: Harden task cancel with timeout enforcement and force-kill -- 2026-03-08
- Added `timeout_seconds=30` param to `cancel_task()` with graceful/forced paths. Cancel endpoint returns `{"graceful": bool}`. Both paths guarantee FAILED status. 2 new tests (graceful, force-kill timeout). 1123 pass, ruff clean.

#### [x] T-P1-117: Audit and fix SDK invocation settings -- 2026-03-08
- Added `setting_sources=[]` to enrichment QueryOptions. Added `execution_model` config field (default `claude-sonnet-4-5`) to `OrchestratorSettings` and `orchestrator_config.yaml`. Execution agent gains `model` from config and `system_prompt` from new `config/prompts/execution_system.md`. All 4 SDK callsites have code comments explaining setting_sources choice. 6 new tests. 1453 pass, ruff clean.

#### [x] T-P1-120: Consolidate prompt templates from 9 files to 4 -- 2026-03-08
- Consolidated `config/prompts/` from 9 files to 4: inlined fragments into `plan_system.md`, merged review files into parameterized `review.md`, renamed `execution_prompt.md` to `execution.md`. `_REVIEWER_PARAMS` config dict replaces 3 separate module-level prompt vars. `enrich_task_title()` gains conditional skip for non-empty descriptions. 6 files deleted. 11 new tests. 1447 pass, ruff clean.

#### [x] T-P1-119: Add reject-to-replan loop and enrich execution prompt with plan data -- 2026-03-08
- Added `replan` decision to review decide endpoint with max 2 attempts enforcement. `generate_task_plan()` gains `review_feedback` param for structured feedback injection. `Task` model gains `replan_attempt: int = 0` field with auto-migration. `_build_prompt()` injects structured `plan_json` (Implementation Steps + Acceptance Criteria) into execution prompt with graceful fallback. Background replan auto-enqueues review pipeline on success. 29 new tests. 1436 pass, ruff clean.

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
