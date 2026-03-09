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


#### T-P0-124: 多个需要纠正的问题： 1. conversation 工作不正常 看不到任何对话。我们需要折叠除了AI的回复之外的所有对话（包括tool use的输入，输出则完全不显示） 2. Plan tab下生成的MD没有正确被渲染 3. Plain Log需要设计一套合理的高亮和字体颜色来准确舒适的区分AI output，tool use和result 4. 当一个task运行时 需要正确的给conversation 、 log 、 plan都加上跳动的红蓝绿点之一表示状态）
- **Plan**: ready

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

#### [x] T-P0-123: Automated PROGRESS.md archiving hook + pytest timeout fix -- 2026-03-09
- Created `.claude/hooks/archive_check.py` SessionStart hook with hysteresis-based archival (PROGRESS.md: >80 entries keep 40, TASKS.md: >20 completed keep 5). Added `pytest-timeout>=2.2.0` with 30s per-test timeout. Updated test_check.py to exclude integration/slow tests, use `--maxfail=1`, 300s hook timeout. Added `pytest_collection_modifyitems` to integration conftest. Marked 2 pre-existing hanging tests as slow. 14 new archive tests pass, 1517 total pass.

#### [x] T-P0-125: Review MD rendering + executor feedback verification + title inline edit -- 2026-03-09
- ReviewPanel.tsx: entry.summary and suggestions now render markdown via MarkdownRenderer with maxHeight="6rem" and "8rem" respectively. TaskCardPopover.tsx: title is now click-to-edit with hover pencil icon, Enter/blur saves, Escape cancels, max 200 chars. Verified scheduler.py correctly injects reviewer feedback (lines 694-714, log "Injecting previous review feedback into prompt"). Frontend builds successfully (no TS errors in changed files).

#### [x] T-P2-133: Remove unused generate-tasks-preview endpoint -- 2026-03-09
- Removed dead `POST /api/tasks/{task_id}/generate-tasks-preview` endpoint and its `GeneratedTaskPreview`/`GenerateTasksPreviewResponse` schemas. Never called from frontend.

#### [x] T-P2-132: Fix misleading enrichment prompt text about plan context -- 2026-03-09
- Removed misleading "This prompt receives plan context when available" from `enrichment_system.md`. Updated test assertion.

#### [x] T-P2-131: Move reviewer personas from Python to config templates -- 2026-03-09
- Extracted `_REVIEWER_PARAMS` into `config/reviewer_personas.yaml` with YAML loader, caching, and fallback. New persona = YAML entry only. 4 new tests.

#### [x] T-P1-130: Parallelize review pipeline reviewer calls -- 2026-03-09
- Replaced sequential loop with `asyncio.gather()` for multi-reviewer cases. Partial failure handled via `return_exceptions=True`. 3 new tests.

#### [x] T-P1-129: Remove dead synthesis code from review pipeline -- 2026-03-09
- Removed unused `SynthesisResult`, `_SYNTHESIS_JSON_SCHEMA`, `_synthesize()`, `_parse_synthesis()` (~90 lines). Deterministic merge is the actual path.

#### [x] T-P1-128: Add pass/fail calibration example to review prompt -- 2026-03-09
- Added passing/failing calibration examples and threshold guidance to `review.md`. 1 new test.

#### [x] T-P1-127: Add specific structural check items to review prompt -- 2026-03-09
- Updated `_REVIEWER_PARAMS` with structural checks (actionable steps, AC coverage, DAG deps, hidden assumptions). Removed generic OWASP checks. 3 new tests.

#### [x] T-P1-126: Rewrite plan_system.md with phased thinking and strict output contract -- 2026-03-09
- Rewrote plan prompt with 4-phase guidance + `{{complexity_hint}}` variable. Added `_strip_markdown_fences()` fallback. 15 new tests.

#### [x] T-P1-125: Align plan and review prompt rule coverage -- 2026-03-09
- Moved Anti-Patterns from `plan_system.md` into `_shared_rules.md` so both plan and review prompts get it via include. 1 new test.

#### [x] T-P1-124: Extract shared prompt rules into includable fragment -- 2026-03-09
- Added `{{include:filename}}` directive to `render_prompt()`. Extracted shared rules into `_shared_rules.md`, used by both plan and review prompts. 4 new tests.

#### [x] T-P1-123: Pass structured plan_json to reviewers instead of formatted text -- 2026-03-08
- Added `_format_plan_json_for_review()` helper formatting steps/ACs/tasks with indexed prefixes. Injected into reviewer content with graceful fallback. 17 new tests.

#### [x] T-P0-122: Fix replan review_attempt reset to 1 instead of incrementing -- 2026-03-08
- Fixed `_run_replan()` hardcoded `review_attempt=1` to query `get_max_review_attempt()` and increment. 3 new tests.

#### [x] T-P0-121: Fix complexity parameter not passed to review pipeline -- 2026-03-08
- Fixed `_run_review_bg()` always defaulting to "S". Added `complexity` field to Task/TaskRow with auto-migration. Inference from plan structure. 10 new tests.

#### [x] T-P1-116: Unified plan review before batch task decomposition -- 2026-03-08
- Implemented plan review panel: SSE `proposed_tasks[]`, reject-plan endpoint, PlanReviewPanel.tsx with confirm/reject, Plan tab with status badges. 10 new tests.

#### [x] T-P1-115: Upgrade agent prompts to production-grade (Phase 3: quality) -- 2026-03-08
- Upgraded all 5 prompts: plan few-shot + anti-patterns, review `{blocking_issues, suggestions, pass}` schema, deterministic merge, enrichment scope prohibition, execution scope constraint. 15 eval tests.

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
