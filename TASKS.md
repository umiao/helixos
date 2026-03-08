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

#### T-P1-115: Upgrade agent prompts to production-grade (Phase 3: quality)
- **Priority**: P1
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P1-113, T-P1-120
- **Description**: After T-P1-119 (flow fixes) and T-P1-120 (structural consolidation), this task focuses on prompt quality: add few-shot examples to plan/review/execution prompts, tighten JSON schemas with stricter validation, add eval test cases that assert prompt output quality against reference inputs. Review prompts use structured output (blocking_issues/suggestions/pass) with deterministic merge (any blocking_issue = reject). Enrichment prompt explicitly forbids scope expansion.
- **Acceptance Criteria**:
  1. Plan prompt: few-shot example of good proposed_tasks[] output; anti-pattern examples; task scope guidance as configurable limits
  2. Review prompts: output schema changed to `{blocking_issues: [], suggestions: [], pass: bool}`; `_REVIEW_JSON_SCHEMA` updated; deterministic merge in review_pipeline.py (any blocking_issue = reject)
  3. Enrichment prompt: receives plan context; explicitly forbids scope expansion
  4. Execution prompt: system_prompt with agent role; file constraint with escape hatch for test/config files; "Only implement the current task"
  5. Eval test cases: at least 3 reference input/output pairs per prompt type asserting key phrases and structure
  6. All existing tests updated for new schemas; new tests verify prompt content and review merge logic

#### T-P1-116: Unified plan review before batch task decomposition
- **Priority**: P1
- **Complexity**: M (1-2 sessions)
- **Depends on**: T-P1-114
- **Description**: Present generated plan with proposed sub-tasks as a single unified document for human review. After human confirms, all tasks batch-created via existing confirm-decomposition endpoint. Tasks are NOT shown as individual cards until confirmed.
- **Acceptance Criteria**:
  1. `plan_status_change` SSE event includes `proposed_tasks[]` when plan_status = `ready`
  2. Frontend: "Plan Review" panel shows plan summary + all proposed tasks as readable document
  3. "Confirm and Create All Tasks" button calls `confirm-decomposition`, batch-writes all tasks to TASKS.md
  4. "Reject Plan" resets plan_status to `none`; no tasks created
  5. Generating state: spinner. Failed state: error message with retry option
  6. Manually verify: Generate Plan -> unified review panel -> Confirm -> tasks appear on board [AUTO-VERIFIED]

#### T-P1-117: Audit and fix SDK invocation settings
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Audit all `run_claude_query()` callsites for consistent configuration. Add `setting_sources=[]` to enrichment. Add explicit model from config and system_prompt to execution agent. Keep execution agent setting_sources as default (all hooks). Add `execution_model` config field.
- **Acceptance Criteria**:
  1. `enrich_task_title()` QueryOptions gains `setting_sources=[]`
  2. `code_executor` QueryOptions gains `model` from `OrchestratorSettings.execution_model` (default `"claude-sonnet-4-5"`)
  3. `code_executor` QueryOptions gains `system_prompt` from execution prompt template
  4. `orchestrator_config.yaml` gains `execution_model` field; `OrchestratorSettings` parses it
  5. Each SDK callsite has code comment explaining its setting_sources choice
  6. All existing tests pass; new tests verify model and system_prompt in QueryOptions

#### T-P1-118: Harden task cancel with timeout enforcement and force-kill
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Add timeout to cancel_task() with force-cleanup fallback. Guarantee FAILED status after cancel. Frontend shows spinner during cancel.
- **Acceptance Criteria**:
  1. `scheduler.cancel_task()` gains `timeout_seconds=30` param; force-cancels asyncio task if exceeded
  2. Task status guaranteed to transition to FAILED after cancel (graceful or forced)
  3. Cancel endpoint returns whether cancel was graceful or forced
  4. Frontend "Stop Execution" button shows loading state during cancel
  5. Cancel on non-running task returns 409 (verify no regression)
  6. New tests: graceful cancel, timeout force-cancel, cancel on non-running task
  7. Manually verify: Start execution -> Stop -> FAILED within 30s [AUTO-VERIFIED]

#### T-P1-120: Consolidate prompt templates from 9 files to 4
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: T-P1-119
- **Description**: The current 9 prompt files use 3-layer nesting (fragment -> context -> final prompt) for only 4 independent responsibilities. Consolidate: (1) Inline `task_schema_context.md` and `project_rules_context.md` into `plan_system.md` (2 consumers, not worth separate files). (2) Merge `review_conventions_context.md`, `review_feasibility.md`, `review_adversarial.md`, `review_default.md` into single `review.md` template parameterized by `{{reviewer_role}}` + `{{review_questions}}` -- 3 parallel reviewer calls preserved, only the template file is unified. (3) Rename `execution_prompt.md` to `execution.md` (already enriched by T-P1-119). (4) Make `enrich_task_title()` conditional: skip if `task.description` is non-empty.
- **Acceptance Criteria**:
  1. `config/prompts/` contains exactly 4 files: `enrichment_system.md`, `plan_system.md`, `review.md`, `execution.md`
  2. `plan_system.md` is self-contained (task schema + project rules inlined, no `{{fragment}}` placeholders)
  3. `review.md` uses `{{reviewer_role}}` and `{{review_questions}}` placeholders; `_build_review_prompt(focus)` renders with per-focus params
  4. Three parallel reviewer calls still work (feasibility, adversarial, default) -- only the template is unified, not the calls
  5. Rendered output of each consolidated prompt is content-equivalent to the old multi-file version (diff test)
  6. `enrichment.py`: `load_prompt("plan_system")` replaces `render_prompt("plan_system", task_schema_context=..., project_rules_context=...)`
  7. `review_pipeline.py`: `_REVIEW_PROMPTS` dict and `_REVIEW_CONVENTIONS_CONTEXT` replaced by single `_REVIEWER_PARAMS` config dict
  8. Enrichment is conditional: `enrich_task_title()` skipped when `task.description` is already non-empty
  9. 5 deleted files: `task_schema_context.md`, `project_rules_context.md`, `review_conventions_context.md`, `review_feasibility.md`, `review_adversarial.md`, `review_default.md`
  10. All existing tests pass; prompt loader tests updated for new file set; no unresolved `{{...}}` in any rendered prompt

### P2 -- Nice to Have

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
T-P1-115 depends on T-P1-113, T-P1-120
T-P1-116 depends on T-P1-114
T-P1-117, T-P1-118 independent
T-P1-120 independent (T-P1-119 completed)


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 120 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

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
