# Completed Tasks Archive

> 16 completed tasks archived as of latest archival.

- [x] **2026-03-10** -- T-P0-163: test sample task. - Completed comprehensive UI journey audit covering 9 user flows (Project Import, Task Creation, Kanban Drag-Drop, Revie
- [x] **2026-03-10** -- T-P0-164: Audit findings review and propose corrective tasks. - Reviewed all findings in docs/audits against current codebase. Corrected 2 inaccuracies (LOW-019 Clear button exists, 
- [x] **2026-03-10** -- T-P0-165: Recover conversation from plain log after page refresh. - Implemented localStorage persistence for selected task ID with automatic restore after page refresh. Added two useEffe
- [x] **2026-03-10** -- T-P0-166: Fix plan summary being cleared after review completion. - Removed `row.description = ""` from GENERATING state in `set_plan_state()` to preserve plan summary during regeneratio
- [x] **2026-03-11** -- T-P0-167: Fix task workflow data flow after review completion. - Auto-approved tasks now transition REVIEW -> QUEUED directly (no REVIEW_AUTO_APPROVED intermediate state)
- request_ch
- [x] **2026-03-11** -- T-P0-168: Investigate blog_proj TASKS.md access and propose onboarding improvements. - Root cause: Claude Code tool-level permissions scoped to working directory. External projects unreachable from helixos
- [x] **2026-03-08** -- T-P1-106: Decompose App.tsx into container components and custom hooks. - Extracted App.tsx (1131 lines) into 4 custom hooks (`useToasts`, `useTaskState`, `useProjectState`, `useSSEHandler`) a
- [x] **2026-03-08** -- T-P1-108: Add Playwright E2E smoke test infrastructure. - Added Playwright E2E test infrastructure with `@playwright/test`, `playwright.config.ts` (Chromium headless, CI-compat
- [x] **2026-03-08** -- T-P1-109: Add cost/usage dashboard endpoint and frontend panel. - Added `GET /api/dashboard/costs` endpoint with single GROUP BY query (review_history JOIN tasks). `CostDashboard.tsx` 
- [x] **2026-03-08** -- T-P1-113: Extract agent prompts into config template files. - Created `config/prompts/` with 9 .md template files, `src/prompt_loader.py` with `load_prompt()`/`render_prompt()` (ca
- [x] **2026-03-08** -- T-P1-114: Add plan output pydantic validation with retry and error feedback. - Added `PlanValidationConfig` to config.py with configurable hard/soft limits. `ProposedTask` gains `files` field. `gen
- [x] **2026-03-10** -- T-P1-168: Write-back UI title edits to TASKS.md. - Added `update_task_title()` to TasksWriter + wired into PATCH handler. Prevents sync from overwriting UI title edits.
- [x] **2026-03-10** -- T-P1-169: Auto-review transitions task to REVIEW status (race-safe). - Added `expected_status` param to `update_status()` for atomic conditional transitions. Auto-review trigger now does BA
- [x] **2026-03-11** -- T-P1-171: Auto-sync Claude Code additionalDirectories on project import. - Created `src/settings_sync.py` syncing non-primary project paths from orchestrator_config.yaml to .claude/settings.loc
- [x] **2026-03-11** -- T-P1-172: Add P3 priority support to UI and enrichment. - Added P3 option to NewTaskModal dropdown, enrichment prompt, EnrichmentResult model, and JSON schema
- Added P3 color 
- [x] **2026-03-11** -- T-P1-173: Add Cancel Execution button to ExecutionLog. - Added "Cancel Execution" button to ExecutionLog header when task status is "running"
- Button shows confirmation dialo
