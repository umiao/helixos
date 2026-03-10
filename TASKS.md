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

#### T-P1-149: Collapse consecutive tool_use blocks in ConversationView
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Multiple consecutive tool_use blocks clutter the conversation. Group them into a single expandable section showing count (e.g. "3 tool calls") with individual tools inside.
- **Acceptance Criteria**:
  1. 2+ consecutive tool_use blocks grouped into single collapsible container
  2. Container shows tool count and summary (e.g. "3 tool calls: Read, Grep, Read")
  3. Individual tools still expandable within the group
  4. Single tool_use blocks render as before (no grouping)
  5. Journey: User scrolls conversation -> sees "5 tool calls" collapsed -> clicks -> expands to see individual tools -> clicks one to see details

#### T-P1-150: Add inline description editing to TaskCardPopover
- **Priority**: P1
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: TaskCardPopover supports title editing but NOT description. Add a textarea with save/cancel for inline description editing on hover.
- **Acceptance Criteria**:
  1. Description editable via pencil icon in TaskCardPopover (same pattern as title)
  2. Textarea with save (Enter or button) and cancel (Escape)
  3. Edits persist via PATCH /api/tasks/{id} with { description }
  4. Works for all task states (backlog, plan, review, etc.)
  5. Journey: User hovers card -> popover appears -> clicks pencil on description -> edits -> saves -> text persists on refresh

#### T-P1-151: Enforce subtask decomposition in planner prompt + review validation
- **Priority**: P1
- **Complexity**: M (1-2 sessions)
- **Depends on**: None
- **Description**: T-P0-139 ran as monolithic task because planner didn't enforce decomposition. Fix the root cause: update planner prompt to require subtask generation for M/L complexity tasks, and add review validation that rejects plans without proper decomposition.
- **Acceptance Criteria**:
  1. Planner system prompt updated: M-complexity tasks MUST propose 2-4 subtasks, L-complexity 3-8 subtasks
  2. Review validation rejects plans for M/L tasks that contain 0 proposed_tasks
  3. S-complexity tasks exempt from decomposition requirement
  4. Existing plan generation tests updated to verify decomposition enforcement
  5. Journey: User creates M-complexity task -> generates plan -> plan contains proposed subtasks -> review validates decomposition

### P2 -- Nice to Have

#### T-P2-143: Rewrite historical non-English commit messages
- **Priority**: P2
- **Complexity**: S (< 1 session)
- **Depends on**: None
- **Description**: Two commits used raw Chinese input as commit messages. Rewrite via `git filter-repo`. Separated from T-P2-142 because this is a destructive git operation that shouldn't mix with feature development.
- **Acceptance Criteria**:
  1. `f31a013` rewritten to `[T-P0-139] Three QoL improvements: DB-persisted project selection, removed [PROGRESS] heartbeat logging, filtered log artifacts in Conversation view`
  2. `5ea7b4c` rewritten to `[T-P0-125] Review MD rendering, executor feedback verification, title inline edit`
  3. `[NEEDS-INPUT]` -- requires user confirmation before force push

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


> 21 completed tasks archived to [archive/completed_tasks.md](archive/completed_tasks.md).

#### [x] T-P1-148: Add thinking block rendering in ConversationView -- 2026-03-09
- Added THINKING event type to backend sdk_adapter.py (previously skipped). Frontend types updated with "thinking" type on StreamDisplayItem. normalizeStreamEvents handles both top-level thinking events and thinking content blocks inside assistant messages. ConversationView renders thinking blocks as collapsible sections (collapsed by default) with muted italic style, text preview when collapsed. Updated test to verify thinking emission instead of skipping; added empty-thinking skip test.

#### [x] T-P1-147: Remove redundant result banner from ConversationView -- 2026-03-09
- Removed green "Completed successfully" result banner from ConversationView. The `type === "result"` render block now returns null. All other message types (text, tool_use, tool_result) unaffected. Vite build clean.

#### [x] T-P1-146: Fix PlanReviewPanel markdown rendering -- 2026-03-09
- Fixed three root causes: (1) SSE race condition -- plan_status_change "ready" event did not include description, causing stale/empty description in optimistic update. Added description to SSE payload in tasks.py and reviews.py, threaded through planStatePatch and useSSEHandler. (2) Missing remark-gfm in MarkdownRenderer -- GFM features (tables, strikethrough) did not render. Added remarkGfm plugin. (3) Whitespace edge case -- changed truthiness check to .trim(). Added 6 regression tests for format_plan_as_text edge cases.

#### [x] T-P0-145: Design agent clarifying question protocol -- 2026-03-09
- Design doc created at `docs/architecture/clarifying-questions.md`. Covers: data model (ReviewQuestionRow table, ReviewQuestion Pydantic model), new AWAITING_ANSWERS lifecycle state with transition rules, 4 API endpoints (GET questions, POST answer, POST answer-all, POST skip), review prompt changes (questions field in ReviewResult JSON, resume prompt template), backend pause/resume flow in review pipeline, frontend UX (question cards with answer textareas in ReviewPanel, Q&A history display, amber banner), 4 new SSE event types, migration plan, edge case matrix (11 scenarios), and 6 implementation subtasks. Awaiting user review before implementation.

#### [x] T-P0-144: Fix ReviewPanel edit persistence bug + always-available Edit button -- 2026-03-09
- Fixed broken link in edit persistence chain: `onTaskUpdated` callbacks in App.tsx updated `tasks` array but not `selectedTask`, causing ReviewPanel to show stale description after save. Added `setSelectedTask` update to both BottomPanelContainer and SwimLane `onTaskUpdated` callbacks. Changed Edit button gating from `review_lifecycle_state` check to `task.status` check (visible unless done/running). TypeScript clean, Vite build clean, 1478 tests pass.

#### [x] T-P2-142: Enrichment title generation + commit message CJK guard -- 2026-03-09
- Enrichment prompt now returns `{title, description, priority}`. `EnrichmentResult` and JSON schema updated with `title` field (maxLength 80). `_parse_enrichment()` validates title is ASCII-safe (discards CJK). Added `original_title` column to TaskRow with auto-migration + backfill. Task model, response schema, and api_helpers updated. `commit_msg_guard.py` PreToolUse hook blocks CJK in git commit messages, registered in settings.json. 1405 tests pass, ruff clean.

#### [x] T-P2-141: Security hardening -- cleanup personal paths, accidental files, hook enforcement -- 2026-03-09
- Replaced hardcoded Windows user paths in orchestrator_config.yaml with ~/. git rm'd accidental =0.1.40 pip output and untracked .claude/settings.local.json. Expanded secret_guard.py with PEM/personal-path patterns and sensitive file blocking. Added .gitignore rules for =*, *.pem, *.key, settings.local.json. Removed stale heartbeat tests. Added LESSONS.md entry #27.

#### [x] T-P0-139: Three QoL improvements: DB-persisted project selection, removed [PROGRESS] heartbeat logging, filtered log artifacts in Conversation view -- 2026-03-09
- (1) Added `ui_preferences` table to db.py with get/set_preference helpers, GET/PUT `/api/ui-preferences/{key}` endpoints in projects.py, fetchSelectedProjects/saveSelectedProjects in api.ts, updated useProjectState to load from API with debounced saves (1s) and localStorage fallback, flush on beforeunload. (2) Removed on_log [PROGRESS] emission in code_executor.py (lines 337-343), keeping timeout checks. (3) In ConversationView, added regex filter `/^\[(RESULT|TOOL|INIT|DONE|PROGRESS)\]/` to skip log text prefixes, added indigo left border to assistant text bubbles, fixed TypeScript errors (pre-existing) by adding React.ReactNode types to ReactMarkdown component overrides. All acceptance criteria met: checkbox state persists across browsers, API fallback to localStorage works, [PROGRESS] lines removed from logs, Conversation tab shows clean structured view with no log clutter, Plain Log unchanged.

#### [x] T-P2-140: Document dirty state lesson in LESSONS.md -- 2026-03-09
- Added LESSONS.md entry #26 covering plan regeneration dirty state bug: context (151 inconsistent rows, stale UI), root cause (no state machine, scattered field clearing, no generation IDs), fix (set_plan_state + generation_id + planStatePatch), and 3 architectural principles. References T-P0-134, T-P0-135, T-P0-138, T-P0-124.

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
