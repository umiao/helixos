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

#### T-P1-125: Align plan and review prompt rule coverage
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: T-P1-124
- **Description**: Plan prompt has Scope Guidance, Few-Shot Example, Anti-Patterns but no State Machine Rules or Smoke Test Enforcement. Review prompt has State Machine and Smoke Test but no Anti-Patterns. This inconsistency causes the planner to generate plans the reviewer will reject for rule violations the planner was never told about, wasting LLM round-trips.
- **Acceptance Criteria**:
  1. Plan prompt includes all rules that the reviewer checks against (State Machine, Smoke Test)
  2. Review prompt includes Anti-Patterns section so reviewer can flag these patterns
  3. No rule exists in review prompt that is absent from plan prompt (planner must know what reviewer will check)
  4. Test: grep both rendered prompts for all rule section headers, verify coverage parity

#### T-P1-126: Rewrite plan_system.md with phased thinking and strict output contract
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: T-P1-124
- **Description**: plan_system.md tries to do architecture design and task decomposition simultaneously, diffusing the LLM's attention. Add explicit phased thinking guidance and a strict JSON output contract. Critically, complexity is NOT self-determined by the LLM -- the pipeline injects `complexity_hint` externally to prevent the LLM from gaming complexity to avoid decomposition work.
- **Acceptance Criteria**:
  1. Plan prompt includes 4-phase thinking guidance:
     - Phase 1: Analyze scope and identify approach
     - Phase 2: Design implementation steps with specific files
     - Phase 3: Define acceptance criteria that verify the approach
     - Phase 4: If `{{complexity_hint}}` is M or L, propose sub-tasks; otherwise skip
  2. `generate_task_plan()` in `src/enrichment.py` gains `complexity_hint: str = "S"` parameter
  3. Caller in `src/routes/tasks.py` determines complexity from task metadata and passes it
  4. `complexity_hint` is injected into the user prompt alongside title/description
  5. Strict JSON-only output contract: `RESPOND WITH JSON ONLY. No markdown fences, no preamble.`
  6. JSON parse fallback handles markdown fences and preamble text (verify existing `_parse_plan_result()` covers this, or add fallback)
  7. Shared rules come from `{{include:_shared_rules.md}}`, not copy-paste
  8. Test: render plan_system.md with complexity_hint="M", verify Phase 4 guidance present
  9. Test: render with complexity_hint="S", verify Phase 4 says to skip sub-tasks
  10. Test: plan with markdown-fenced JSON response is correctly parsed

#### T-P1-127: Add specific structural check items to review prompt
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: T-P1-123
- **Description**: Reviewer prompts use generic instructions like "check feasibility" instead of specific structural verification items. With structured plan_json now available (T-P1-123), reviewers can perform precise checks on steps, ACs, files, and dependency graphs. Adversarial reviewer should focus on plan-level logic gaps, not code-level security (reviewer has no code to inspect at plan stage).
- **Acceptance Criteria**:
  1. Feasibility reviewer (`_REVIEWER_PARAMS["feasibility_and_edge_cases"]`) includes:
     - "For each step: is it actionable (specific files, specific changes)?"
     - "Does at least one AC verify each step's outcome?"
     - "Are listed files consistent with the codebase structure?"
  2. Adversarial reviewer (`_REVIEWER_PARAMS["adversarial_red_team"]`) includes:
     - "Do proposed_tasks dependencies form a DAG (no cycles)?"
     - "Is each proposed task independently testable?"
     - "Are there hidden assumptions or missing boundary conditions?"
     - "Does the plan risk scope creep beyond the original task description?"
  3. No OWASP/security checks in plan-only review context (no code available to inspect)
  4. Test: mock reviewer call, verify user content includes structural check prompts

#### T-P1-128: Add pass/fail calibration example to review prompt
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: The review prompt tells the LLM to "set pass to true if acceptable" but gives no examples of what constitutes a pass vs fail. This leads to inconsistent severity thresholds across reviews. Add a few-shot example showing a passing review and a failing review with blocking issues.
- **Acceptance Criteria**:
  1. Review prompt includes at least one example of a passing review response (minor suggestions, pass=true)
  2. Review prompt includes at least one example of a failing review response (blocking issues, pass=false)
  3. Examples clearly show the threshold: what severity/type of issue should block vs suggest
  4. Test: render review prompt, verify examples are present in output

#### T-P1-129: Remove dead synthesis code from review pipeline
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: `ReviewPipeline._synthesize()` (review_pipeline.py:795-870) and `_SYNTHESIS_JSON_SCHEMA` (review_pipeline.py:94-101) are fully implemented but never called -- `review_task()` uses deterministic merge instead. Dead code adds maintenance burden and confuses readers.
- **Acceptance Criteria**:
  1. `_synthesize()` method removed from ReviewPipeline class
  2. `_SYNTHESIS_JSON_SCHEMA` constant removed
  3. `_parse_synthesis()` method removed
  4. `SynthesisResult` model removed (if no other consumers)
  5. All existing tests still pass
  6. No remaining references to removed code

#### T-P1-130: Parallelize review pipeline reviewer calls
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: `review_task()` in review_pipeline.py:320 runs reviewers sequentially in a `for` loop. For M/L complexity tasks with 2+ reviewers, this doubles the review wait time. Reviewers are independent and can be parallelized with `asyncio.gather()`.
- **Acceptance Criteria**:
  1. Multiple reviewers run concurrently via `asyncio.gather()` or equivalent
  2. Progress callbacks still report per-reviewer completion (completed count increments)
  3. If one reviewer fails, the other's result is still captured (partial result)
  4. Single-reviewer case is unchanged (no regression)
  5. Test: mock two reviewers, verify both are called concurrently (not sequentially)

### P2 -- Nice to Have

#### T-P2-131: Move reviewer personas from Python to config templates
- **Priority**: P2
- **Complexity**: S (< 1 session)
- **Depends on**: T-P1-124
- **Description**: The 3 reviewer personas (`_REVIEWER_PARAMS` dict in review_pipeline.py:111-141) are hardcoded as Python strings. Moving them to config files would make them easier to iterate on without code changes.
- **Acceptance Criteria**:
  1. Reviewer role and questions defined in config files (YAML or prompt templates)
  2. `_REVIEWER_PARAMS` dict populated from config instead of hardcoded
  3. Adding a new reviewer persona requires only config changes, not code changes
  4. Existing reviewer behavior unchanged
  5. Test: override reviewer config, verify new persona is used

#### T-P2-132: Fix misleading enrichment prompt text about plan context
- **Priority**: P2
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: `enrichment_system.md` line 9 says "This prompt receives plan context when available" but enrichment is typically called on new tasks before plan generation, so plan context is rarely if ever available. The claim is misleading.
- **Acceptance Criteria**:
  1. If plan context IS wired into enrichment calls, document where; if NOT, remove the misleading line
  2. Verify `enrich_task_title()` call sites to confirm whether plan context is ever passed

#### T-P2-133: Remove unused generate-tasks-preview endpoint or wire it into frontend
- **Priority**: P2
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: `POST /api/tasks/{task_id}/generate-tasks-preview` endpoint exists in tasks.py but the frontend `confirm-generated-tasks` flow doesn't call it -- going straight to confirm without preview. Either remove the dead endpoint or add a preview step in PlanReviewPanel before confirm.
- **Acceptance Criteria**:
  1. Either: endpoint removed and tests cleaned up
  2. Or: PlanReviewPanel shows a diff preview before user confirms task creation
  3. No dead API endpoints remain

## Dependency Graph

> Full historical dependency graph relocated to [docs/architecture/dependency-graph-history.md](docs/architecture/dependency-graph-history.md).

### Current
T-P1-115 depends on T-P1-113, T-P1-120 (both completed -- T-P1-115 now unblocked)
T-P1-116 depends on T-P1-114 (completed -- T-P1-116 unblocked)
T-P1-125 depends on T-P1-124 (completed -- T-P1-125 now unblocked)
T-P1-126 depends on T-P1-124 (completed -- T-P1-126 now unblocked)
T-P1-127 depends on T-P1-123 (completed -- T-P1-127 now unblocked)
T-P2-131 depends on T-P1-124 (completed -- T-P2-131 now unblocked)


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 120 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

- T-P0-121: Fix complexity parameter not passed to review pipeline
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
