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
- **Sanity check result**: TypeScript clean (`npx tsc --noEmit`), Vite build clean, 11 stream_json tests pass, ruff clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-148 to Completed

## 2026-03-10 -- [T-P0-165] Persist task selection to localStorage for conversation recovery after page refresh
- **What I did**: Implemented localStorage persistence for selected task to recover conversation state after page refresh. Added two useEffect hooks in `useTaskState.ts`: one syncs selectedTask changes to localStorage (key: "helix_selected_task_id"), another restores persisted selection after tasks load (with deleted-task cleanup). Improved error handling in ConversationView.tsx fetchStreamLog catch block to log errors to console instead of silently swallowing. Enhanced backend endpoint `/api/tasks/{task_id}/stream-log` with explicit OSError handling for concurrent read scenarios and added `errors="replace"` to file.open() for robustness during active JSONL writes. Fixed two pre-existing TypeScript errors discovered during build: ConversationView.tsx line 435 (toolInput unknown → null check), types.ts PlanStatus missing "decomposed" value.
- **Deliverables**: `frontend/src/hooks/useTaskState.ts` (added localStorage sync + restore logic), `frontend/src/components/ConversationView.tsx` (improved error logging + TypeScript fix), `src/routes/dashboard.py` (enhanced stream-log endpoint error handling), `frontend/src/types.ts` (added "decomposed" to PlanStatus)
- **Sanity check result**: TypeScript clean build, 11 stream_json tests pass, Vite build successful, frontend compiles without errors. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-165 to Completed
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

## 2026-03-09 -- [T-P1-166] Verify subtask decomposition is functional end-to-end
- **What I did**: Traced full decomposition flow. All 4 components verified WORKING: (1) enrichment.py:520-534 returns proposed_tasks in plan_json. (2) enrichment.py:754-763 enforces M>=2, L>=3 subtask minimums via PlanValidationConfig. (3) task_manager.py:397-415 decomposition gate blocks RUNNING when has_proposed_tasks=True AND plan_status=READY (raises DecompositionRequiredError HTTP 428). (4) PlanReviewPanel.tsx renders ProposedTaskCard components with "Confirm and Create All Tasks" button, confirm_generated_tasks endpoint creates subtasks via task_generator.
- **Deliverables**: Investigation only, no code changes
- **Sanity check result**: All components verified via code trace. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-166 to Completed

## 2026-03-09 -- [T-P1-167] Verify task title hover-edit works for all Kanban card statuses
- **What I did**: Verified hover-edit across all statuses. Popover renders unconditionally (TaskCard.tsx:310 -- no status filter on showPopover). Pencil icon visible via group-hover opacity (TaskCardPopover.tsx:308-320 -- no status guard). Title save calls updateTask PATCH (TaskCardPopover.tsx:175). Mouse delay (150ms) from T-P1-156 wired correctly (TaskCard.tsx:138-160). Description edit also unconditional (TaskCardPopover.tsx:425-438).
- **Deliverables**: Investigation only, no code changes
- **Sanity check result**: Grep confirms no status-based filtering of edit UI. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-167 to Completed

## 2026-03-10 -- [T-P1-168/169] Fix sync consistency and auto-review status transition
- **What I did**: Two bug fixes: (1) T-P1-168: Added `update_task_title()` to `TasksWriter` that writes UI title edits back to TASKS.md, preventing next sync from overwriting them. Wired into PATCH handler as non-fatal write-back (like plan_status pattern). (2) T-P1-169: Added `expected_status` parameter to `TaskManager.update_status()` for atomic conditional transitions (no-op on mismatch, not error). Used in auto-review trigger to safely do BACKLOG->REVIEW before enqueuing review pipeline, fixing the bug where task stayed in BACKLOG and review pipeline hit ValueError on REVIEW->REVIEW_AUTO_APPROVED.
- **Deliverables**: `src/tasks_writer.py` (new method), `src/task_manager.py` (new param), `src/routes/tasks.py` (title write-back + auto-review transition), `tests/test_tasks_writer.py` (5 new tests), `tests/test_task_manager.py` (3 new tests)
- **Sanity check result**: 73 tests pass (test_tasks_writer + test_task_manager), ruff clean on all modified files. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P1-168 and T-P1-169 to Completed

## 2026-03-10 -- [T-P0-163] Comprehensive UI journey audit
- **What I did**: Conducted comprehensive audit of 9 user journeys spanning the entire HelixOS UI: (1) Project Import Flow (ImportProjectModal 3-step wizard, validation, override fields), (2) Task Creation Flows (NewTaskModal, InlineTaskCreator, 3 entry paths including AI enrichment), (3) Kanban Drag-Drop Lifecycle (5-column state machine, backward drag confirmation, decomposition gate), (4) Review Gate Flow (ReviewSubmitModal, 428 handling, plan validation), (5) Plan Generation & Decomposition (PlanReviewPanel, 5 plan_status states, confirm/reject/edit/delete actions), (6) Execution Monitoring (ExecutionLog, live SSE streaming, auto-scroll, filtering), (7) Review Pipeline UX (ReviewPanel, multi-round review, consensus scoring, human decision UI), (8) Filtering & Search (multi-select filters, search scope, priority/complexity chips), (9) LLM Prompt Design (enrichment_system.md, plan_system.md, review.md, _shared_rules.md template variable analysis). Identified 5 MEDIUM risks: P3 priority gap in NewTaskModal and enrichment prompt, race condition in ReviewSubmitModal (PATCH task + PATCH status as 2 separate calls), missing cancel-execution affordance in ExecutionLog, needs-human notification gap (no proactive toast/browser notification), review column grouping 3 sub-states without clear differentiation. Identified 11 LOW risks covering UX polish, missing validations, and workflow gaps. Created 66KB audit report with full user journey traces, conditional behavior documentation, risk summary table, and actionable recommendations.
- **Deliverables**: `docs/audits/ui-journey-audit-T-P0-163.md`, `TASKS.md` (updated), `PROGRESS.md` (this entry)
- **Sanity check result**: All 9 journeys traced via code review across ~20 frontend/backend files. All 3 LLM prompts analyzed for template variables, output formats, and scope constraints. Risk categorization follows MEDIUM (data integrity, workflow blocking, discoverability) vs LOW (polish, edge cases) severity model. Report format verified against acceptance criteria: flow descriptions, assessments, risk categorization, user journey traces, conditional behaviors documented, summary table present. [MANUAL-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-163 to Completed

## 2026-03-10 -- [T-P0-164] Fix review pipeline TOCTOU bugs
- **What I did**: Fixed 5 interacting TOCTOU bugs in the review pipeline that caused "Cannot move task from backlog to review_auto_approved" ValueError. (1) Moved pipeline enqueue inside `if refreshed.status == TaskStatus.REVIEW` guard in tasks.py. (2) Added `set_review_result()` to TaskManager for targeted review_json writes with `expected_status` guard, replacing full-object `update_task()` overwrite. (3) Added `expected_status=TaskStatus.REVIEW` on completion `update_status()` call. (4) Added status check in `_handle_replan` before re-enqueuing pipeline. (5) Added pre-flight status check before expensive LLM work. Also added REVIEW_NEEDS_HUMAN->REVIEW transition in replan flow.
- **Deliverables**: `src/task_manager.py` (new `set_review_result` method), `src/routes/reviews.py` (Fix 2-5), `src/routes/tasks.py` (Fix 1), `tests/test_review_pipeline_guards.py` (9 new tests), `LESSONS.md` (TOCTOU lesson #28)
- **Sanity check result**: 1621 tests pass (9 new + 1612 existing), ruff clean on all modified files. Pre-existing scheduler test timeout unrelated. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: No TASKS.md change (T-P0-164 is an audit task, not the fix task itself)

## 2026-03-10 16:40 -- [T-P0-164] Audit review: Verify findings accuracy and propose fix tasks
- **What I did**: Reviewed all findings in docs/audits (UI Journey Audit, Race Condition Audit, UX Task Audit) against current codebase. Verified 5 MEDIUM findings (P3 priority gap, review submission race, cancel-execution affordance, needs-human notification, review sub-status badges) and selected LOW findings. Found 2 audit inaccuracies: LOW-019 (Clear Filters button DOES exist at App.tsx:311-318, audit had incomplete code read), MEDIUM-003 (backend cancel endpoint EXISTS at src/routes/execution.py:426, gap is frontend-only). Updated audit docs with corrections: (1) LOW-019 marked as corrected with verification details, (2) MEDIUM-003 updated to note backend endpoint exists, (3) Known Omissions section added covering 7 unaudited areas (error boundary, dev server lifecycle, cost dashboard UX, multi-project import races, SSE reconnection, filter persistence edge cases, accessibility). Updated Race Condition Audit to note T-P1-169 expected_status mitigation for RACE-1 and RACE-4 review_lifecycle_state cleanup fix. Proposed 6 fix tasks in TASKS.md with full schema (priority, complexity, dependencies, description, user journey ACs, smoke test ACs): P1 tasks (Add P3 priority support, Add Cancel Execution button), P2 tasks (Atomic review submission endpoint, Review sub-status badges, Needs-human notification), P3 task (Filter persistence).
- **Deliverables**: `/tmp/audit-verification-T-P0-164.md` (verification tracking doc), `docs/audits/ui-journey-audit-T-P0-163.md` (updated: LOW-019 correction, MEDIUM-003 backend note, Known Omissions section), `docs/architecture/race-condition-audit.md` (updated: RACE-1 and RACE-4 mitigation notes), `TASKS.md` (6 proposed tasks added with full ACs), `PROGRESS.md` (this entry)
- **Sanity check result**: 40 task_manager tests pass, no regressions. Audit corrections verified against codebase (App.tsx:311-318 Clear button confirmed, src/routes/execution.py:426 cancel endpoint confirmed, task_manager.py:321,362,480 race mitigations confirmed). All 6 proposed tasks have proper schema (priority, complexity, description, 5+ ACs including user journey and smoke test). UX Task Audit reviewed and found accurate (no corrections needed). [MANUAL-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-164 to Completed

## 2026-03-10 -- [T-P0-8-fix] Atomic review pipeline completion (finalize_review)
- **What I did**: Fixed review pipeline TOCTOU Gap 1 (multi-field completion not atomic) and Gap 2 (lifecycle state set before pre-flight). Created `finalize_review()` method in TaskManager that atomically writes review_json, review_status, review_lifecycle_state, AND transitions task status in one DB session with one expected_status guard. Refactored `_run_review_bg()` to replace 4 separate calls (2 guarded, 2 unguarded) with single `finalize_review()` call. Moved `set_review_lifecycle_state(RUNNING)` to AFTER pre-flight check passes, so non-REVIEW tasks never get lifecycle_state=RUNNING written. Updated 15 tests (5 new for finalize_review atomicity, updated existing tests to use finalize_review).
- **Deliverables**: `src/task_manager.py` (new `finalize_review` method), `src/routes/reviews.py` (refactored `_run_review_bg`, reordered lifecycle/pre-flight), `tests/test_review_pipeline_guards.py` (15 tests, 5 new), `LESSONS.md` (lesson #29)
- **Sanity check result**: 15 review pipeline guard tests pass, 65 task_manager + drag_to_review tests pass, 1627 full suite tests pass (6 skipped). Pre-existing scheduler test timeout unrelated. Ruff clean. [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: No TASKS.md change (fix for blog-proj:T-P0-8 incident, not a tracked task)

## 2026-03-10 -- [T-P0-166] Bug fix: Preserve plan summary during regeneration
- **What I did**: Fixed bug where plan summary (description field) was cleared when user clicked Replan after review, causing context loss. Removed `row.description = ""` from GENERATING state in `set_plan_state()` (line 705 in task_manager.py). Updated docstring to document that GENERATING now preserves description so UI can show previous summary during regeneration, while still setting `has_proposed_tasks=False` (no valid plan_json to back it) and clearing plan_json. Added inline comments explaining the rationale. Updated 2 tests in test_plan_state_machine.py to reflect new behavior where description is preserved during all transitions to GENERATING state.
- **Deliverables**: `src/task_manager.py` (modified: removed description clearing, updated docstring, added comments), `tests/test_plan_state_machine.py` (updated 2 tests: test_none_to_generating, test_ready_to_generating)
- **Sanity check result**: 73 plan state machine tests pass, 383 plan-related tests pass, 40 task_manager tests pass. Frontend gracefully handles description + null plan_json: ReviewPanel shows old description as markdown during GENERATING (verified at line 865-869 in ReviewPanel.tsx), PlanReviewPanel shows spinner during GENERATING (line 119-140 in PlanReviewPanel.tsx). [AUTO-VERIFIED]
- **Status**: [DONE]
- **Request**: Move T-P0-166 to Completed
