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

<!-- T-P1-113 completed, moved to Completed Tasks -->

## Active Tasks

### P0 -- Must Have (core functionality)


### P1 -- Should Have (agentic intelligence)

#### T-P1-113: Extract agent prompts into config template files
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Move all inline prompt constants from Python source into `config/prompts/` as separate .md files. Create `src/prompt_loader.py` with `load_prompt(name)` (UTF-8, cached). Version via git history, not filename versioning. Pure refactor -- no behavior change.
- **Acceptance Criteria**:
  1. `config/prompts/` directory with files: `plan_system.md`, `task_schema_context.md`, `project_rules_context.md`, `review_feasibility.md`, `review_adversarial.md`, `review_default.md`, `review_conventions_context.md`, `execution_prompt.md`
  2. `src/prompt_loader.py` with `load_prompt(name: str) -> str` (UTF-8, module-level cache, FileNotFoundError on missing)
  3. `src/enrichment.py` replaces `_PLAN_SYSTEM_PROMPT`, `_TASK_SCHEMA_CONTEXT`, `_PROJECT_RULES_CONTEXT` with `load_prompt()` calls
  4. `src/review_pipeline.py` replaces `_REVIEW_PROMPTS`, `_DEFAULT_REVIEW_PROMPT`, `_REVIEW_CONVENTIONS_CONTEXT` with `load_prompt()` calls
  5. `src/executors/code_executor.py` `_build_prompt()` uses loaded template with variable substitution
  6. All existing tests pass without modification (behavior-identical refactor)
  7. New tests: `load_prompt()` returns non-empty for each file, raises on missing, prompts contain expected key phrases

#### T-P1-114: Add plan output pydantic validation with retry and error feedback
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Plan agent output must go through strict pydantic validation before entering review. On parse failure, retry up to 2 times with validation error fed back to the LLM. Implement as output parser in `generate_task_plan()`, not a separate pipeline stage. Task scope enforced via configurable soft limits (warnings) and hard ceilings (rejects).
- **Acceptance Criteria**:
  1. `ProposedTask` pydantic model enforces required fields: title, description, files (list[str]), dependencies (list[str])
  2. `generate_task_plan()` validates output against pydantic model; on failure, retries with error message appended to prompt (max 2 retries)
  3. Hard ceiling: proposed_tasks max 10, dependencies must form DAG (via `detect_cycles()`)
  4. Soft limits (logged as warnings, not blocking): tasks >8, steps >12 per task, files >8 per task
  5. Scope limits configurable in `orchestrator_config.yaml` under `plan_validation` section
  6. All existing tests pass; new tests for validation pass/fail/retry scenarios

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

#### T-P1-119: Add reject-to-replan loop and enrich execution prompt with plan data
- **Priority**: P1
- **Complexity**: M (1-2 sessions)
- **Depends on**: None
- **Description**: Two flow gaps: (1) When review rejects a plan, the only options are re-review (same plan) or human override -- there is no "regenerate plan incorporating review feedback" path. Add a `"replan"` decision to the review decide endpoint that feeds review suggestions back to `generate_task_plan()` for re-generation (max 2 replan attempts). (2) The execution prompt only receives title + description text; the structured `plan_json` (implementation steps with files, acceptance criteria) stored in DB is never fed to the executor. Inject it.
- **Acceptance Criteria**:
  1. `submit_review_decision()` accepts `decision="replan"` alongside approve/reject/request_changes
  2. When user picks "replan": `plan_status` transitions to `"generating"`, `replan_attempt` increments, `generate_task_plan()` called with review suggestions as structured feedback
  3. After successful replan: `plan_status="ready"`, review pipeline auto-enqueued for the new plan
  4. Max 2 replan attempts enforced; 3rd attempt returns 409 with clear message
  5. When replan is NOT chosen (approve/reject/request_changes): existing behavior unchanged
  6. `generate_task_plan()` gains `review_feedback: str | None` param; when provided, appends structured "address these issues" block to user prompt
  7. `Task` model gains `replan_attempt: int = 0` field with DB migration in `init_db()`
  8. `_build_prompt()` in `code_executor.py` parses `task.plan_json` when available and injects `## Implementation Steps` (numbered, with files) and `## Acceptance Criteria` (checklist) into execution prompt
  9. Graceful fallback: if `plan_json` is None or malformed, execution prompt uses description-only (no crash)
  10. Journey AC: User generates plan -> review rejects -> user picks "replan" -> new plan generated with feedback -> auto-review runs -> approve -> execution prompt contains structured steps + ACs + review feedback
  11. All existing tests pass; new tests for replan flow (decision handling, limit enforcement, feedback injection, auto-review trigger) and execution prompt enrichment (with/without plan_json)

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
T-P1-119 independent
T-P1-120 depends on T-P1-119


---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks

> 99 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P1-113: Extract agent prompts into config template files -- 2026-03-08
- Created `config/prompts/` with 9 .md template files, `src/prompt_loader.py` with `load_prompt()`/`render_prompt()` (cached, UTF-8). Replaced inline prompt constants in `enrichment.py`, `review_pipeline.py`, `code_executor.py`. 20 new tests. 1383 pass, ruff clean.

#### [x] T-P1-109: Add cost/usage dashboard endpoint and frontend panel -- 2026-03-08
- Added `GET /api/dashboard/costs` endpoint with single GROUP BY query (review_history JOIN tasks). `CostDashboard.tsx` component with formatted USD table, "Costs" tab in bottom panel. 4 new tests. 1363 pass, TS clean, Vite build clean.

#### [x] T-P1-106: Decompose App.tsx into container components and custom hooks -- 2026-03-08
- Extracted App.tsx (1131 lines) into 4 custom hooks (`useToasts`, `useTaskState`, `useProjectState`, `useSSEHandler`) and `BottomPanelContainer` component. App.tsx is now ~280 lines of pure composition. 1359 tests pass, TS clean, Vite build clean, 12 Playwright tests discovered.

#### [x] T-P1-108: Add Playwright E2E smoke test infrastructure -- 2026-03-08
- Added Playwright E2E test infrastructure with `@playwright/test`, `playwright.config.ts` (Chromium headless, CI-compatible), and 4 test files (12 tests) in `frontend/e2e/`: page-load (Kanban columns + header), task-card (card rendering + status badges), task-click (bottom panel activation), project-filter (filter bar + search + priority chips). Added `npm run e2e` script. 1359 Python tests pass, TS clean, Vite build clean.

#### [x] T-P1-105: Split api.py into domain-specific route modules -- 2026-03-07
- Split src/api.py (2470 lines) into 5 route modules under src/routes/ (dashboard, execution, projects, reviews, tasks) + src/api_helpers.py for shared helpers. api.py retained lifespan, middleware, create_app(), router mounting (323 lines). All 1359 tests pass unmodified, ruff clean.

#### [x] T-P1-110: Add task filtering by priority and complexity -- 2026-03-07
- Added priority (P0/P1/P2/P3) and complexity (S/M/L) multi-select filter chips to filter bar. Priority extracted from local_task_id, complexity from description. AND-composed with existing status/project/search filters. Clear button resets both. 1359 pass, TS clean, Vite build clean.

#### [x] T-P1-112: Extract dependency_graph module from scheduler.py -- 2026-03-07
- Created src/dependency_graph.py with validate_dependency_graph(), detect_cycles(), extract_priority(). scheduler.py and task_manager.py import from new module. task_generator.py deduplicated cycle detection via shared detect_cycles(). 1359 pass, ruff clean.

#### [x] T-P0-111: Inject review suggestions into re-execution prompt -- 2026-03-07
- Added `build_review_feedback()` to scheduler.py (caps at last 3 reviews, includes suggestions + summary + human_reason). Scheduler fetches review history before execution and passes formatted feedback through `_run_with_retry` -> `executor.execute()` -> `_build_prompt()`. No new DB fields needed (uses existing `get_reviews()`). 10 new tests (7 build_review_feedback + 3 _build_prompt). 1359 pass, ruff clean.

#### [x] T-P0-107: Add React ErrorBoundary to crash-prone components -- 2026-03-07
- Created reusable ErrorBoundary.tsx (componentDidCatch, fallback UI with component name + error + retry button). Wraps entire bottom panel in App.tsx. KanbanBoard/header remain functional on crash. 1350 pass, TS clean, Vite build clean.

#### [x] T-P0-102: Project research and improvement decomposition -- 2026-03-07
- Researched codebase (99 tasks, 30+ modules, 25+ components). Identified top improvements: monolith splitting (api.py/App.tsx/scheduler.py), ErrorBoundary, review feedback loop, E2E tests, cost dashboard, priority filtering. Decomposed into T-P0-107 through T-P1-112 (8 tasks).

#### [x] T-P2-103: Tool block structured rendering -- 2026-03-07
- Refactored tool_use + tool_result into single bordered blocks with collapsed-by-default summary (tool name + input detail + line count). Single expand/collapse per block, no nested accordions. Orphaned tool_results render gracefully. 1350 pass, TS clean, Vite build clean.

#### [x] T-P2-102: Markdown + code syntax highlighting via Prism -- 2026-03-07
- Added rehype-prism-plus, remark-gfm, prism-themes to frontend. Wired remark-gfm + rehype-prism-plus into ReactMarkdown in ConversationView. Imported prism-one-dark CSS theme. Added 5KB size guard (strips language tag from large code blocks). 1350 pass, Vite build clean.

#### [x] T-P2-104: ExecutionLog filter UX improvement -- 2026-03-07
- Replaced level filter dropdown with multi-select toggle chips (ERROR, WARN, INFO) + "More" dropdown (DEBUG). Active chips show colored ring. Clear button resets filters. 1350 pass, TS clean, Vite build clean.

#### [x] T-P2-101: Typography + contrast improvements for log display -- 2026-03-07
- ConversationView body text bumped to 14px (`text-sm`), headings scaled up. ExecutionLog message text bumped to 13px. All badges normalized to 12px (`text-xs`). Contrast improved across both components. 1350 pass, ruff clean, TS clean.

#### [x] T-P2-100: Clean up plan log display (hide raw JSON artifacts) -- 2026-03-07
- Excluded `level='artifact'` entries from `get_logs()`/`count_logs()` by default. Added `include_artifacts` param to both methods + API endpoint. Artifacts still persisted in DB for forensic access. 5 new tests. 1312 pass, ruff clean.

#### [x] T-P1-104: Task Generator -- deterministic proposal-to-TASKS.md pipeline -- 2026-03-07
- Pure Python task generator: extracts `proposed_tasks[]` from plan_json, allocates sequential IDs per priority, validates schema/dependencies, detects cycles (DFS), enforces max 8 tasks, generates human-readable diff. Two API endpoints: preview (GET diff) and confirm (write + auto-pause). Added `DECOMPOSED` plan status. 43 new tests. 1308 pass, ruff clean.

#### [x] T-P1-103: Selective hooks loading for plan/review agents -- 2026-03-07
- Added `setting_sources` to QueryOptions/ClaudeAgentOptions. Plan and review agents use `setting_sources=[]` to disable CLI hooks. Created `session_context_loader.py` to inject session context into system prompts. Execution agent unchanged. 16 new tests. 1292 pass, ruff clean.

#### [x] T-P1-102: Enrich review prompt with project conventions -- 2026-03-06
- Injected CLAUDE.md rules (task planning, state machine, smoke test, key constraints) + TASKS.md schema conventions into all review prompts. Imported shared context from enrichment.py. Upgraded adversarial reviewer + synthesis model to opus 4.6. 4 new tests. 1244 pass, ruff clean.

#### [x] T-P1-101: Enrich plan prompt with project context + proposed_tasks schema -- 2026-03-06
- Enriched plan prompt with CLAUDE.md rules + TASKS.md schema conventions. Extended JSON schema with `proposed_tasks[]` (title, desc, priority, complexity, deps, ACs). Added `ProposedTask` model, `MAX_TASKS_PER_PLAN=8` validation. 13 new tests. 1273 pass, ruff clean.

#### [x] T-P1-100: Enable plan mode + upgrade plan model to opus 4.6 -- 2026-03-06
- Changed `generate_task_plan()` to use `model="claude-opus-4-6"` (was `claude-sonnet-4-5`) and `permission_mode="plan"` (read-only). Updated test assertion. 1260 pass, ruff clean.

#### [x] T-P0-99: Auto-sync frontend board after drag and task completion -- 2026-03-06
- Added `board_sync` SSE event on all task state changes (api, scheduler). Frontend debounced (500ms) full refetch on board_sync events. StartAllPlanned gets success toast via onStarted callback. 3 new tests. 1259 pass, ruff clean, TS clean, Vite build clean.

#### [x] T-P0-100: Fix stop/cancel task signal propagation -- 2026-03-06
- Root cause: no frontend cancel mechanism + no backend auto-cancel on RUNNING status change. Added `cancelTask()` API, "Stop Execution" context menu button, backend auto-cancel in `update_task_status()`. 4 new tests. 1257 pass, ruff clean, TS clean.

#### [x] T-P0-101: Priority-based dependency-aware queue scheduling + cycle detection -- 2026-03-06
- Added `extract_priority()` helper and priority-sorted `get_ready_tasks()`. Added `validate_dependency_graph()` with DFS cycle detection and missing-ref validation. Enhanced `_deps_fulfilled()` with missing-ref alerts. Fixed tick over-fetch. 16 new tests, 79 scheduler tests pass, ruff clean.

#### [x] T-P3-83: Done column ordering fix -- 2026-03-06
- Root cause: `completed_at` only set for DONE status, not FAILED/BLOCKED. Fixed to set on all terminal transitions and clear on backward transitions. Frontend sort/filter already existed.

#### [x] T-P2-82: UX audit + smoke test enforcement -- 2026-03-06
- Audited 34 completed UX tasks against 5 Task Planning Rules. Key finding: 0/34 tasks had manual browser smoke tests documented. Added "Smoke Test Enforcement" section (3 rules) and planning rule #6 "New-field consumer audit" to CLAUDE.md. Full audit at docs/audits/ux-task-audit.md.

#### [x] T-P2-81: PRD clarification (Pause/Gate/Start All Planned semantics) -- 2026-03-06
- Added PRD section 5.4 defining Pause (scheduler dispatch only), Review Gate (two-layer review enforcement), and Start All Planned (batch operation). Includes per-control behavior tables and 7 edge case combinations. Answers: Pause does NOT affect review pipeline.

#### [x] T-P2-80: State machine diagram documentation -- 2026-03-06
- Created `docs/architecture/state-machine.md` documenting both TaskStatus (9 states, 22 transitions) and ReviewLifecycleState (7 states, 16 transitions) state machines. Includes ASCII diagrams, transition tables with triggers and side-effects, guards/gates, backward cleanup matrix, cross-machine interactions, and race condition references.

#### [x] T-P2-75: Raw-response decoupling postmortem integration test -- 2026-03-06
- Integration test `test_raw_response_decoupled_from_parsed_fields` validates 5 decoupling invariants: raw_response metadata keys disjoint from parsed fields, correct SDK metadata, correct parsed extraction, result mirroring, and no field leakage. 1248 pass, ruff clean.

#### [x] T-P1-85: Replace Launch button with "Start All Planned Tasks" -- 2026-03-06
- Added `POST /api/projects/{project_id}/start-all-planned` endpoint. Batch-moves BACKLOG tasks with plan_status=ready: gate ON -> REVIEW + pipeline, gate OFF -> QUEUED. Optimistic locking per-task. Created `StartAllPlanned.tsx`, replaced LaunchControl in SwimLaneHeader, deleted `LaunchControl.tsx`. 8 new tests. 1209 pass, ruff clean, TS clean.

#### [x] T-P1-77: Scheduler finalization epoch ID -- 2026-03-06
- Added `execution_epoch_id` to TaskRow/Task model. Scheduler generates UUID epoch on dispatch, verifies before DONE/FAILED finalization. Epoch cleared on backward transitions. 11 new tests. 1201 pass, ruff clean.

#### [x] T-P1-76: State machine transition race condition audit -- 2026-03-06
- Audit doc in `docs/architecture/race-condition-audit.md` covering 8 race windows with severity ratings and mitigations. Fixed `_cleanup_on_backward()` to reset `review_lifecycle_state`. 11 new tests. 1228 pass, ruff clean.

#### [x] T-P1-73: Log retention/purge policy -- 2026-03-06
- Added `log_retention_days` (default 30) to OrchestratorSettings. Added `purge_old_entries()` to HistoryWriter (deletes execution_logs + review_history older than retention). Wired into lifespan startup. 4 new tests. 1206 pass, ruff clean.

#### [x] T-P2-99: Expose review conversation_turns in API + ReviewPanel -- 2026-03-06
- Added `conversation_turns_json`/`conversation_summary_json` columns to ReviewHistoryRow (auto-migrated). Updated write/get in history_writer. Added to ReviewHistoryEntry schema (Python + TS). Extracted TOOL_COLORS to shared `streamUtils.ts`. Added collapsible conversation section to ReviewPanel. 4 new tests. 1175 pass, ruff clean.

#### [x] T-P1-98: Add claude-agent-sdk to dependency smoke test -- 2026-03-06
- Added `claude_agent_sdk`, `ruamel.yaml`, `filelock` to `test_core_dependencies_importable()`. Synced `pyproject.toml` dependencies with `requirements.txt` (added 3 missing packages). Documented dependency source-of-truth convention in CLAUDE.md. 1171 tests pass, ruff clean.

#### [x] T-P2-91: Conversation extraction mock tests with real fixtures -- 2026-03-06
- Created 3 JSON fixtures (review, execution, enrichment sessions) in `tests/fixtures/`. 37 tests verify `collect_turns()` produces non-empty turns/actions, `_extract_conversation_summary()` extracts findings/actions/conclusion, and `ClaudeResult.structured_output` is correctly populated. All deterministic. 1211 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-90: Remove dead subprocess boilerplate -- 2026-03-06
- Removed `_StreamJsonBuffer`, `_simplify_stream_event()`, `_terminate_process_group()`, `_kill_process_group()`, `_truncate_stderr()`, `MAX_STDERR_BYTES` from code_executor.py. Removed `SUBPROCESS_STREAM_LIMIT` from config.py. Removed 31 associated tests. Also committed T-P1-70 (extract `_is_process_alive()` to `platform_utils.py`). Net reduction: 512 lines. 1174 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-70: Extract _is_process_alive() to shared module -- 2026-03-06
- Deduplicated `_is_process_alive()` from port_registry.py, process_manager.py, subprocess_registry.py into `src/platform_utils.py` with proper `sys.platform` guard. All 3 callsites import from shared module. 1205 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-89: Migrate review_pipeline.py + conversation extraction -- 2026-03-06
- Replaced `_call_claude_cli()` subprocess with `_call_claude_sdk()` using `run_claude_query()` + producer-task + queue pattern. Added `conversation_turns` and `conversation_summary` fields to `LLMReview` model. Integrated `collect_turns()` for turn reconstruction and `_extract_conversation_summary()` for structured findings/actions/conclusion. Removed subprocess process-group helpers. Updated 98 review_pipeline tests + 4 integration tests + 1 subprocess_stream_limit test. Full suite: 1205 pass + 4 skipped, ruff clean.

#### [x] T-P1-88: Migrate code_executor.py to Agent SDK -- 2026-03-06
- Replaced `asyncio.create_subprocess_exec` in `CodeExecutor.execute()` with `run_claude_query()` from `sdk_adapter`. Uses producer-task + queue pattern with 30s heartbeat, manual time-based session/inactivity timeout (avoids nested asyncio.timeout/wait_for conflicts). Preflight check updated to verify SDK importability. Cancel support via producer task cancellation. Updated 76 tests (+ stream_json + subprocess_stream_limit) to mock `run_claude_query` instead of subprocess. 1208 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-87: Migrate enrichment.py to Agent SDK -- 2026-03-06
- Replaced both `asyncio.create_subprocess_exec` calls in `enrichment.py` with `run_claude_query()` from `sdk_adapter`. `enrich_task_title()` iterates SDK events for structured output. `generate_task_plan()` uses producer-task + queue pattern for heartbeat-safe streaming. `is_claude_cli_available()` updated to check SDK import. 94 tests updated, full suite 1218 pass + 4 skipped, ruff clean.

#### [x] T-P1-86: Claude Agent SDK adapter layer -- 2026-03-06
- Added `claude-agent-sdk>=0.1.40` to requirements.txt. Created `src/sdk_adapter.py` with Pydantic models (ClaudeEvent, AssistantTurn, ToolAction, ClaudeResult, QueryOptions), `run_claude_query()` async iterator wrapping SDK `query()`, and `collect_turns()` for conversation reconstruction. SDK flag spike documented inline (permission_mode, add_dirs, max_budget_usd). No JSONL logging in adapter. 33 new tests in `tests/test_sdk_adapter.py`. 1219 tests pass + 4 skipped, ruff clean.

#### [x] T-P1-84: Persist plan_status to TASKS.md (bidirectional sync) -- 2026-03-06
- Added `plan_status: str | None` to ParsedTask, parser extracts `- **Plan**: <value>` with whitelist validation. TasksWriter.update_task_plan_status() inserts/updates Plan field with .bak backup. upsert_task() respects None=DB-wins semantics. API generate_plan writes Plan=ready to TASKS.md (non-fatal). 23 new tests. 1186 tests pass + 4 skipped, ruff clean.

#### [x] T-P0-97: Add real-CLI integration test for stream pipeline -- 2026-03-06
- Created `tests/integration/test_stream_cli.py` with 4 tests covering execution (CodeExecutor), plan generation (generate_task_plan), review (_call_claude_cli), and API endpoint (stream-log). Tests use `@pytest.mark.cli_integration` and are auto-skipped unless `-m cli_integration` is passed. Added `pytest_collection_modifyitems` hook in `tests/conftest.py` for auto-skip. Registered `cli_integration` marker in `pyproject.toml`. 1159 tests pass + 4 skipped, ruff clean.

#### [x] T-P0-96: Fix log creation strategy -- lazy file creation + cleanup -- 2026-03-06
- Added `_LazyFileWriter` class that defers file creation to first `write()` call, preventing empty log files. Replaced eager `open()` in code_executor.py, enrichment.py, review_pipeline.py. Added `cleanup_empty_log_files()` for startup cleanup of 0-byte files, wired into `lifespan()`. 11 new tests. 1159 tests pass, ruff clean.

#### [x] T-P0-95: Enable stream-json for plan generation + ConversationView -- 2026-03-06
- Switched `generate_task_plan` from `--output-format json` to `stream-json --verbose`. Added `_StreamJsonBuffer` parsing, JSONL persistence (`plan_stream_*.jsonl` + `plan_raw_*.log`), `on_stream_event` callback, partial buffer flush at EOF. Wired SSE `execution_stream` emission with `origin="plan"` in `api.py`. Result still correctly extracted from stream `result` event's `structured_output`. Added 5 new tests (CLI args, event callback, None safety, multi-event, JSONL persistence). 1148 tests pass, ruff clean. AC5 (manual smoke test) deferred to T-P0-97.

#### [x] T-P0-94: Enable stream-json for review pipeline + ConversationView -- 2026-03-06
- Switched `_call_claude_cli` from `--output-format json` to `stream-json --verbose`. Added `_StreamJsonBuffer` parsing, JSONL persistence (`review_stream_*.jsonl`), `on_stream_event` callback threaded through `review_task` -> `_call_reviewer` -> `_call_claude_cli` -> `_synthesize`. Wired SSE `execution_stream` emission in `api.py`. Result still correctly extracted from `result` event's `structured_output`. Added 5 new tests. 1143 tests pass, ruff clean. AC5 (manual smoke test) deferred to T-P0-97 (end-to-end verification).

#### [x] T-P0-93: Harden stream-json event parser + add --verbose flag -- 2026-03-06
- Replaced `--include-partial-messages` with `--verbose` in CLI args. Extended `_simplify_stream_event` to handle all 6 real event types: added `stream_event` (nested delta parsing), `system` init (`[INIT] model=X`), `user` (suppressed), `rate_limit_event` (suppressed). Added 8 new tests. 1138 tests pass, ruff clean. AC5/AC6 (real CLI verification) deferred to T-P0-97.

#### [x] T-P0-92: Fix schema/parsing in plan + review pipelines -- 2026-03-06
- Fixed all 5 callsites to read `structured_output` (dict) instead of `result` (null). Updated 4 parse functions to accept `str | dict` and skip `json.loads()` when already a dict. Updated all test helpers to simulate real `structured_output` field. 1131 tests pass, ruff clean.

#### [x] T-P0-91: Investigate CLI --json-schema output behavior -- 2026-03-06
- Confirmed root cause via official docs (code.claude.com/docs/en/headless): `--json-schema` puts output in `structured_output` field (object), NOT `result` (null). All 5 callsites in enrichment.py and review_pipeline.py read `result` and get null. Stream-json + --json-schema confirmed compatible. Documented 6 real stream-json event types vs 5 handled by parser. Added LESSONS #20 and #21.

#### [x] T-P0-90: Frontend Popover Enhancement -- 2026-03-06
- Enhanced TaskCardPopover with "Live Activity" section for running tasks: shows tool call count, elapsed minutes, and last activity (tool name or text snippet). Added StreamSummary type, computed via useMemo in App.tsx from streamEvents, threaded through SwimLane->KanbanBoard->TaskCard->TaskCardPopover. Non-running tasks unaffected. TypeScript clean, Vite build clean.

#### [x] T-P0-89: Frontend Conversation View -- 2026-03-06
- Created `ConversationView.tsx` with markdown assistant bubbles, collapsible color-coded tool badges, tool results matched by `tool_use_id`, result banner. Added `StreamEvent`/`StreamDisplayItem`/`StreamLogResponse` types, `fetchStreamLog` API. App.tsx handles `execution_stream` SSE (capped 2000/task), `viewMode` toggle between Conversation and Plain Log. TypeScript clean, Vite build clean.

#### [x] T-P0-87: Backend stream-json + Log Persistence -- 2026-03-06
- Switched `--output-format json` to `stream-json`. Added `_StreamJsonBuffer` for incremental JSON parsing, `on_stream_event` callback through executor chain, `_simplify_stream_event` for backward-compat `on_log`, JSONL persistence to `data/logs/{task_id}/`, `GET /api/tasks/{task_id}/stream-log` endpoint, `execution_stream` SSE event type. 26 new tests, 1124 total passing, ruff clean.
