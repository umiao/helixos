# Task Backlog

> **Convention**: Pick tasks from top of Active (highest priority first).
> Move to In Progress when starting. Move to Completed when done.
> PRD reference: helixos_prd_v0.3.md (single source of truth for architecture)

## In Progress
<!-- Only ONE task here at a time. Focus. -->

## Active Tasks

### P0 -- Must Have (core functionality)

#### T-P0-25: Token usage limit bar in UI top-right corner [NEEDS-INPUT]
- **Priority**: P0
- **Complexity**: M
- **Depends on**: None
- **Status**: Blocked -- requires research on reliable usage API endpoint (non-public internal API may change)

#### ~~T-P0-26: Fix drag-to-REVIEW workflow~~ [DONE -- see Completed Tasks]

#### ~~T-P0-27: Add planning quality rules to CLAUDE.md + LESSONS.md postmortem~~ [DONE -- see Completed Tasks]

#### ~~T-P0-28: Store full reviewer raw_response + surface in ReviewPanel~~ [DONE -- see Completed Tasks]

#### ~~T-P0-29: Upgrade primary reviewer to Opus + per-reviewer budget config + cost tracking~~ [DONE -- see Completed Tasks]

#### ~~T-P0-30: Subprocess inactivity timeout + process group cleanup for execution pipeline~~ [DONE -- see Completed Tasks]

#### ~~T-P0-31: Apply timeout to review pipeline subprocess calls~~ [DONE -- see Completed Tasks]

#### ~~T-P0-32: Review + execution progress phase reporting via SSE~~ [DONE -- see Completed Tasks]

#### T-P0-33: Fix review panel data bugs (T-P0-28 regressions)
- **Priority**: P0
- **Complexity**: M
- **Depends on**: None

**Problem**: 3 data-path bugs make ReviewPanel a rubber-stamp UI.

**AC**:

1. **raw_response stores explicit CLI fields** (not parsed result JSON):
   - `review_pipeline.py:_call_reviewer()`: build raw_response as:
     ```python
     raw_response = json.dumps({
         "model": cli_output.get("model"),
         "usage": cli_output.get("usage"),
         "result": cli_output.get("result"),
         "session_id": cli_output.get("session_id"),
     })
     ```
   - Explicit field extraction -- do NOT `json.dumps(cli_output)` blindly.
     Decouples DB schema from CLI contract.
   - Journey AC: User clicks "Show Full Response" -> sees model info,
     token counts (input/output), result text. This is data NOT already
     shown in summary/suggestions.
   - Inverse: When raw_response is empty/legacy -> section hidden (existing)
   - Invariant test: `assert set(json.loads(raw_response).keys()) - {"result"}`
     is non-empty (raw_response must contain fields beyond parsed result)

2. **Plan content visible in ReviewPanel**:
   - Collapsible "Plan Under Review" section at top showing `task.description`
   - When description is empty: show "(No plan content provided to reviewer)"
     -- explicit emptiness, NOT hidden section
   - Journey AC: User sees REVIEW_NEEDS_HUMAN task -> opens ReviewPanel ->
     reads plan text -> reads reviewer feedback -> makes informed decision

3. **Decision reason persisted E2E**:
   - `db.py`: add `human_reason TEXT NULL DEFAULT NULL` column to
     ReviewHistoryRow (auto-migrate, explicitly nullable)
   - `history_writer.write_review_decision()`: accept + persist `reason`
   - `api.py:submit_review_decision()`: pass `body.reason` through
   - API response: include `human_reason` in ReviewHistoryEntry
   - Frontend: display persisted reason below "Human decision:" label
   - Journey AC: User types "Need error handling for X" -> clicks Approve ->
     reloads -> reason appears in review history
   - Inverse: When reason is empty -> no reason line displayed

4. **Manual smoke test**: Open ReviewPanel for a reviewed task -> verify
   plan content visible, raw response has token usage data, submit decision
   with reason, reload and verify reason persists.

**Files**: `src/review_pipeline.py`, `src/history_writer.py`, `src/db.py`,
`src/api.py`, `src/schemas.py`, `frontend/src/types.ts`,
`frontend/src/components/ReviewPanel.tsx`, + regression tests

---

#### T-P0-34: Request Changes decision + human feedback loop
- **Priority**: P0
- **Complexity**: M
- **Depends on**: T-P0-33
- **Pre-implementation requirement**: Produce formal state machine diagram
  (ASCII in task description or separate doc) before writing any code.

**Problem**: Binary approve/reject is too coarse. No iteration.

**State Machine (must be formalized before coding)**:
```
BACKLOG --[submit to review]--> REVIEW (review_status=running)
REVIEW (running) --[pipeline completes]--> REVIEW_NEEDS_HUMAN
REVIEW_NEEDS_HUMAN --[approve]--> QUEUED
REVIEW_NEEDS_HUMAN --[reject]--> BACKLOG
REVIEW_NEEDS_HUMAN --[request_changes]--> REVIEW (review_status=idle)
REVIEW (idle) --[user edits plan + triggers re-review]--> REVIEW (running)

Illegal transitions (must be guarded):
- request_changes while review_status=running -> 409
- edit plan while review_status=running -> allowed (edit is on task,
  not review), but does NOT cancel running review
- re-review while review_status=running -> 409 (already running)

Semantics:
- reject = "this task should not be done" -> BACKLOG (terminal for this cycle)
- request_changes = "right direction, needs revision" -> stays in REVIEW
- approve = "proceed to execution" -> QUEUED
```

**AC**:

1. **"Request Changes" decision type**:
   - New decision: `"request_changes"` alongside approve/reject
   - Requires non-empty reason (400 if empty)
   - Backend: task transitions REVIEW_NEEDS_HUMAN -> REVIEW, review_status=idle
   - Human feedback persisted via write_review_decision with reason
   - Journey AC: User clicks "Request Changes" -> types "Add timeout handling"
     -> submits -> task stays in REVIEW column -> user can edit and re-review

2. **Feedback injection into re-review** (all previous feedback, not just latest):
   - On re-review, fetch ALL human feedback from previous attempts for this
     task, ordered by timestamp
   - Include plan version identifier with each feedback entry
   - Append to reviewer user prompt as "Previous human feedback" section
   - Token safety: practical limit ~5 iterations, not a concern
   - Journey AC: Attempt 1 reject -> Attempt 2 request_changes("add X") ->
     Attempt 3 request_changes("also fix Y") -> Attempt 4 reviewer sees
     both feedback entries in context

3. **Review attempt increment timing**:
   - Increment happens at pipeline start (not at human decision time)
   - Each automated reviewer run = one review_attempt
   - Human decisions do not increment attempt number

4. **Frontend 3-button decision area**:
   - Approve (green) -- reason optional -> QUEUED
   - Reject (red) -- reason optional -> BACKLOG
   - Request Changes (amber) -- reason REQUIRED -> REVIEW (idle)
   - When Request Changes selected: textarea border amber, placeholder
     "Describe the changes needed (required)", submit disabled if empty
   - After request_changes: show "Re-review" button

5. **Concurrent scenario guards**:
   - If review_status=running: decision buttons disabled with tooltip
     "Review in progress, please wait"
   - If user submits request_changes then immediately clicks Re-review:
     normal flow (request_changes sets idle, re-review sets running)

6. **Manual smoke test**: Review completes -> click Request Changes with
   feedback -> task stays REVIEW -> edit plan -> re-review -> verify
   reviewer prompt contains human feedback -> approve -> QUEUED.

**Files**: `src/models.py`, `src/api.py`, `src/review_pipeline.py`,
`src/history_writer.py`, `src/schemas.py`,
`frontend/src/components/ReviewPanel.tsx`, `frontend/src/api.ts`, + tests

---

#### T-P0-35: Inline plan editing + versioned review history
- **Priority**: P0
- **Complexity**: M
- **Depends on**: T-P0-34

**Problem**: No way to iterate on the plan itself before re-review.

**AC**:

1. **Inline plan editor in ReviewPanel**:
   - "Edit Plan" button on "Plan Under Review" section (from T-P0-33)
   - Toggles to textarea with Save/Cancel buttons
   - Save calls PATCH /api/tasks/{id} (existing endpoint)
   - When review_status=running: edit button disabled (keep simple)
   - Journey AC: User reads plan -> clicks Edit -> modifies -> saves ->
     clicks Re-review -> new review runs on updated plan

2. **Plan snapshot per review attempt**:
   - `db.py`: add `plan_snapshot TEXT NULL` column to ReviewHistoryRow
   - Each review_attempt stores immutable copy of task.description at
     pipeline start
   - Never reconstruct from current task.description
   - Snapshots are append-only, never updated

3. **Review attempt grouping in UI**:
   - ReviewPanel groups history entries by review_attempt
   - Each group header: "Attempt N" with timestamp
   - Human feedback entries shown between attempt groups
   - Journey AC: User sees Attempt 1 (original plan, reject), human
     feedback, Attempt 2 (edited plan, approve)

4. **Simple text diff after plan edit**:
   - When current plan differs from previous attempt's snapshot,
     show "Plan was modified" banner with collapsible unified diff
   - Pure text diff (no semantic diffing)
   - Journey AC: After editing plan and re-reviewing, diff shows
     what changed between attempts

5. **Manual smoke test**: Create task -> review rejects -> edit plan
   inline -> re-review -> verify attempt history with grouping and
   diff visible -> approve.

**Files**: `frontend/src/components/ReviewPanel.tsx` (editor + grouping),
`frontend/src/components/PlanDiffView.tsx` (new, simple text diff),
`src/db.py` (plan_snapshot column), `src/review_pipeline.py` (snapshot
storage), `src/history_writer.py` (write snapshot), + tests

---

### P1 -- Should Have (important features)

#### T-P0-36: Structured plan generation via Claude --plan
- **Priority**: P1 (evaluate feasibility first)
- **Complexity**: M
- **Depends on**: T-P0-35
- **Pre-implementation requirement**: Research Claude CLI `--plan` output
  format stability. If format is unstable or undocumented, defer task.

**Problem**: Plans are currently free-text task descriptions. Structured
plan generation could improve review quality.

**AC**:

1. **Feasibility assessment**: Document `--plan` output format, stability
   guarantees, and parsing requirements before implementation.
2. **Plan generation**: For M/L complexity tasks, offer "Generate Plan"
   button that calls Claude CLI with `--plan` flag.
3. **Plan output stored**: Generated plan stored as task.description,
   visible in ReviewPanel.
4. **Graceful degradation**: If --plan fails or format changes, fall back
   to raw description without breaking review pipeline.

<!-- All 7 P1 tasks completed (pre-T-P0-36). See Completed Tasks below. -->

### P2 -- Nice to Have (polish, optimization)
<!-- All 8 P2 tasks completed. See Completed Tasks below. -->

### P3 -- Phase 3: UX + Polish
<!-- All P3 tasks completed. See Completed Tasks below. -->

### Tech Debt (tracked, not blocking current work)
- [ ] T-P0-28 postmortem: integration test asserting raw_response contains fields not present in summary/suggestions
- [ ] Log retention/purge policy for execution_logs + review_history tables
- [ ] Unified timeout policy for enrichment CLI subprocess calls (review covered by T-P0-31)
- [ ] Unify subprocess management into shared `SubprocessRunner` abstraction (T-P0-30/T-P0-31 tech debt)
- [ ] Review state machine diagram documentation
- [ ] (from web UI) Done column: investigate random ordering, add sort/filter.
      Self-editing workflow: test changes then restart (queued stage only?).
- [ ] Audit completed UX tasks (T-P0-8a through T-P3-11) for scenario-matrix gaps
- [ ] Clarify Pause/Gate/Launch semantic boundaries in PRD (does Pause affect review pipeline?)

## Dependency Graph

```
T-P0-1 [S] Scaffold
  |
  +---> T-P0-2 [M] Models+DB+TaskManager
  |       |
  |       +---> T-P0-3 [S] Config ---> T-P0-4 [S] Parser ----+
  |       |                                                     |
  |       +---> T-P0-5 [M] Executor (also needs T-P0-11) -----+
  |       |       |                                             |
  |       |       +---> T-P0-6a [M] Scheduler core (also needs T-P0-4)
  |       |               |
  |       |               +---> T-P0-6b [M] Scheduler hardening
  |       |               |       |
  |       |               |       +---> T-P0-12 [S] Git auto-commit
  |       |               |
  |       |               +---> T-P0-9 [S] SSE endpoint
  |       |
  |       +---> T-P0-7 [M] Review pipeline
  |
  +---> T-P0-11 [S] Env loader
  |
  +---> T-P0-8a [S] Dashboard static
          |
          +---> T-P0-8b [M] Drag-drop+API (also needs T-P0-10)
          |
          +---> T-P0-8c [M] Log+Review+SSE (also needs T-P0-9)

T-P0-10 [L] API (needs T-P0-6b + T-P0-7 + T-P0-4)
T-P0-13 [M] Integration tests (needs T-P0-10 + T-P0-12)

--- P1 ---

T-P1-1 [M] Review pipeline refactor (no deps)
  |
  +---> T-P1-2 [S] API lifespan cleanup
  |
  +---> T-P1-3 [S] Remove API key deps
  |
  +---> T-P1-4 [M] Update tests

T-P1-5 [S] Fix config (no deps)
  |
  +---> T-P1-6 [M] QUICKSTART.md

T-P1-7 [S] E2E verification (needs T-P1-4 through T-P1-6)

--- P2 ---

T-P2-1 [S] Config extension (no deps)
  |
  +---> T-P2-2 [M] PortRegistry
  |       |
  |       +---> T-P2-3 [M] Validate/Import API ----------+
  |       |                                                |
  |       +---> T-P2-5 [M] ProcessManager [DONE] ----------+
  |                                                        |
  +---> T-P2-4 [M] TasksWriter [DONE] --------------------+
                                                           |
T-P2-6 [M] Frontend Swim Lanes [DONE] ------------------+
                                                           |
                                                    T-P2-7 [M] Frontend Operations UI [DONE]
                                                           |
                                                    T-P2-8 [S] E2E Integration

--- P0 (new, completed) ---

T-P0-18 [M] Review gate [DONE]
T-P0-19 [S] asyncio fix [DONE]
  |
  +---> T-P0-20 [S] Fix --loop none CLI crash [DONE]

--- P0 (new) ---

T-P0-21 [M] Fix review gate bypass [DONE]
  |
  +--> T-P0-23 [L] Bidirectional transitions + concurrency
         |
         +--> T-P0-24 [M] Review gate UX modal [DONE]

T-P0-22 [M] Soft-delete tasks [DONE]

--- P3 (new) ---

T-P3-12 [M] Resizable divider [DONE]

--- P0 (new -- review workflow fix + process rules) ---

T-P0-24 [M] Review gate UX modal [DONE]
  |
  +--> T-P0-26 [L] Fix drag-to-REVIEW [DONE]

T-P0-25 [M] Token usage limit bar [NEEDS-INPUT]

T-P0-27 [S] Planning quality rules [DONE] (no deps)

--- P0 (new -- review context + monitoring + liveness) ---

T-P0-28 [M] Full reviewer raw_response [DONE] (no deps)
T-P0-29 [S] Opus upgrade + cost tracking [DONE] (no deps)

T-P0-30 [M] Inactivity timeout + process groups [DONE] (no deps)
  |
  +--> T-P0-31 [S] Review pipeline timeout + retry semantics [DONE] (needs T-P0-30)
  |
  +--> T-P0-32 [M] Progress phase SSE (needs T-P0-28 + T-P0-30)

--- P0 (new -- review panel overhaul) ---

T-P0-33 [M] Fix review panel data bugs (no deps)
  |
  +--> T-P0-34 [M] Request Changes + feedback loop (needs T-P0-33)
         |
         +--> T-P0-35 [M] Inline plan editing + versioned history (needs T-P0-34)
                |
                +--> T-P0-36 [M] Claude --plan integration [P1] (needs T-P0-35)
```

---

## Blocked
<!-- Tasks that can't proceed and why -->

## Completed Tasks
<!-- Move finished tasks here with [x] and completion date -->

#### [x] T-P0-32: Review + execution progress phase reporting via SSE -- 2026-03-03
- Extended on_progress to (completed, total, phase) with "Starting {focus} review...", "Completed {focus} review", "Synthesizing..." phase strings. API forwards phase in SSE review_progress events. CodeExecutor emits [PROGRESS] log entries every 60s (elapsed, line count, since last output) via background task. Frontend: ReviewPanel shows live phase label, ExecutionLog shows live M:SS elapsed counter. SSE task_id guard: reviewPhase only updates for selected task, cleared on task switch. 11 new tests, 854 total passing.

#### [x] T-P0-31: Apply timeout to review pipeline subprocess calls -- 2026-03-03
- Process group isolation in _call_claude_cli (start_new_session / CREATE_NEW_PROCESS_GROUP). Timeout via asyncio.wait_for on proc.communicate() (review_timeout_minutes, default 10, 0=disabled). On timeout: SIGTERM -> 5s grace -> SIGKILL -> RuntimeError -> review_status=failed + SSE alert + Retry. Retry semantics: review_attempt column on ReviewHistoryRow (auto-migrated, default 1), get_max_review_attempt() query, next attempt = max+1. Synthesis step covered by same timeout. 23 new tests, 843 total passing.

#### [x] T-P0-30: Subprocess inactivity timeout + process group cleanup for execution pipeline -- 2026-03-03
- Process group isolation: start_new_session=True (Unix) / CREATE_NEW_PROCESS_GROUP (Windows) matching ProcessManager pattern. On timeout/cancel, entire process group killed via os.killpg/CTRL_BREAK_EVENT with SIGKILL fallback. Inactivity detection: per-line asyncio.wait_for(readline(), timeout) replaces async-for iteration. No output for inactivity_timeout_minutes (default 20, 0=disabled) -> INACTIVITY_TIMEOUT error type, process group terminated. Config: inactivity_timeout_minutes on OrchestratorSettings. 13 new tests, 820 total passing.

#### [x] T-P0-29: Upgrade primary reviewer to Opus + per-reviewer budget config + cost tracking -- 2026-03-03
- Primary reviewer upgraded to claude-opus-4-6 with max_budget_usd:2.00. Adversarial stays claude-sonnet-4-5 at 0.50. Per-reviewer max_budget_usd config field (default 0.50, backward compatible). _extract_cost_usd() computes approximate cost from CLI usage data with model-specific pricing table. cost_usd nullable column on ReviewHistoryRow (auto-migrated), persisted in HistoryWriter, returned via API. Frontend shows ~$X.XX cost badge per review entry (hidden when NULL). Synthesis stays claude-sonnet-4-5. 15 new tests, 807 total passing.

#### [x] T-P0-28: Store full reviewer raw_response + surface in ReviewPanel -- 2026-03-03
- Added raw_response TEXT column to ReviewHistoryRow (auto-migrated, 200KB truncation limit). Capture raw CLI result text in review_pipeline.py, persist in HistoryWriter, return via API. Frontend: collapsible "Show Full Response (debug)" section in ReviewPanel with amber warning banner, collapsed by default, hidden for legacy/empty entries. 8 new tests, 792 total passing.

#### [x] T-P0-27: Add planning quality rules to CLAUDE.md + LESSONS.md postmortem -- 2026-03-03
- Added 6 actionable rules to CLAUDE.md: Task Planning Rules (5 rules: scenario matrix, journey-first ACs, cross-boundary integration, "other case" gate, manual smoke test AC) and State Machine Rules (1 rule: document states/triggers/side-effects, backend owns side-effects). Added LESSONS.md entry #12 with T-P0-24 root cause analysis (missing scenario matrix, no journey-first AC, cross-boundary gap, no manual smoke test).

#### [x] T-P0-26: Fix drag-to-REVIEW workflow -- transition-driven pipeline + review_status -- 2026-03-03
- Transition-driven review pipeline: status transition to REVIEW auto-enqueues pipeline (sets review_status=running). Pipeline success -> review_status=done + transition to REVIEW_AUTO_APPROVED/REVIEW_NEEDS_HUMAN. Pipeline failure -> review_status=failed + SSE alert. Backward transitions reset to idle. POST /api/tasks/{id}/review repurposed as retry-only (409 if running). Frontend: auto-focus ReviewPanel on drag-to-REVIEW, review_status-based rendering (idle/running/done/failed), retry button. 25 new tests, 784 total passing.

#### [x] T-P0-24: Review gate UX -- edit modal + preview before review submission -- 2026-03-03
- PATCH /api/tasks/{id} endpoint for title/description updates. Frontend 428 detection opens ReviewSubmitModal with edit fields + live preview. PATCH-if-changed then BACKLOG->REVIEW transition. "Send to Review" context menu for BACKLOG/QUEUED tasks. Auto-focus in ReviewPanel on submit. Gate OFF = direct transition, no modal. 15 new tests, 759 total passing.

#### [x] T-P0-23: Bidirectional state transitions + concurrency control -- 2026-03-03
- Bidirectional VALID_TRANSITIONS (backward drags: REVIEW->BACKLOG, QUEUED->BACKLOG/REVIEW, DONE->BACKLOG/QUEUED, FAILED->BACKLOG). RUNNING stays strict (DONE/FAILED only). Timestamp cleanup matrix clears completed_at/execution_state on backward moves. OptimisticLockError with updated_at comparison (Z/+00:00 normalized). StatusTransitionRequest gains reason + expected_updated_at. API returns 409 with conflict=true on lock mismatch. Frontend: KanbanBoard backward-drag prompt, App.tsx sends expected_updated_at, auto-refresh on conflict. 52 new tests, 744 total passing.

#### [x] T-P0-22: Soft-delete tasks via context menu + API -- 2026-03-02
- is_deleted column + auto-migration, TaskManager.delete_task() with RUNNING/dependents guards, DELETE endpoint (204/404/409 with dependents list), frontend deleteTask + context menu Delete with confirmation and force-delete flow. 22 new tests, 692 total passing.

#### [x] T-P0-21: Fix review gate bypass -- 5 vulnerable paths -- 2026-03-02
- Fixed all 5 bypass paths: sync auto-promotion, execute, retry, review/decide, status endpoint. ReviewGateBlockedError returns 428 (not 409). 15 new regression tests, 670 total passing.

#### [x] T-P0-1: Project scaffold (FastAPI + React + SQLite) -- 2026-03-01
- Scaffold complete: pyproject.toml, requirements.txt, frontend (Vite+React+TS+Tailwind v4), orchestrator_config.yaml, contracts/, scripts/start.ps1, src/executors/, src/sync/

#### [x] T-P0-11: Unified .env loader + env injection -- 2026-03-01
- EnvLoader class with per-project key filtering, validation, missing-file handling, ANTHROPIC_API_KEY warning. 15 tests passing.

#### [x] T-P0-2: Data model + TaskManager + database layer -- 2026-03-01
- Pydantic models (TaskStatus 9 values, ExecutorType, Project, Task, ReviewState, LLMReview, ExecutionState, Dependency). SQLAlchemy 2.0 async DB (TaskRow, DependencyRow, indexes). TaskManager CRUD + state machine + startup recovery. 82 tests passing.

#### [x] T-P0-3: Project registry + YAML config loader -- 2026-03-01
- Pydantic settings models (OrchestratorSettings, ProjectConfig, GitConfig, ReviewerConfig, DependencyConfig, OrchestratorConfig). YAML loader with validation. ProjectRegistry with get_project, list_projects, get_project_config. Path expansion via expanduser. 33 tests passing.

#### [x] T-P0-4: TASKS.md parser (one-way sync) -- 2026-03-01
- TasksParser with regex-based T-P\d+-\d+ extraction, section-to-status mapping, configurable status_sections. sync_project_tasks async upsert (BACKLOG->QUEUED, DONE force-update). ParsedTask/SyncResult dataclasses. Edge cases: no IDs, duplicates, empty sections. 43 tests passing.

#### [x] T-P0-5: CodeExecutor (subprocess + timeout + streaming) -- 2026-03-01
- ExecutorResult model + BaseExecutor ABC (execute + cancel). CodeExecutor spawns claude CLI via asyncio.create_subprocess_exec with stdout streaming, timeout (terminate->grace->kill), cancel support, and _build_prompt per PRD 7.2. Last 100 log lines kept. All decoding UTF-8. 26 tests passing.

#### [x] T-P0-6a: Scheduler core (EventBus + tick loop + concurrency) -- 2026-03-02
- EventBus pub/sub (Event dataclass, emit/subscribe, bounded queues max 1000, drop oldest). Scheduler with tick loop (5s interval), per-project + global concurrency control, dependency checking, executor factory (CodeExecutor MVP), task execution (success->DONE, failure->FAILED), start/stop lifecycle. 35 tests passing (12 events + 23 scheduler).

#### [x] T-P0-6b: Scheduler hardening (retry + recovery + cancel) -- 2026-03-02
- _run_with_retry with exponential backoff (30s, 60s, 120s), max retries -> BLOCKED. startup_recovery marks orphaned RUNNING tasks as FAILED with alerts. cancel_task calls executor.cancel() + asyncio task cancel, updates FAILED. _auto_commit_hook placeholder. 39 tests passing (16 new + 23 existing scheduler).

#### [x] T-P0-12: Git auto-commit with staged safety check -- 2026-03-01
- GitOps.auto_commit with git add -A, staged file count via numstat, safety check (max_files limit), unstage+alert on abort, configurable commit message template. check_repo_clean utility. Wired into Scheduler._auto_commit_hook with try/except guard. 8 tests passing.

#### [x] T-P0-7: Review pipeline (Anthropic-only, opt-in, async) -- 2026-03-01
- ReviewPipeline with review_task (required + optional adversarial for M/L), _call_reviewer (Anthropic Messages API), _build_review_prompt (focus-area prompts), _parse_review (JSON -> LLMReview with fallback), _synthesize (multi-review consensus), SynthesisResult model. Scoring: approve=1.0, reject=0.3, multi=synthesized. Configurable threshold, on_progress callback. 20 tests passing.

#### [x] T-P0-9: SSE event stream endpoint -- 2026-03-01
- format_sse (Event -> SSE data frame), sse_stream async generator (EventBus subscriber with keepalive on idle), sse_router (GET /api/events, StreamingResponse, text/event-stream). Disconnect cleanup via generator finally. Event JSON: {type, task_id, data, timestamp}. 21 tests passing.

#### [x] T-P0-8a: Dashboard Kanban -- static layout + TaskCard -- 2026-03-01
- TypeScript interfaces matching backend Pydantic models. API client stubs with mock data (5 tasks). KanbanBoard 5 columns (BACKLOG, REVIEW, QUEUED, RUNNING, DONE). TaskCard with project ID, task ID, title, status badge, dependency indicator. App layout with header (title, Sync All, running count), filter bar (project, status, search). npm run build succeeds.

#### [x] T-P0-10: API endpoints (CRUD + sync + execute + review + lifespan) -- 2026-03-01
- FastAPI app with lifespan (init DB, config, services, startup_recovery, scheduler start/stop). CORS for localhost:5173. Static mount for frontend/dist/. All 14 PRD Section 10 endpoints: project CRUD, task CRUD+filter, status transitions (state machine validated), review trigger (202 async), review decide, force-execute, retry, cancel, project sync, sync-all, dashboard summary, SSE events. Pydantic request/response schemas (src/schemas.py). Error responses with 404/409 codes. 32 tests passing.

#### [x] T-P0-8b: Dashboard Kanban -- drag-drop + API integration -- 2026-03-01
- Installed @dnd-kit/core. Real fetch calls replacing mock data. Drag-drop cards between columns with PATCH /api/tasks/{id}/status and optimistic update + rollback. Invalid transitions show error toast. Sync All calls POST /api/sync-all and refreshes. SkeletonCard loading states. Filter bar (project, status, search) functional. Toast notification system. npm run build succeeds.

#### [x] T-P0-8c: Dashboard -- ExecutionLog + ReviewPanel + SSE -- 2026-03-01
- useSSE hook (EventSource, auto-reconnect with exponential backoff 1s/2s/4s/max 30s, connected boolean). ExecutionLog (scrollable dark log, task filter, auto-scroll with scroll-lock, timestamps, max 500 lines). ReviewPanel (progress bar, consensus score, decision points, approve/reject buttons). SSE status_change auto-updates card positions, alert events as toasts, log events populate ExecutionLog. Connection indicator in header. Elapsed timer on running cards. Bottom panel with log/review tabs. npm run build succeeds.

#### [x] T-P0-13: Integration testing (end-to-end) -- 2026-03-01
- 19 integration tests across 5 modules. conftest with MockExecutor, MockAnthropicClient, temp git repo, config factory. test_sync_to_execute (sync->QUEUED->RUNNING->DONE->git commit). test_review_flow (approve/reject/human decide/multi-reviewer synthesis). test_failure_retry (retry backoff 30/60/120s, max retries->BLOCKED). test_concurrency (per-project + global limits, dependency blocking). test_startup_recovery (orphaned RUNNING->FAILED, alerts, error_summary). 335 total tests passing.

#### [x] T-P1-1: Review pipeline refactor -- Replace Anthropic SDK with `claude -p` -- 2026-03-01
- Replaced Anthropic SDK calls with `asyncio.create_subprocess_exec("claude", "-p", ...)` using `--system-prompt`, `--model`, `--output-format json`, `--json-schema`, `--no-session-persistence`, `--max-budget-usd 0.50`. Removed `anthropic_client` parameter from `__init__`. Added `_call_claude_cli()` method. Adapted all 20 unit tests and 4 integration tests to use subprocess mocking. Updated api.py lifespan. 335 tests passing.

#### [x] T-P1-2: API lifespan cleanup -- Remove Anthropic SDK init -- 2026-03-02
- Added `claude --version` check at startup. If Claude CLI is in PATH, logs version and creates ReviewPipeline. If not found, logs warning and sets review_pipeline to None. Removed ANTHROPIC_API_KEY from test fixtures. 335 tests passing.

#### [x] T-P1-3: Remove ANTHROPIC_API_KEY dependency from env/config -- 2026-03-02
- Removed ANTHROPIC_API_KEY warning from env_loader. Removed anthropic SDK from dependencies. Changed reviewer api default from "anthropic" to "claude_cli". Updated all test fixtures. 333 tests passing.

#### [x] T-P1-4: Update review pipeline tests for subprocess mocking -- 2026-03-02
- Verified T-P1-1 already replaced all MockAnthropicClient with subprocess mocking. No MockAnthropicClient references remain in any .py files. Fixed pre-existing SSE test timing race condition. 333 tests passing.

#### [x] T-P1-5: Fix orchestrator config for self-management -- 2026-03-02
- Fixed repo_path from ~/projects/helixos to ~/Desktop/Gen_AI_Proj/helixos. Added ~/.helixos/ directory auto-creation in API lifespan. 333 tests passing.

#### [x] T-P1-6: Create root-level QUICKSTART.md -- 2026-03-02
- Comprehensive guide with prerequisites, installation, configuration (orchestrator_config.yaml, adding projects), running (dev/production/Windows), TASKS.md format, all 14 API endpoints documented, autonomous mode, and troubleshooting section. 333 tests passing.

#### [x] T-P1-7: E2E startup verification -- 2026-03-02
- Full pipeline verified: server starts on port 8000, dashboard loads from static build, sync-all parses 20 tasks from TASKS.md, all 14 API endpoints respond correctly, SSE streams with text/event-stream, review pipeline initialized with Claude CLI 2.1.63, state machine enforces transitions. Verification checklist in docs/e2e_verification.md. 333 tests passing.

#### [x] T-P2-1: Extend ProjectConfig + OrchestratorSettings for P2 features -- 2026-03-02
- Added PortRange model, port_ranges dict and max_total_subprocesses to OrchestratorSettings. Added launch_command, project_type (Literal), preferred_port to ProjectConfig. All fields optional with defaults (backward compatible). 24 new tests, 359 total passing.

#### [x] T-P2-2: PortRegistry -- auto-assign ports, conflict detection, persistence -- 2026-03-02
- PortRegistry with assign_port (preferred_port + exclude_ports), release_port, get_assignment, update_pid, list_assignments, cleanup_orphans. Atomic persistence via tmp + os.replace to ports.json. 33 new tests, 392 total passing.

#### [x] T-P2-3: Project validation + import API + config writer (ruamel.yaml) -- 2026-03-02
- config_writer.py (ruamel.yaml comment-preserving read-modify-write, atomic write, suggest_next_project_id), project_validator.py (directory validation with limited-mode detection). POST /api/projects/validate and POST /api/projects/import endpoints. Auto-assign port, auto-sync, duplicate/invalid-path rejection. 29 new tests, 421 total passing.

#### [x] T-P2-4: TasksWriter -- create tasks by appending to TASKS.md (with filelock) -- 2026-03-02
- TasksWriter with filelock + threading.Lock for concurrent write safety. ID generation inside lock, .bak backup before every write, post-write validation. Handles empty file, no Active section, ID format variations. POST /api/projects/{id}/tasks endpoint with auto-sync. 28 new tests, 449 total passing.

#### [x] T-P2-5: ProcessManager + SubprocessRegistry -- launch/stop project processes -- 2026-03-03
- SubprocessRegistry (unified tracker, shared global limit, orphan cleanup). ProcessManager (launch with PORT injection, graceful stop with timeout, stop_all, cleanup_orphans). Windows compatible (CREATE_NEW_PROCESS_GROUP + CTRL_BREAK_EVENT). 3 API endpoints (launch, stop, process-status). Shutdown order enforced. 31 new tests, 480 total passing.

#### [x] T-P2-6: Frontend -- ProjectSelector + SwimLane + KanbanBoard refactor -- 2026-03-03
- ProjectSelector.tsx (multi-select checkbox dropdown with Select all/Clear, localStorage persistence). SwimLane.tsx (per-project wrapper with header bar + KanbanBoard, solo/multi-lane height modes). App.tsx refactored: swim lane layout, tasks grouped by project, each SwimLane has own DndContext (no cross-project drag), visible dividers between lanes, global status/search filters apply across all lanes. npm run build succeeds, 480 tests passing.

#### [x] T-P2-7: Frontend -- SwimLaneHeader + ImportModal + NewTaskModal + LaunchControl -- 2026-03-03
- SwimLaneHeader.tsx (per-project action bar with Launch/Stop, New Task, Sync buttons, limited-mode warning badges). LaunchControl.tsx (launch/stop toggle with port display, running indicator, uptime, 5s polling). ImportProjectModal.tsx (3-step: path input -> validate -> review/configure -> import with success feedback). NewTaskModal.tsx (title + description + priority form). "Import Project" button in header. All modals have loading states and error handling. New types in types.ts (ProcessStatus, ValidationResult, ImportResult, CreateTaskResult). New API calls in api.ts (syncProject, validateProject, importProject, createTask, launchProject, stopProject, getProcessStatus). npm run build succeeds, 480 tests passing.

#### [x] T-P2-8: E2E integration + SSE events for P2 features -- 2026-03-03
- Added per-project process_status to dashboard summary endpoint. Verified SSE events (process_start/process_stop), startup orphan cleanup (SubprocessRegistry + PortRegistry + ProcessManager), and shutdown order (ProcessManager -> Scheduler -> DB). 14 new integration tests covering import-to-swimlane, task creation, process lifecycle with SSE, orphan cleanup, shutdown order, and full E2E flow. 494 total tests passing.

#### [x] T-P3-1: Fix "No CLAUDE.md" false-positive badge -- 2026-03-03
- Added claude_md_path to ProjectResponse/ProjectDetailResponse schemas. ProjectRegistry auto-detects CLAUDE.md at repo_path when not explicitly configured. Import endpoint auto-sets claude_md_path in YAML config. SwimLaneHeader badge now shows descriptive tooltip. 6 new tests, 500 total passing.

#### [x] T-P3-2: Backend directory browser + frontend picker -- 2026-03-03
- GET /api/filesystem/browse with $HOME sandbox, hidden dir filtering, project indicator flags. DirectoryPicker component with breadcrumb navigation. Integrated into ImportProjectModal as toggleable browse mode. 11 new tests, 511 total passing.

#### [x] T-P3-3: Import Project in ProjectSelector dropdown -- 2026-03-03
- Added "Import Project" button with + icon at bottom of ProjectSelector dropdown. Closes dropdown and opens ImportProjectModal. Connected via onImportClick prop.

#### [x] T-P3-4: Task card hover popover with details -- 2026-03-03
- TaskCardPopover component rendered via React portal with full task details (description, dependencies, execution state, review state, timestamps). 300ms hover delay, auto-positioning (right/left/below), hides on drag. npm run build succeeds, 511 tests passing.

#### [x] T-P3-5: Workflow clarity -- inline task creation, context menu, tooltips -- 2026-03-03
- InlineTaskCreator in Backlog column (expand-on-click title input, Enter to create, Esc to cancel). TaskContextMenu with right-click context menu (view details, move-to-column, retry for failed). Tooltips on all buttons (header, swim lane, launch, panel tabs, project selector). npm run build succeeds, 511 tests passing.

#### [x] T-P3-6a: Persistent execution log + review history -- backend -- 2026-03-02
- 2 new DB tables (execution_logs, review_history) with indexes. HistoryWriter service with DB-first writes, 2KB text cap, batch support. Wired into Scheduler (execution start/success/failure/cancel logs) and ReviewPipeline (per-round review persistence). 2 new API endpoints (GET /api/tasks/{id}/logs, GET /api/tasks/{id}/reviews) with pagination, level filtering, and total count. 31 new tests, 542 total passing.

#### [x] T-P3-6b: Persistent execution log + review history -- frontend -- 2026-03-02
- Task-focused bottom panel: ExecutionLog fetches persistent DB logs + merges live SSE entries with level badges and source tags. ReviewPanel shows conversation-style review history with verdict badges, suggestions, consensus bars. Task focus indicator in tab bar with clear button. 4 new TS interfaces, 2 new API client functions. npm run build succeeds, 542 tests passing.

#### [x] T-P0-15: Surface detailed execution error diagnostics -- 2026-03-02
- ErrorType enum (INFRA, CLI_NOT_FOUND, REPO_NOT_FOUND, NON_ZERO_EXIT, TIMEOUT, UNKNOWN) on ExecutorResult. Pre-flight checks (repo_path exists, claude CLI on PATH). Stderr capture with 4KB truncation and ANSI stripping. Exception details in SSE alerts and execution logs. MAX_CONCURRENT_EXECUTIONS=2 hard limit. 27 new tests, 569 total passing.

#### [x] T-P0-16: Per-project execution pause/resume gate -- 2026-03-02
- DB-backed execution_paused on ProjectSettingsRow (persists across restarts). Scheduler pause_project/resume_project methods; paused = skip new executions, in-flight continue. API endpoints for pause/resume. SwimLaneHeader amber Pause/Resume toggle + PAUSED badge. SSE execution_paused events for real-time UI. 27 new tests, 596 total passing.

#### [x] T-P3-7: README overhaul -- 2026-03-02
- Project-specific README with architecture diagram, features, backend/frontend module tables, API reference, task state machine, tech stack, quick start, configuration reference, and project structure tree.

#### [x] T-P3-8: Self-hosting guardrails -- design document -- 2026-03-02
- Design doc at docs/design/self-hosting-guardrails.md covering: worker isolation via git worktree branches, commit serialization with pytest validation gate, log isolation with [SELF-HOST] tags, human-triggered-only restart (no auto-restart), safety boundary classification (safe: code/tests/docs; unsafe: DB schema/config/scheduler/hooks), state diagram for self-modification lifecycle, recursive execution prevention, and 5-phase implementation plan.

#### [x] T-P3-9: AI-assisted task enrichment via Claude CLI -- 2026-03-02
- POST /api/tasks/enrich endpoint (Claude CLI, JSON schema, 503 if unavailable). NewTaskModal "Enrich with AI" button pre-fills description + priority. InlineTaskCreator Tab key expands to NewTaskModal with auto-enrich. Reuses review_pipeline JSON extraction and code_executor pre-flight patterns. 19 new tests, 615 total passing.

#### [x] T-P3-10: Done column sorting and sub-status filtering -- 2026-03-02
- Sort dropdown in DONE column header (Newest first/Oldest first/By task ID). Sub-status filter badges (DONE/FAILED/BLOCKED) with counts and click-to-toggle filtering. Both preferences persist in localStorage. Client-side only, no backend changes. npm run build succeeds, 615 tests passing.

#### [x] T-P3-11: Enhanced review observation and human interaction UX -- 2026-03-02
- Review status badges: pulsing for active review, orange for needs-human, green for auto-approved. REVIEW_NEEDS_HUMAN triggers toast + auto-switch to Review tab + auto-select task. ReviewPanel reason text area wired to ReviewDecisionRequest.reason. REVIEW column header shows pulsing needs-human count badge. Client-side only, no backend changes. npm run build succeeds, 615 tests passing.

#### [x] T-P0-17: Design analysis -- evaluate achievements and future directions -- 2026-03-02
- Root cause analysis of three issues (missing review gate, asyncio Windows crash, fixed bottom panel). Design document at docs/design/review-gate-asyncio-divider.md. Added T-P0-18 (review gate), T-P0-19 (asyncio fix), T-P3-12 (resizable divider) to TASKS.md.

#### [x] T-P0-18: Configurable review gate before execution (two-layer defense) -- 2026-03-02
- Two-layer review gate. Layer 1: review_gate_enabled column in DB, blocks BACKLOG->QUEUED in TaskManager when enabled. Layer 2: Scheduler._can_execute() checks ReviewHistoryRow for approved verdict before execution. PATCH /api/projects/{id}/review-gate endpoint. SwimLaneHeader Gate ON/OFF toggle. SSE review_gate_changed events. 22 new tests, 641 total passing.

#### [x] T-P0-19: Fix asyncio NotImplementedError on Windows with --reload -- 2026-03-02
- Added --loop none to start.ps1 uvicorn command. Split error logging in api.py lifespan (NotImplementedError vs FileNotFoundError with distinct messages). Defense-in-depth comment on ProactorEventLoopPolicy. QUICKSTART.md updated with Windows dev instructions and troubleshooting. 4 tests, 619 total passing.

#### [x] T-P0-20: Fix --loop none breaks uvicorn CLI startup -- 2026-03-02
- uvicorn CLI rejects --loop none; replaced with scripts/run_server.py calling uvicorn.run(loop="none"). Rewrote tests with behavioral mocks + upstream guards. 8 tests, 645 total passing.
- Followup: fixed sys.path bug (uvicorn.run doesn't add CWD like CLI does), updated 8 stale uvicorn references across 4 docs, added --log-level arg, added doc regression guard test. 13 tests, 650 total passing.
- Followup-3: Fixed stale DB crash (_migrate_missing_columns in init_db), added real subprocess smoke test (test_server_startup.py), embedded verification best practices in CLAUDE.md/LESSONS.md/stop hook. 655 total passing.

#### [x] T-P3-12: Resizable bottom panel divider -- 2026-03-02
- ResizableDivider.tsx with setPointerCapture, grip dots, hover/drag highlight. Min 80px, max 60% viewport, double-click reset to 224px. localStorage persistence. Wired into App.tsx replacing fixed h-56. npm run build succeeds, 641 tests passing.
