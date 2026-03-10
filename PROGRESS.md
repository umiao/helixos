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
