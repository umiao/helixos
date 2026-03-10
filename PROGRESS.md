# Progress Log

> Append-only session log. Each session adds an entry at the bottom.
> Never edit previous entries.
>
> **Size invariant**: Keep under ~300 lines. When exceeded, older entries are archived to [archive/progress_log.md](archive/progress_log.md).
> 200 session entries archived as of 2026-03-09.

<!-- Entry format:

## YYYY-MM-DD HH:MM -- [T-XX-N] Brief Title
- **What I did**: 1-3 sentences on concrete actions taken
- **Deliverables**: List of files created/modified
- **Sanity check result**: What I verified and the outcome
- **Status**: [DONE] Done / [PARTIAL] Partial (what remains) / [BLOCKED] Blocked (why)
- **Request**: Cross off TASK-XXX / Move TASK-XXX to In Progress / No change

-->

## 2026-03-08 -- [T-P1-109] Add cost/usage dashboard endpoint and frontend panel
- **What I did**: Added `GET /api/dashboard/costs` endpoint with single GROUP BY query joining `review_history` with `tasks` to aggregate per-project cost data. Created `ProjectCostSummary` and `CostDashboardResponse` schemas. Added `get_cost_summary()` to `HistoryWriter`. Frontend: created `CostDashboard.tsx` component with formatted USD table (project name, reviews, total cost, avg cost, grand total row), empty state handling. Added "Costs" tab to `BottomPanelContainer`. Updated panel type union across `useTaskState`, `useSSEHandler`, and `BottomPanelContainer`. 4 new backend tests.
- **Deliverables**: `src/routes/dashboard.py` (new endpoint), `src/schemas.py` (2 new schemas), `src/history_writer.py` (new method), `frontend/src/components/CostDashboard.tsx` (new), `frontend/src/components/BottomPanelContainer.tsx` (updated), `frontend/src/hooks/useTaskState.ts` (updated), `frontend/src/hooks/useSSEHandler.ts` (updated), `frontend/src/api.ts` (new function), `frontend/src/types.ts` (2 new interfaces), `tests/test_api.py` (4 new tests)
- **Sanity check result**: TypeScript clean (`npx tsc --noEmit`), Vite build clean, 1363 Python tests pass + 6 skipped, ruff clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-109 to Completed

## 2026-03-08 -- [T-P1-108] Add Playwright E2E smoke test infrastructure
- **What I did**: Added Playwright E2E test infrastructure to the frontend. Installed `@playwright/test` as dev dependency, installed Chromium browser. Created `playwright.config.ts` with Chromium headless project, CI-compatible settings (forbidOnly, retries, single worker), trace/screenshot on failure. Created 4 test files in `frontend/e2e/` with 12 tests total: `page-load.spec.ts` (dashboard loads, Kanban columns visible, header buttons), `task-card.spec.ts` (card rendering, task ID/title, status badge), `task-click.spec.ts` (click opens bottom panel, panel shows content), `project-filter.spec.ts` (filter bar, status dropdown options, search input, priority chips). Added `npm run e2e` and `npm run e2e:headed` scripts.
- **Deliverables**: `frontend/playwright.config.ts`, `frontend/e2e/page-load.spec.ts`, `frontend/e2e/task-card.spec.ts`, `frontend/e2e/task-click.spec.ts`, `frontend/e2e/project-filter.spec.ts`, `frontend/package.json` (updated scripts + devDep)
- **Sanity check result**: TypeScript clean (`npx tsc --noEmit`), Vite build clean, 1359 Python tests pass + 6 skipped, `npx playwright test --list` discovers all 12 tests in 4 files. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-108 to Completed

## 2026-03-08 -- [T-P1-106] Decompose App.tsx into container components and custom hooks
- **What I did**: Extracted App.tsx (1131 lines, 59+ state variables) into 4 custom hooks and 1 container component. Created `useToasts.ts` (toast state management), `useTaskState.ts` (tasks, filters, selected task, log entries, stream events, and all task handlers), `useProjectState.ts` (projects, selected projects, syncing, sync handlers), `useSSEHandler.ts` (SSE event handler construction + connection via useSSE). Created `BottomPanelContainer.tsx` encapsulating tab bar + panel rendering (ConversationView, ExecutionLog, ReviewPanel, RunningJobsPanel). App.tsx is now a thin composition layer (~280 lines) that calls hooks and renders components.
- **Deliverables**: `frontend/src/hooks/useToasts.ts`, `frontend/src/hooks/useTaskState.ts`, `frontend/src/hooks/useProjectState.ts`, `frontend/src/hooks/useSSEHandler.ts`, `frontend/src/components/BottomPanelContainer.tsx`, `frontend/src/App.tsx` (rewritten)
- **Sanity check result**: TypeScript clean (`npx tsc --noEmit`), Vite build clean, 1359 Python tests pass + 6 skipped, `npx playwright test --list` discovers all 12 tests in 4 files. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-106 to Completed

## 2026-03-08 -- [T-P1-113] Extract agent prompts into config template files
- **What I did**: Moved all inline prompt constants from `enrichment.py`, `review_pipeline.py`, and `code_executor.py` into 9 `.md` template files under `config/prompts/`. Created `src/prompt_loader.py` with `load_prompt(name)` (UTF-8, module-level cache) and `render_prompt(name, **kwargs)` for `{{variable}}` substitution. Updated all 3 source files to use the loader. Added 6 new tasks (T-P1-113 through T-P1-118) to TASKS.md.
- **Deliverables**: `config/prompts/` (9 files: enrichment_system, task_schema_context, project_rules_context, plan_system, review_conventions_context, review_feasibility, review_adversarial, review_default, execution_prompt), `src/prompt_loader.py`, updated `src/enrichment.py`, `src/review_pipeline.py`, `src/executors/code_executor.py`, `tests/test_prompt_loader.py`
- **Sanity check result**: 1383 tests pass + 6 skipped (20 new), ruff clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-113 to Completed

## 2026-03-09 -- [T-P1-148] Add thinking block rendering in ConversationView
- **What I did**: Added THINKING event type to backend `sdk_adapter.py` -- ThinkingBlock content is now emitted as `ClaudeEvent(type=THINKING, thinking=...)` instead of being silently skipped. Frontend: added `"thinking"` to `StreamDisplayItem.type` and `StreamContentBlock.type` in `types.ts`. Updated `normalizeStreamEvents` to handle both top-level `thinking` events and thinking content blocks inside `assistant` messages. Added collapsible thinking block renderer in ConversationView: collapsed by default showing "Thinking" label + preview, expandable to full reasoning text. Visual treatment: muted gray-500 italic text, semi-transparent bg, distinct from regular text messages. Updated existing test from "thinking skipped" to "thinking emitted" and added empty-thinking skip test.
- **Deliverables**: `src/sdk_adapter.py`, `frontend/src/types.ts`, `frontend/src/components/ConversationView.tsx`, `tests/test_sdk_adapter.py`
- **Sanity check result**: 37 sdk_adapter tests pass, 217 core tests pass, 144 review/plan tests pass, ruff clean, Vite build clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-148 to Completed

## 2026-03-09 -- [T-P1-149] Collapse consecutive tool_use blocks in ConversationView
- **What I did**: Added grouping logic to ConversationView that identifies consecutive tool_use runs and renders groups of 2+ as a single collapsible container. Container shows tool count and name summary (e.g. "3 tool calls: Read, Grep, Read"). When expanded, individual tool_use blocks are shown inside, each still individually expandable to show input/output. Single tool_use blocks render unchanged (no grouping wrapper). Extracted `renderToolUse` helper to avoid duplication between single and grouped rendering.
- **Deliverables**: `frontend/src/components/ConversationView.tsx`
- **Sanity check result**: Vite build clean, TypeScript clean, 188 unit tests pass. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-149 to Completed

## 2026-03-09 -- [T-P1-150] Add inline description editing to TaskCardPopover
- **What I did**: Added inline description editing to TaskCardPopover following the existing title editing pattern. Description section now always visible (shows "No description" placeholder when empty). Clicking the pencil icon opens a textarea with Save/Cancel buttons. Ctrl+Enter saves, Escape cancels, blur saves. Edits persist via PATCH /api/tasks/{id} with { description }. No backend changes needed -- API already supported description updates.
- **Deliverables**: `frontend/src/components/TaskCardPopover.tsx`
- **Sanity check result**: TypeScript clean, Vite build clean, 1643 Python tests pass. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-150 to Completed

## 2026-03-09 -- [T-P1-151] Enforce subtask decomposition in planner prompt + review validation
- **What I did**: Updated planner system prompt (plan_system.md) with explicit per-complexity decomposition requirements: M must propose 2-4 subtasks, L must propose 3-8 subtasks, S is exempt. Added `min_proposed_tasks_m` (default 2) and `min_proposed_tasks_l` (default 3) to PlanValidationConfig. Extended `_validate_plan_structure` to accept `complexity_hint` and reject plans with insufficient decomposition. Updated review.md to flag missing decomposition as a FAIL condition. Added 11 new tests covering unit validation, configurable limits, and integration with `generate_task_plan`. Fixed 3 existing tests that needed proposed_tasks for M/L complexity hints.
- **Deliverables**: `config/prompts/plan_system.md`, `config/prompts/review.md`, `src/config.py`, `src/enrichment.py`, `tests/test_plan_generation.py`, `tests/test_prompt_eval.py`, `tests/factories.py`
- **Sanity check result**: 1578 tests pass (6 skipped), ruff clean. Scheduler tests hang on Windows asyncio (pre-existing). [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-151 to Completed

## 2026-03-09 -- [T-P0-152] Fix ConversationView event normalization (invisible content)
- **What I did**: Fixed `normalizeStreamEvents()` to handle backend `sdk_adapter.py` event types. Added `type: "text"` as primary handler (was only checking `"assistant"`), causing all assistant text to be silently dropped. Added `type: "init"` (silently ignored), `type: "error"` (red error bubble). Fixed field name mismatches: backend uses `tool_name`/`tool_input`/`tool_use_id`/`tool_result_content`/`tool_result_for_id` but normalizer was reading `name`/`input`/`id`/`content`/`tool_use_id`. Also fixed `result` event to read `result_text` field. Added `errorMessage` field and `"error"` type to `StreamDisplayItem`.
- **Deliverables**: `frontend/src/components/ConversationView.tsx`, `frontend/src/types.ts`
- **Sanity check result**: TypeScript clean, Vite build clean, 1576 Python tests pass. Grep-verified wiring: error type flows through normalization -> display entries -> render. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-152 to Completed

## 2026-03-09 -- [T-P2-143] Rewrite historical non-English commit messages
- **What I did**: Verified that both target commits (`f31a013` -> `d4a02ef`, `5ea7b4c` -> `4c0b50f`) already have the correct English messages in the current git history. No non-ASCII commit messages remain. The rewriting was already done as part of T-P2-142. No code changes needed.
- **Deliverables**: `TASKS.md` (updated status)
- **Sanity check result**: `git log --oneline --all` shows all commits have ASCII-only messages. Both target messages confirmed present. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P2-143 to Completed

## 2026-03-09 -- [T-P0-154] Set agent cwd for plan/review on imported projects
- **What I did**: Changed plan agent (`enrichment.py`) from `add_dirs=[repo_path]` to `cwd=str(repo_path)` so SDK auto-indexes the project directory. Threaded `repo_path` through the review pipeline (`review_task` -> `_call_reviewer` -> `_call_claude_sdk`) with `cwd` setting. Added `_resolve_repo_path()` helper in `routes/reviews.py` to look up project repo path from task's `project_id`. Updated all 4 `_enqueue_review_pipeline` call sites (reviews.py x3, execution.py x1) to pass `repo_path`. Also fixed `get_session_context()` call in review pipeline to pass `repo_path`.
- **Deliverables**: `src/enrichment.py`, `src/review_pipeline.py`, `src/routes/reviews.py`, `src/routes/execution.py`, `tests/test_plan_generation.py`
- **Sanity check result**: 276 related tests pass (plan_generation, review_pipeline, enrichment, plan_review, replan, review_gate, drag_to_review, start_all_planned). Ruff clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-154 to Completed

## 2026-03-09 -- [T-P0-153] Fix plan edit persistence (description/plan_json desync)
- **What I did**: Fixed PATCH `/api/tasks/{id}` endpoint to route description edits through `plan_json["plan"]` when the task has a plan, then re-derive `description` via `format_plan_as_text()`. Added `plan_json` field to `TaskResponse` schema and `_task_to_response()` so frontend can access plan data. Audited all `plan_json` write paths (generate, replan, PATCH) -- generate and replan were already correct.
- **Deliverables**: `src/routes/tasks.py`, `src/schemas.py`, `src/api_helpers.py`, `tests/test_review_gate_ux.py`
- **Sanity check result**: 302 related tests pass (review_gate_ux, api, task_manager, plan_generation, plan_review, plan_validity, enrichment). 8 new regression tests for plan_json sync. Ruff clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-153 to Completed

## 2026-03-09 -- [T-P1-155] Add Edit button to PlanReviewPanel
- **What I did**: Added "Edit Plan" button to PlanReviewPanel's ready-state header. Clicking enters edit mode with textarea pre-filled with current plan text, Edit/Preview tabs (with MarkdownRenderer preview), and Save/Cancel buttons. Save persists via PATCH `updateTask()` and calls `onTaskUpdated` to refresh parent state (proposed tasks refresh via T-P0-153's plan_json support). All header action buttons disabled during edit mode to prevent conflicting actions.
- **Deliverables**: `frontend/src/components/PlanReviewPanel.tsx`
- **Sanity check result**: TypeScript clean, Vite build clean, 302 Python tests pass (api, task_manager, review_gate_ux, plan_generation, plan_review, plan_validity, enrichment). [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-155 to Completed

## 2026-03-09 -- [T-P1-157] Investigate T-P0-139 decomposition failure (RCA)
- **What I did**: Investigated why T-P0-139 was executed as a single large task without decomposition. Traced git history, TASKS.md versions, and progress logs. Found that T-P0-139 was never formally specified in TASKS.md -- it was an ad-hoc bundle of 3 QoL improvements created and completed in a single session, bypassing the planning pipeline entirely. The task ID also collided with existing T-P2-139 (Test suite consolidation). Decomposition enforcement (T-P1-151) was committed ~2.75 hours after T-P0-139 on the same day, so even if the planner had been invoked, no enforcement existed yet.
- **Deliverables**: `TASKS.md` (RCA findings in completed entry, task closed)
- **Sanity check result**: Root cause verified via git log timestamps (T-P0-139: 16:14:10, T-P1-151: 18:57:46), TASKS.md history (no T-P0-139 spec in any prior commit), and code trace (_validate_plan_structure only enforces M/L after T-P1-151). No code fix needed. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-157 to Completed

## 2026-03-09 -- [T-P1-156] Fix inline task edit across all statuses
- **What I did**: Fixed popover disappearing before user can interact with it. Root cause: popover renders via `createPortal` to `document.body`, so moving mouse from card to popover triggers card's `onMouseLeave`, closing popover instantly. Fix: added 150ms delayed close on card's `onMouseLeave`, with `onMouseEnter`/`onMouseLeave` props on popover to cancel/trigger the delayed close. This allows the mouse to travel from card to popover without losing it. Works for all statuses (backlog, review, queued, running, done, etc.) since the bug was status-independent.
- **Deliverables**: `frontend/src/components/TaskCard.tsx`, `frontend/src/components/TaskCardPopover.tsx`
- **Sanity check result**: TypeScript clean, Vite build clean, 1570 Python tests pass (scheduler excluded due to Windows timeout). Grep-based wiring verification: props threaded from TaskCard -> TaskCardPopover, handlers attached to popover div. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-156 to Completed

## 2026-03-09 -- [T-P2-158] Clarifying question workflow for review
- **What I did**: Implemented full clarifying question workflow spanning data model, backend API, and frontend UI. Added `ReviewQuestion` Pydantic model with id/text/answer/source_reviewer/timestamps. Extended `ReviewState` with `questions` list (stored in `review_json`). Updated review pipeline to extract questions from LLM structured output (`questions` field) with fallback extraction of `?`-ending sentences from suggestions/blocking_issues. Added `questions_json` column to `ReviewHistoryRow` (migration-safe). Created `POST /api/tasks/{task_id}/review/answer` endpoint. Injected answered questions into replan feedback. Built frontend Q&A UI in ReviewPanel: unanswered questions with answer textarea + submit button (violet theme), answered questions as compact green cards.
- **Deliverables**: `src/models.py`, `src/review_pipeline.py`, `src/routes/reviews.py`, `src/schemas.py`, `src/api_helpers.py`, `src/history_writer.py`, `src/db.py`, `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/components/ReviewPanel.tsx`, `tests/test_review_questions.py`, `tests/test_review_models.py`
- **Sanity check result**: 1568 Python tests pass (16 new), TypeScript clean, Vite build clean. Grep-based wiring: `answerReviewQuestion` API -> `ReviewPanel.tsx` import + call, `ReviewQuestion` type in `ReviewState` + `ReviewHistoryEntry`, `questions_json` column in DB. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P2-158 to Completed

## 2026-03-09 -- [T-P0-159] Fix session_factory not stored on app.state
- **What I did**: Fixed `AttributeError` crash on `GET/PUT /api/ui-preferences/{key}` caused by `session_factory` never being assigned to `app.state` in lifespan handler. Added `app.state.session_factory = session_factory` in `api.py`. Added defensive `getattr` guards in both preference endpoints in `projects.py`. Wired `session_factory` into test fixture and added 3 new preference endpoint tests.
- **Deliverables**: `src/api.py`, `src/routes/projects.py`, `tests/test_api.py`
- **Sanity check result**: 67 test_api tests pass (3 new), ruff clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-159 to Completed

## 2026-03-09 -- [T-P0-160] Redesign Conversation tab -- collapse tool use, show only AI replies
- **What I did**: Added consecutive text event merging in ConversationView. Extended `displayEntries` grouping to detect runs of consecutive `text` items and render them as a single merged bubble (`text_group` kind) joined with `\n\n`. tool_use blocks were already collapsed by default (via empty `expandedTools` Set), tool_results already hidden unless parent expanded, thinking blocks already collapsed. Also added 8 new tasks to TASKS.md (T-P0-160 through T-P1-167).
- **Deliverables**: `frontend/src/components/ConversationView.tsx`, `TASKS.md`
- **Sanity check result**: TypeScript clean, Vite build clean. Grep confirms expandedTools/toggleExpand wiring intact. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-160 to Completed

## 2026-03-09 -- [T-P0-161] Fix markdown rendering in Plan and Review tabs
- **What I did**: MarkdownRenderer was missing `rehype-prism-plus` plugin -- code blocks rendered without syntax highlighting. Added `import rehypePrism from "rehype-prism-plus"` and added it to `rehypePlugins` prop with `ignoreMissing: true`. Content prop wiring verified correct across all 7 usage sites (PlanReviewPanel x2, ReviewPanel x5). Prism CSS theme already loaded globally in main.tsx.
- **Deliverables**: `frontend/src/components/MarkdownRenderer.tsx`
- **Sanity check result**: TypeScript clean, Vite build clean. Grep confirms all MarkdownRenderer usages pass content prop correctly. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-161 to Completed

## 2026-03-09 -- [T-P0-162] Verify executor receives reviewer approval and replan feedback (RCA)
- **What I did**: Traced full data flow from reviewer verdict to executor prompt. Found 3 broken links: (1) `_build_replan_feedback()` in reviews.py ignored `blocking_issues` from LLMReview -- only used suggestions/summary. Fixed: added blocking_issues loop before suggestions. (2) `ReviewHistoryRow` had no `blocking_issues_json` column -- blocking issues were lost on DB persistence. Fixed: added column to db.py, write in history_writer.py `write_review()`, read in `get_reviews()`. Migration handled by `_migrate_missing_columns()`. (3) `build_review_feedback()` in scheduler.py didn't include blocking_issues or answered clarifying questions in execution prompt. Fixed: added both.
- **Deliverables**: `src/routes/reviews.py`, `src/db.py`, `src/history_writer.py`, `src/scheduler.py`
- **Sanity check result**: 1604 tests pass (6 skipped), ruff clean on changed files. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-162 to Completed

## 2026-03-09 -- [T-P1-164] Add animated status dots to Review tab + unify dot colors
- **What I did**: Added review status dot based on `review_lifecycle_state`: blue pulse for running/partial, solid green for approved, solid red for rejected_single/rejected_consensus/failed. Unified Conversation/Log tab dots from green to blue (matching "in-progress" semantic). All tabs now use consistent color scheme: blue=in-progress, green=success, red=failure.
- **Deliverables**: `frontend/src/components/BottomPanelContainer.tsx`
- **Sanity check result**: TypeScript clean, Vite build clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-164 to Completed

## 2026-03-09 -- [T-P1-163] Redesign Plain Log visual hierarchy with role-based highlighting
- **What I did**: Added content role detection from message prefix patterns ([TOOL], [RESULT], [PROGRESS]/[DONE] = system, else = AI output). Each role gets distinct visual treatment: AI text = bright gray-100 + indigo left border, tool calls = cyan text + cyan border, tool results = muted gray-400 + gray border, progress = dim gray-500 (no border). Level badges (ERROR/WARN) remain visible as overlay. Hid INFO level badge as redundant noise. Left border provides instant visual scanning.
- **Deliverables**: `frontend/src/components/ExecutionLog.tsx`
- **Sanity check result**: TypeScript clean, Vite build clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-163 to Completed

## 2026-03-09 -- [T-P1-165] Auto-trigger review after plan generation
- **What I did**: Wired auto-review trigger at both plan completion points. In `routes/tasks.py`, after initial plan generation sets plan_status=ready, auto-enqueue review via `_enqueue_review_pipeline` (deferred import to avoid circular). In `routes/reviews.py`, the replan completion path already had auto-enqueue but lacked dedup guard. Added idempotent dedup: check `review_lifecycle_state != RUNNING` before triggering to prevent concurrent duplicate reviews if manual trigger races with auto-trigger.
- **Deliverables**: `src/routes/tasks.py`, `src/routes/reviews.py`
- **Sanity check result**: 1604 tests pass (6 skipped), ruff clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-165 to Completed
