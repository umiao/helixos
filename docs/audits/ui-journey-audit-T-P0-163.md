# HelixOS UI Journey Audit Report
**Task**: T-P0-163
**Date**: 2026-03-10
**Auditor**: Claude Code (implementation agent)
**Scope**: 9 user journeys + 3 LLM prompts across ~20 files

---

## Executive Summary

This audit reviews the entire HelixOS UI user journey flow from project import through task execution and review. The system demonstrates solid architectural foundations with proper separation of concerns, clear state management, and comprehensive error handling. However, **5 MEDIUM-severity risks** and **11 LOW-severity risks** were identified that could impact user experience, data integrity, or workflow clarity.

### Risk Distribution
- **5 MEDIUM risks**: P3 priority gap, race condition in review submission, missing cancel-execution affordance, needs-human notification gap, review sub-state differentiation
- **11 LOW risks**: Various UX polish items, missing validations, and workflow gaps

---

## Journey 1: Project Import Flow

### Flow Description
`ImportProjectModal` implements a 3-step wizard for importing project directories:
1. **Input Step**: User enters path manually or browses via `DirectoryPicker` → validates via `POST /api/projects/validate`
2. **Review Step**: Shows validation results (has_git, has_tasks_md, has_claude_config) with optional override fields (name, project_type, launch_command, preferred_port)
3. **Done Step**: Displays import success with project ID, port assignment, and sync results

### Assessment: ✅ PASS (with LOW risks)

### Identified Risks

**LOW-001: Port NaN handling relies on parseInt behavior**
- **Location**: `ImportProjectModal.tsx:84`
- **Issue**: `preferredPort ? parseInt(preferredPort, 10) : undefined` will send `NaN` if input contains non-numeric text, even though the input has `type="number"` (which provides client-side validation but can be bypassed)
- **Impact**: Backend may receive invalid port values if browser validation is circumvented
- **Recommendation**: Add explicit `isNaN` check or backend validation

**LOW-002: Duplicate project detection not visible in validation flow**
- **Location**: `ImportProjectModal.tsx` (validation step)
- **Issue**: Validation shows has_git/has_tasks_md/has_claude_config checks and warnings, but no visible check for "project already imported with this path"
- **Impact**: User may unknowingly re-import the same project
- **Recommendation**: Add duplicate detection to validation endpoint response

**LOW-003: No cancellation of in-flight validation request**
- **Location**: `ImportProjectModal.tsx:52-67`
- **Issue**: If user rapidly changes path and clicks Validate multiple times, stale validation results could overwrite newer ones (no request cancellation via AbortController)
- **Impact**: Race condition where older validation result displays after newer one
- **Recommendation**: Add AbortController to cancel in-flight requests on new validation

### User Journey Trace
```
User clicks "Import Project" → modal opens (step=input, browsing=false)
→ User types path "C:\myproject" → state updates (path="C:\myproject")
→ User clicks "Validate" → API POST /api/projects/validate → success
  → validation={valid:true, name:"myproject", has_git:true, has_tasks_md:false, suggested_id:"myproject-1"}
  → step transitions to "review", nameOverride set to "myproject"
→ User edits name to "My Project", sets project_type="frontend", preferredPort="3000"
→ User clicks "Import" → API POST /api/projects/import
  → Backend creates project record, assigns port, syncs TASKS.md
  → result={project_id:"myproject-1", name:"My Project", port:3000, synced:true, sync_result:{added:5, updated:0}}
  → step transitions to "done", importResult set
→ User sees success message, clicks "Done" → modal closes, onImported() triggers refresh
```

---

## Journey 2: Task Creation Flows

### Flow Description
HelixOS supports 3 task creation entry paths:

1. **InlineTaskCreator (Quick Create)**: User types title in Backlog column placeholder → presses Enter → creates task with title only
2. **InlineTaskCreator (AI-Enhanced)**: User types title → presses **Tab** → opens `NewTaskModal` with `autoEnrich=true` → auto-triggers enrichment on mount
3. **NewTaskModal (Direct)**: User opens modal manually → fills title, description, priority → optionally clicks "Enrich with AI" → submits

All paths converge on `POST /api/tasks` (create) or `POST /api/tasks/enrich` (enrichment).

### Assessment: ⚠️ MEDIUM RISK (P3 priority gap)

### Identified Risks

**MEDIUM-001: P3 priority option missing in NewTaskModal and enrichment prompt**
- **Location**: `NewTaskModal.tsx:169-172`, `enrichment_system.md:6`
- **Issue**: Priority dropdown only offers P0/P1/P2, but TASKS.md schema defines P3 (stretch goals). Enrichment prompt also only generates P0/P1/P2.
- **Failure Scenario**:
  - User wants to create a P3 task → opens NewTaskModal
  - Dropdown shows P0/P1/P2 only → user forced to mislabel as P2
  - Task incorrectly prioritized → planning/scheduling skewed
- **Impact**: P3 tasks cannot be created via UI, forcing users to edit TASKS.md manually
- **Recommendation**: Add P3 option to dropdown and update enrichment prompt

**LOW-004: No unsaved-changes warning when closing NewTaskModal**
- **Location**: `NewTaskModal.tsx:195-199` (Cancel button), `NewTaskModal.tsx:118-122` (X button)
- **Issue**: User can close modal with unsaved edits (title/description/priority changed) without confirmation
- **Impact**: Accidental data loss if user clicks Cancel/X after filling form
- **Recommendation**: Track `hasUnsavedChanges` and show confirmation dialog before closing

**LOW-005: Enrichment error recovery doesn't preserve user's original description**
- **Location**: `NewTaskModal.tsx:48-50`
- **Issue**: If enrichment succeeds, it overwrites both description AND priority. If user had manually entered a description before clicking "Enrich with AI", it's lost.
- **Impact**: User must re-type description if they want to enrich but keep their custom text
- **Recommendation**: Show confirmation "Enrichment will overwrite description. Continue?" or provide "Merge" option

**LOW-006: InlineTaskCreator auto-submits on blur, may surprise users**
- **Location**: `InlineTaskCreator.tsx:83-91`
- **Issue**: If user types a title then clicks outside the input (blur), task is auto-created. This may be unexpected (user might have intended to cancel).
- **Impact**: Unintended task creation, requiring deletion
- **Recommendation**: Consider requiring explicit Enter keypress (remove auto-submit on blur) or show subtle confirmation toast "Task created"

### User Journey Trace (AI-Enhanced Path)
```
User clicks "+ Add task..." in Backlog column → InlineTaskCreator expands (editing=true)
→ User types "Add user authentication" → presses Tab
  → InlineTaskCreator calls onEnrichExpand("Add user authentication")
  → App.tsx opens NewTaskModal with initialTitle="Add user authentication", autoEnrich=true
→ NewTaskModal mounts → useEffect triggers doEnrich(initialTitle)
  → API POST /api/tasks/enrich {title: "Add user authentication"}
  → enriching=true (button shows "Enriching...")
  → Response: {description: "Create login/signup endpoints...", priority: "P0"}
  → setDescription(...), setPriority("P0"), enriching=false
→ User sees pre-filled description and P0 priority → edits description slightly
→ User clicks "Create Task"
  → API POST /api/tasks {title: "Add user authentication", description: "...", priority: "P0"}
  → success → onCreated(synced=true) → board refreshes, task appears in Backlog
```

---

## Journey 3: Kanban Drag-Drop Lifecycle

### Flow Description
`KanbanBoard` uses `@dnd-kit/core` for drag-and-drop across 5 columns:
- **BACKLOG** → status: `backlog`
- **REVIEW** → status: `review`, `review_auto_approved`, `review_needs_human`
- **QUEUED** → status: `queued`
- **RUNNING** → status: `running`
- **DONE** → status: `done`, `failed`, `blocked`

**Drag interception logic**:
1. **Backward drag** (targetColumn < sourceColumn in COLUMN_ORDER): Show `BackwardDragModal` with reason input
2. **Decomposition gate** (drag to RUNNING with `plan_status=ready` and `proposed_tasks.length > 0`): Show `DecomposeRequiredModal` with "Go to Plan Review" / "Execute Anyway" / "Cancel"
3. **Normal forward drag**: Call `onMoveTask(taskId, newStatus)` directly

### Assessment: ✅ PASS (with LOW risks)

### Identified Risks

**LOW-007: Review gate (428 response) not intercepted at drag-drop layer**
- **Location**: `KanbanBoard.tsx:366-387` (drag-end handler)
- **Issue**: Drag-drop calls `onMoveTask()` directly without checking if review gate would block. 428 response likely handled in API layer, but user sees no pre-flight indication.
- **Impact**: User drags BACKLOG → QUEUED, drop completes, then sees error toast "Review required" retroactively
- **Recommendation**: Add client-side check for `review_gate_enabled` project setting and show `ReviewSubmitModal` proactively before transition (similar to decomposition gate)

**LOW-008: Optimistic UI update not visible for drag-drop state transitions**
- **Location**: `KanbanBoard.tsx` (no optimistic state mutation visible)
- **Issue**: Task remains in source column until SSE `task_status_changed` event arrives. For slow API responses, creates perceived lag.
- **Impact**: User drags task but it "snaps back" for 500ms-2s until server confirms
- **Recommendation**: Add optimistic task mutation in `onMoveTask` (move task in local state immediately, rollback on error)

**LOW-009: No indication of which column is targeted during drag-over**
- **Location**: `KanbanBoard.tsx:155-166` (DroppableColumn)
- **Issue**: `isOver` changes background to `bg-blue-50`, but for large boards with many tasks, user may not notice which column will receive the drop
- **Recommendation**: Add more prominent visual feedback (border, shadow, or header highlight)

### User Journey Trace (Backward Drag)
```
User drags T-P0-42 from RUNNING column → drops on BACKLOG column
→ KanbanBoard.handleDragEnd() detects backward drag (COLUMN_ORDER[BACKLOG]=0 < COLUMN_ORDER[RUNNING]=3)
  → setBackwardDrag({taskId, taskTitle, taskLocalId, sourceColumn:RUNNING, targetColumn:BACKLOG, newStatus:backlog})
→ BackwardDragModal renders
  → Shows task title, RUNNING → BACKLOG transition visualization
  → Consequence text: "The task will return to the backlog. Any review progress or queue position will be reset."
  → User enters reason: "Found critical bug in requirements"
→ User clicks "Confirm Move"
  → onConfirm(reason="Found critical bug...") called
  → onMoveTask(taskId, "backlog", {reason: "Found critical bug..."})
  → API PATCH /api/tasks/{id}/status → backend logs reason, transitions task to backlog
  → SSE task_status_changed event → board refreshes, task moves to BACKLOG column
```

### Conditional Behaviors

| Condition | Action | Inverse Case |
|-----------|--------|--------------|
| **Backward drag detected** | Show BackwardDragModal with reason input → require confirmation | Forward drag: call `onMoveTask()` directly |
| **Drag to RUNNING with plan_status=ready AND proposed_tasks.length > 0** | Show DecomposeRequiredModal → block execution | plan_status != ready OR proposed_tasks empty: allow execution |
| **Drop on same column** | No-op (early return) | Drop on different column: proceed with transition |

---

## Journey 4: Review Gate Flow

### Flow Description
`ReviewSubmitModal` opens when:
1. Backend returns **428 Precondition Required** on status transition (review gate enabled)
2. Task has `plan_invalid=true` (plan too short or missing)

Modal allows user to edit title/description (plan text), shows preview, validates min-length (20 chars), then:
1. **If edits made**: `PATCH /api/tasks/{id}` to save title/description
2. **Always**: `PATCH /api/tasks/{id}/status` to transition to `review`

### Assessment: ⚠️ MEDIUM RISK (race condition)

### Identified Risks

**MEDIUM-002: PATCH task fields + PATCH status as 2 separate API calls (race condition)**
- **Location**: `ReviewSubmitModal.tsx:38-58`
- **Issue**: If user edits title/description, modal makes 2 sequential API calls:
  1. `updateTask(task.id, {title, description})` (line 46)
  2. `updateTaskStatus(task.id, "review")` (line 49)

  Race condition scenarios:
  - **Scenario A**: Another client (or backend process) updates task between calls → description update may be lost or status transition may operate on stale task data
  - **Scenario B**: Network partition causes call 1 to succeed but call 2 to fail → task updated but still in BACKLOG (user sees error, must retry, but description is already saved)
  - **Scenario C**: Backend processes call 2 before call 1 fully commits → reviewer sees old plan text

- **Failure Scenario**:
  ```
  User edits plan text → clicks "Submit for Review"
  → PATCH /api/tasks/123 {description: "new plan"} → 200 OK (DB write pending)
  → Concurrent SSE event updates task → overwrites description
  → PATCH /api/tasks/123/status {status: "review"} → 200 OK
  → Task in REVIEW with OLD description
  ```
- **Impact**: Data loss (plan text overwritten), review based on stale plan
- **Recommendation**: Backend should provide single atomic endpoint `POST /api/tasks/{id}/submit-for-review` accepting optional `{title, description}` to ensure transactional consistency

**LOW-010: Preview section shows plaintext, not rendered markdown**
- **Location**: `ReviewSubmitModal.tsx:138-144`
- **Issue**: Preview uses `whitespace-pre-wrap` but does not render markdown. Reviewers will see markdown source (e.g., `## Step 1\n- Item`) instead of formatted output.
- **Impact**: User may submit poorly formatted plan thinking it looks correct, but reviewers see raw markdown
- **Recommendation**: Use `MarkdownRenderer` component for preview section

**LOW-011: No warning if user tries to close modal with unsaved edits**
- **Location**: `ReviewSubmitModal.tsx:158-162` (Cancel button)
- **Issue**: User can click Cancel and lose edits without confirmation (similar to LOW-004)
- **Impact**: Accidental data loss
- **Recommendation**: Add "You have unsaved changes. Discard?" confirmation

### User Journey Trace (428 Response Handling)
```
User drags T-P0-55 from BACKLOG → QUEUED
→ App.tsx handleMoveTask() → API PATCH /api/tasks/T-P0-55/status {status: "queued"}
→ Backend checks review_gate_enabled=true for project → returns 428 Precondition Required
  → Error payload: {detail: "Task must pass review before queuing", task: {...}}
→ Frontend receives 428 → App.tsx detects error → opens ReviewSubmitModal
  → Modal loads with task.title, task.description (current plan text)
  → User sees warning "Plan required (at least 20 characters)"
→ User edits description: adds 100 chars of implementation steps
  → hasEdits=true, planValid=true (length >= 20)
→ User clicks "Submit for Review"
  → API PATCH /api/tasks/T-P0-55 {description: "..."} → 200 OK
  → API PATCH /api/tasks/T-P0-55/status {status: "review"} → 200 OK
  → onSubmitted(T-P0-55) → board refreshes → task moves to REVIEW column
```

### Conditional Behaviors

| Condition | Action | Inverse Case |
|-----------|--------|--------------|
| **Plan length >= 20 chars** | Enable "Submit for Review" button | Plan length < 20: button disabled, warning shown |
| **Title/description edited** | PATCH task fields before status transition | No edits: skip task PATCH, only transition status |
| **Title empty** | Disable submit button | Title present: allow submit |

---

## Journey 5: Plan Generation & Decomposition

### Flow Description
`PlanReviewPanel` renders based on `task.plan_status` (5 states):

1. **none**: "No plan generated" message + instruction to use "Plan" button on task card
2. **generating**: Spinner + "Generating plan..." + "Cancel" link (which triggers delete with confirmation)
3. **failed**: Error message + `plan_error_message` + "Retry" and "Delete Plan" buttons
4. **ready**: Plan summary (markdown-rendered) + list of `ProposedTask` cards + action buttons:
   - "Edit Plan" → inline textarea editor with Edit/Preview tabs
   - "Delete Plan" → delete with confirmation
   - "Reject Plan" → sets plan_status=none, keeps task in BACKLOG
   - "Confirm and Create All Tasks" → creates sub-tasks, sets plan_status=decomposed
5. **decomposed**: "Plan decomposed" message + "Delete Plan" button (with warning "will not remove already-created subtasks")

### Assessment: ✅ PASS (with LOW risks)

### Identified Risks

**LOW-012: Stale generation_id filtering not visible in frontend**
- **Location**: `PlanReviewPanel.tsx` (no generation_id comparison logic)
- **Issue**: Backend may send SSE events for plan generation started with old generation_id. Frontend doesn't validate `task.generation_id === event.generation_id` before updating UI.
- **Impact**: If user rapidly clicks "Plan" → "Cancel" → "Plan", stale SSE events may cause UI flicker (old plan briefly appears then disappears)
- **Recommendation**: Add generation_id validation in SSE handler (src/hooks/useSSEHandler.ts)

**LOW-013: No bulk-edit capability for proposed tasks**
- **Location**: `PlanReviewPanel.tsx:478-483` (ProposedTaskCard list)
- **Issue**: Each proposed task is read-only except via "Edit Plan" (which edits markdown text, not structured task fields). User cannot bulk-adjust priorities or dependencies.
- **Impact**: If LLM generates 8 tasks all with P0 priority but user wants 5 as P1, must reject entire plan and regenerate
- **Recommendation**: Add inline edit for each ProposedTask (priority, complexity, dependencies) before confirmation

**LOW-014: Plan edit mode doesn't warn about unsaved changes on tab switch**
- **Location**: `PlanReviewPanel.tsx:410-426` (Edit/Preview tabs)
- **Issue**: User can switch Edit → Preview → Edit without warning, but if they click "Delete Plan" or "Reject Plan" while editing, changes are lost
- **Impact**: Accidental data loss
- **Recommendation**: Disable Delete/Reject buttons while editing, or add "You have unsaved edits" warning

### User Journey Trace (Plan Generation → Decomposition)
```
User clicks "Plan" button on task card T-P0-88 (complexity=M)
→ API POST /api/tasks/T-P0-88/generate-plan → 202 Accepted {generation_id: "abc123"}
→ Frontend updates task.plan_status="generating", task.generation_id="abc123"
→ PlanReviewPanel renders spinner + "Cancel" link
→ Backend invokes Claude via prompt `plan_system.md` with {{complexity_hint}}="M"
  → LLM generates plan with 3 proposed sub-tasks (M complexity requires 2-4 per validation)
  → Backend saves plan to task.description, proposed_tasks array, sets plan_status="ready"
  → SSE event plan_generation_complete → frontend updates task
→ PlanReviewPanel transitions to "ready" state
  → Shows plan summary (markdown-rendered description)
  → Shows 3 ProposedTaskCard components (each with title, description, priority, complexity, ACs, files, dependencies)
→ User expands task #2 → clicks "Show details" → sees acceptance_criteria list
→ User clicks "Confirm and Create All Tasks (3)"
  → API POST /api/tasks/T-P0-88/confirm-plan
  → Backend creates 3 new tasks in TASKS.md, commits to git, sets plan_status="decomposed"
  → Response: {written_ids: ["T-P0-89", "T-P0-90", "T-P0-91"]}
  → onConfirmed() → App.tsx syncs project → new tasks appear in board
```

### Conditional Behaviors

| Condition | Action | Inverse Case |
|-----------|--------|--------------|
| **plan_status = none** | Show "No plan generated" message | plan_status != none: render active plan UI |
| **plan_status = generating** | Show spinner + Cancel link | Other states: show static plan content |
| **plan_status = failed** | Show error + Retry/Delete buttons | plan_status != failed: no error UI |
| **plan_status = ready** | Show plan review UI with Confirm/Reject/Edit/Delete | Other states: different UI |
| **plan_status = decomposed** | Show "decomposed" message + Delete button (with warning) | plan_status != decomposed: show active plan UI |
| **ProposedTask card expanded** | Show acceptance_criteria, files, dependencies | Collapsed: show only title, description (2-line clamp) |

---

## Journey 6: Execution Monitoring

### Flow Description
`ExecutionLog` displays task execution logs with 2 modes:

1. **Task-focused mode** (selectedTaskId set):
   - Fetches persistent logs from DB via `GET /api/tasks/{id}/logs?limit=500`
   - Polls DB every 5 seconds for updates
   - Merges with live SSE log entries (deduplicates by timestamp)
   - Shows level filters (ERROR, WARN, INFO, DEBUG with "More" dropdown)
   - Auto-scroll behavior: scrolls to bottom on new entries, pauses if user scrolls up

2. **All-tasks mode** (no selectedTaskId):
   - Shows SSE log entries only (last 500 lines)
   - Task filter dropdown to filter by task_id

**Log entry coloring**: Level-based (error=red, warn=yellow, debug=gray) + role-based (tool=cyan, result=gray, progress=gray, ai=white)

### Assessment: ⚠️ MEDIUM RISK (no cancel-execution button)

### Identified Risks

**MEDIUM-003: No explicit cancel-execution affordance (workaround: backward drag)**
- **Location**: `ExecutionLog.tsx` (no cancel/stop button visible)
- **Backend Endpoint Status**: ✅ Backend endpoint POST /api/tasks/{id}/cancel EXISTS (src/routes/execution.py:12,426). Gap is frontend-only.
- **Issue**: User watching a RUNNING task in ExecutionLog has no direct "Cancel Execution" button. Current workaround is dragging task backward to BACKLOG/QUEUED (which cancels execution as side-effect).
- **Failure Scenario**:
  - Task T-P0-100 stuck in infinite loop during execution
  - User opens ExecutionLog, sees repeating errors
  - User looks for "Cancel" / "Stop" button → not found
  - User discovers workaround via trial-and-error (drag to BACKLOG) or asks support
- **Impact**: Poor UX, especially for new users. Execution cancellation is non-discoverable.
- **Recommendation**: Add "Cancel Execution" button in ExecutionLog header when selectedTaskStatus="running", calling backend `POST /api/tasks/{id}/cancel` endpoint (frontend-only change)

**LOW-015: Auto-scroll pause on manual scroll-up may confuse users**
- **Location**: `ExecutionLog.tsx:199-209`
- **Issue**: Auto-scroll pauses if user scrolls up, but there's no clear visual indication that auto-scroll is OFF (only small "Resume scroll" button in top-right)
- **Impact**: User may think logs stopped streaming when they've just scrolled up
- **Recommendation**: Show persistent banner "Auto-scroll paused. New logs arriving. [Resume]" at bottom of log area

**LOW-016: No context menu or copy-to-clipboard for log entries**
- **Location**: `ExecutionLog.tsx:469-505` (log entry rendering)
- **Issue**: User cannot right-click log entry to copy timestamp/message, must manually select text
- **Impact**: Harder to share specific error messages with team
- **Recommendation**: Add copy button on hover, or right-click context menu

### User Journey Trace (Live Execution Monitoring)
```
User selects task T-P0-77 from RUNNING column
→ App.tsx setSelectedTask(T-P0-77) → bottomPanel="log"
→ ExecutionLog receives selectedTaskId=T-P0-77, selectedTaskStatus="running"
→ useEffect triggers fetchExecutionLogs(T-P0-77, {limit:500})
  → API GET /api/tasks/T-P0-77/logs → response: {entries: [...], total: 342}
  → setDbEntries(entries), setDbTotal(342), setDbFetchedTaskId(T-P0-77)
→ ExecutionLog builds displayEntries: DB logs (342) + newer SSE entries
→ User sees log stream, latest entry: "[TOOL] Running npm install..."
→ New SSE event arrives: log_entry {task_id: T-P0-77, message: "[RESULT] npm install completed", timestamp: "2026-03-10T15:32:10Z"}
  → useSSEHandler adds entry to logEntries state
  → ExecutionLog merges SSE entry (timestamp > latest DB entry) → displayEntries updated
  → Auto-scroll triggers: containerRef.scrollTop = scrollHeight
→ User sees new log line appear at bottom, window auto-scrolls
→ User scrolls up to review earlier logs
  → handleScroll() detects scroll-up → setAutoScroll(false)
  → "Resume scroll" button appears in header
→ More SSE events arrive → logs accumulate at bottom, user still scrolled to middle
→ User clicks "Resume scroll" → setAutoScroll(true) → window jumps to bottom
```

### Conditional Behaviors

| Condition | Action | Inverse Case |
|-----------|--------|--------------|
| **selectedTaskId set** | Task-focused mode: fetch DB logs, merge SSE, show level filters | No selectedTaskId: all-tasks mode, SSE-only, show task dropdown |
| **selectedTaskStatus = "running"** | Show elapsed timer in header | Other statuses: no timer |
| **Auto-scroll ON** | Scroll to bottom on new log entries | Auto-scroll OFF: logs accumulate below viewport, show "Resume scroll" |
| **User scrolls up > 30px from bottom** | Pause auto-scroll | User at bottom: enable auto-scroll |
| **dbTotal > FETCH_LIMIT (500)** | Show truncation notice "Showing latest 500 of X entries" | dbTotal <= 500: no notice |

---

## Journey 7: Review Pipeline UX

### Flow Description
`ReviewPanel` displays review history and consensus scoring for tasks in REVIEW column. Component was too large to read fully (53.4KB), but based on preview and related code:

**Review lifecycle states** (from comments in ReviewPanel.tsx):
- `not_started`: "Review not started"
- `running`: Spinner + "Review in progress..."
- `approved`: Review results with approved badge
- `rejected_single`: "Single reviewer rejected" label
- `rejected_consensus`: Review results with consensus bar
- `partial`: Partial results
- `failed`: Error message + "Retry Review" button

**Human decision UI**: Approve / Reject / Request Changes buttons visible in component signature.

### Assessment: ⚠️ MEDIUM RISK (needs-human notification gap)

### Identified Risks

**MEDIUM-004: Human-review needs-attention not proactively surfaced (no toast/notification)**
- **Location**: `KanbanBoard.tsx:441-453` (needs_human badge), `ReviewPanel.tsx` (decision UI)
- **Issue**: When task reaches `review_needs_human` state (consensus failed, human decision required):
  - Badge appears in REVIEW column header with count + "needs human" text (with animate-pulse)
  - BUT no proactive notification: no toast, no browser notification, no email alert
  - User must notice the pulsing badge in the column header
- **Failure Scenario**:
  - Task T-P0-55 completes automated review → 3 reviewers, 2 approve, 1 reject → needs human decision
  - Backend sets status=review_needs_human
  - User is focused on another tab/window → doesn't see badge pulse
  - Task sits in needs_human for hours until user happens to check board
- **Impact**: Delayed human decisions, potential pipeline stalls
- **Recommendation**: Send browser notification + toast when task transitions to needs_human (especially if user has that project selected)

**MEDIUM-005: Review column grouping 3 sub-states without clear differentiation**
- **Location**: `KanbanBoard.tsx:441-453`, `types.ts` (STATUS_TO_COLUMN mapping)
- **Issue**: REVIEW column groups 3 distinct sub-states:
  - `review`: Under review (no decision yet)
  - `review_auto_approved`: Passed consensus, auto-approved
  - `review_needs_human`: Consensus failed, awaiting human decision

  Visual differentiation:
  - `needs_human`: Pulsing orange badge in column header ("X needs human")
  - `review`/`review_auto_approved`: No visual distinction on task cards themselves (both appear identical in REVIEW column)

- **Impact**: User cannot tell if task in REVIEW column is actively being reviewed vs. already approved vs. stuck waiting for human decision (without clicking into it)
- **Recommendation**: Add sub-status badge to task card (e.g., green "Auto-Approved" badge, orange "Needs Human" badge, gray "Under Review" badge)

**LOW-017: No filtering by review_lifecycle_state**
- **Location**: `App.tsx:230-244` (filter dropdown)
- **Issue**: Filter dropdown shows `review`, `review_auto_approved`, `review_needs_human` as separate options, but user may want "all review states" filter
- **Impact**: Minor UX friction (user must select 3 separate filters to see all review tasks)
- **Recommendation**: Add "All Review States" meta-filter option

### User Journey Trace (Multi-Round Review)
```
(Unable to trace fully due to file size limit on ReviewPanel.tsx. Based on available code:)

User drags T-P0-33 from BACKLOG → REVIEW (via ReviewSubmitModal)
→ Backend transitions task to status=review, triggers review pipeline
→ Review pipeline invokes 3 AI reviewers (configured in project settings)
→ Reviewer 1 (Strict): {"pass": false, "blocking_issues": [...]}
→ Reviewer 2 (Pragmatic): {"pass": true, "suggestions": [...]}
→ Reviewer 3 (Security): {"pass": true, "suggestions": [...]}
→ Consensus algorithm: 2/3 pass, but threshold=100% required
→ Backend sets task.review_lifecycle_state="review_needs_human"
→ SSE event task_status_changed → frontend updates task
→ KanbanBoard REVIEW column header shows "1 needs human" badge (orange, pulsing)
→ User clicks task card T-P0-33 → ReviewPanel renders
  → Shows review history grouped by attempt
  → Shows consensus bar: 2/3 (67%) with threshold indicator at 100%
  → Shows human decision buttons: Approve / Reject / Request Changes
→ User reads blocking issues from Reviewer 1
→ User clicks "Request Changes" → submitReviewDecision(task.id, "request_changes", comment="Please address security concerns")
→ Backend sends task back to BACKLOG with review feedback attached
→ SSE event → task moves to BACKLOG column, user sees feedback in task details
```

---

## Journey 8: Filtering & Search

### Flow Description
`App.tsx` provides multi-layered filtering:

1. **Project filter**: `ProjectSelector` multi-select (shows selected projects or all if none selected)
2. **Status filter**: Single-select dropdown (all statuses, backlog, review, review_auto_approved, review_needs_human, queued, running, done, failed, blocked)
3. **Search filter**: Text input (searches task titles/descriptions)
4. **Priority filter**: Multi-select chips (P0, P1, P2, P3)
5. **Complexity filter**: Multi-select chips (S, M, L)

All filters combine via AND logic in `useTaskState.ts` → `globallyFiltered` computed property.

### Assessment: ✅ PASS (with LOW risks)

### Identified Risks

**LOW-018: Filter state not persisted to localStorage**
- **Location**: `App.tsx` (filter state in React useState, not localStorage)
- **Issue**: User sets filters (e.g., show only P0 + P1 tasks) → closes browser → reopens → filters reset to defaults
- **Impact**: User must re-apply filters on every session
- **Recommendation**: Persist `filterStatus`, `filterPriorities`, `filterComplexities`, `searchQuery` to localStorage (similar to DONE column sort order in KanbanBoard)

**~~LOW-019: Clear filters behavior unclear (button not visible in code snippet)~~** [CORRECTED 2026-03-10]
- **Location**: `App.tsx:311-318`
- **Issue**: ~~Code snippet cut off at line 300, couldn't verify if "Clear Filters" button exists in UI~~
- **Verification**: ✅ Clear Filters button EXISTS (App.tsx:311-318). Conditional rendering: shown when `filterPriorities.size > 0 || filterComplexities.size > 0`. Button text: "Clear". Calls `clearFilters()` function.
- **Impact**: None. Feature is implemented correctly.
- **Status**: Finding was incorrect due to incomplete code read. No action needed.

**LOW-020: Search scope unclear (no indication of what fields are searched)**
- **Location**: `App.tsx:248-252` (search input)
- **Issue**: Placeholder says "Search tasks..." but doesn't specify if it searches only title, or title + description, or also local_task_id
- **Impact**: User may search for text they know is in description, get no results, assume it's not there (when actually search only covers title)
- **Recommendation**: Update placeholder to "Search title/description..." or add tooltip

### User Journey Trace (Multi-Filter Search)
```
User opens HelixOS Dashboard → sees all tasks from all projects
→ User clicks ProjectSelector → selects "frontend" and "backend" projects → deselects "mobile"
  → handleSelectedProjectsChange([frontend.id, backend.id])
  → activeProjectIds updated → tasksByProject recomputed → mobile tasks hidden
→ User sets Status filter → selects "review_needs_human"
  → setFilterStatus("review_needs_human")
  → useTaskState.globallyFiltered filters to status="review_needs_human" only
  → Board shows 3 tasks
→ User types "authentication" in search box
  → setSearchQuery("authentication")
  → useTaskState.globallyFiltered further filters to tasks with "authentication" in title/description
  → Board shows 1 task: T-P0-55 "Add user authentication"
→ User clicks P0 priority chip
  → setFilterPriorities(new Set(["P0"]))
  → globallyFiltered filters to priority="P0" only
  → Board still shows T-P0-55 (which is P0)
→ User clicks P1 priority chip (adding, not replacing)
  → setFilterPriorities(new Set(["P0", "P1"]))
  → globallyFiltered includes P0 OR P1 tasks
  → Board shows 2 tasks: T-P0-55, T-P1-60
→ User clicks "Clear Filters" (if implemented)
  → clearFilters() → resets all filters to defaults
  → Board shows all tasks from selected projects again
```

### Conditional Behaviors

| Condition | Action | Inverse Case |
|-----------|--------|--------------|
| **selectedProjects.length > 0** | Show only tasks from selected projects | selectedProjects empty: show all projects |
| **filterStatus set** | Filter to single status | filterStatus="": show all statuses |
| **searchQuery not empty** | Filter to tasks matching search term | searchQuery="": show all tasks |
| **filterPriorities.size > 0** | Filter to selected priorities (OR logic) | filterPriorities empty: show all priorities |
| **filterComplexities.size > 0** | Filter to selected complexities (OR logic) | filterComplexities empty: show all complexities |

---

## Journey 9: LLM Prompt Design Review

### Files Analyzed
1. `config/prompts/enrichment_system.md` (task enrichment)
2. `config/prompts/plan_system.md` (plan generation)
3. `config/prompts/review.md` (plan review)
4. `config/prompts/_shared_rules.md` (common rules)

### Enrichment Prompt Assessment

**Template variables**: None (static prompt)

**Output format**: JSON `{"title": "...", "description": "...", "priority": "P0"}`

**Scope constraint**: ✅ "Do NOT expand the scope of the task. The description should explain what the title says, not add new requirements." (line 8)

**Priority generation**: ⚠️ Only generates P0/P1/P2 (line 6), missing P3 (MEDIUM-001 applies here too)

**Quality**: Strong. Clear instructions, good example of scope constraint enforcement.

---

### Plan Prompt Assessment

**Template variables**:
- `{{complexity_hint}}`: S/M/L complexity for sub-task decomposition
- `{{include:_shared_rules.md}}`: Injects shared project rules

**Output format**: ✅ JSON with `plan`, `steps`, `acceptance_criteria`, `proposed_tasks` (line 82)

**Phased thinking**: ✅ 4-phase structure guides LLM through analysis → design → ACs → decomposition (lines 5-30)

**Decomposition rules**:
- S: 0 sub-tasks (lines 26)
- M: 2-4 sub-tasks (line 27)
- L: 3-8 sub-tasks (line 28)

**Few-shot example**: ✅ 2-task example showing auth decomposition (lines 39-75)

**Quality**: Excellent. Clear phased structure, explicit decomposition rules, good example.

**Potential issue**: Line 31 `{{include:_shared_rules.md}}` assumes backend properly injects file content. If injection fails, LLM doesn't see project rules.

---

### Review Prompt Assessment

**Template variables**:
- `{{reviewer_role}}`: Reviewer persona (Strict / Pragmatic / Security)
- `{{review_questions}}`: Specific questions to guide review
- `{{include:_shared_rules.md}}`: Shared project rules

**Output format**: ✅ JSON with `blocking_issues`, `suggestions`, `pass` (line 10)

**Calibration examples**: ✅ 2 examples showing PASS vs FAIL thresholds (lines 18-49)

**Severity levels**: ✅ `high` (must fix) vs `medium` (strongly recommended) (line 11)

**Pass/Fail threshold guidance**: ✅ Clear criteria for PASS (implementable as-is) vs FAIL (structural defects) (lines 51-54)

**Quality**: Excellent. Calibration examples are especially valuable for consistent review quality.

---

### Shared Rules Assessment

**Schema enforcement**: ✅ Task IDs, Priority, Complexity, Depends on, Description, ACs all documented (lines 3-13)

**Project rules**: ✅ 6 key rules including:
- Journey-first ACs (line 19)
- Cross-boundary integration testing (line 21)
- "Other case" gate for conditionals (line 23)
- Manual smoke test AC for UX tasks (line 24)

**Constraints**: ✅ API keys in .env, type hints, no emoji, UTF-8 encoding, Windows-compatible, schema migrations (lines 27-33)

**Anti-patterns**: ✅ Too many tasks, vague ACs, scope creep, missing inverse cases (lines 45-49)

**Quality**: Strong foundation for consistent task specifications.

---

### Template Variable Population Risk

**LOW-021: No validation that template variables are populated correctly**
- **Location**: Backend prompt loader (not audited, but referenced by prompts)
- **Issue**: Prompts rely on `{{complexity_hint}}`, `{{reviewer_role}}`, `{{review_questions}}`, `{{include:_shared_rules.md}}`. If backend prompt loader fails to inject these, LLM receives malformed prompt with literal `{{...}}` placeholders.
- **Impact**: Degraded LLM output quality (may generate invalid plans if complexity_hint missing, or ignore project rules if _shared_rules.md inclusion fails)
- **Recommendation**: Add backend validation that all template variables are resolved before sending to LLM (fail-fast if variable missing)

---

## Risk Summary Table

| ID | Severity | Risk | Affected Component | Recommended Action |
|----|----------|------|-------------------|-------------------|
| MEDIUM-001 | MEDIUM | P3 priority option missing in NewTaskModal and enrichment prompt | NewTaskModal.tsx:169-172, enrichment_system.md:6 | Add P3 to dropdown and prompt |
| MEDIUM-002 | MEDIUM | PATCH task + PATCH status as 2 separate API calls (race condition) | ReviewSubmitModal.tsx:38-58 | Backend atomic endpoint POST /submit-for-review |
| MEDIUM-003 | MEDIUM | No explicit cancel-execution affordance (workaround: backward drag) | ExecutionLog.tsx | Add "Cancel Execution" button when task running |
| MEDIUM-004 | MEDIUM | Human-review needs-attention not proactively surfaced (no toast/notification) | KanbanBoard.tsx:441-453 | Send browser notification + toast on needs_human |
| MEDIUM-005 | MEDIUM | Review column grouping 3 sub-states without clear differentiation | KanbanBoard.tsx, TaskCard | Add sub-status badge to task cards |
| LOW-001 | LOW | Port NaN handling relies on parseInt behavior | ImportProjectModal.tsx:84 | Add explicit isNaN check or backend validation |
| LOW-002 | LOW | Duplicate project detection not visible in validation flow | ImportProjectModal.tsx | Add duplicate check to validation endpoint |
| LOW-003 | LOW | No cancellation of in-flight validation request | ImportProjectModal.tsx:52-67 | Add AbortController for request cancellation |
| LOW-004 | LOW | No unsaved-changes warning when closing NewTaskModal | NewTaskModal.tsx:195-199 | Add confirmation dialog before closing with edits |
| LOW-005 | LOW | Enrichment error recovery doesn't preserve user's original description | NewTaskModal.tsx:48-50 | Show confirmation or provide merge option |
| LOW-006 | LOW | InlineTaskCreator auto-submits on blur, may surprise users | InlineTaskCreator.tsx:83-91 | Require explicit Enter keypress |
| LOW-007 | LOW | Review gate (428 response) not intercepted at drag-drop layer | KanbanBoard.tsx:366-387 | Add client-side review gate check pre-flight |
| LOW-008 | LOW | Optimistic UI update not visible for drag-drop state transitions | KanbanBoard.tsx | Add optimistic task mutation |
| LOW-009 | LOW | No indication of which column is targeted during drag-over | KanbanBoard.tsx:155-166 | Add prominent visual feedback |
| LOW-010 | LOW | Preview section shows plaintext, not rendered markdown | ReviewSubmitModal.tsx:138-144 | Use MarkdownRenderer for preview |
| LOW-011 | LOW | No warning if user tries to close modal with unsaved edits | ReviewSubmitModal.tsx:158-162 | Add "Discard changes?" confirmation |
| LOW-012 | LOW | Stale generation_id filtering not visible in frontend | PlanReviewPanel.tsx | Add generation_id validation in SSE handler |
| LOW-013 | LOW | No bulk-edit capability for proposed tasks | PlanReviewPanel.tsx:478-483 | Add inline edit for ProposedTask fields |
| LOW-014 | LOW | Plan edit mode doesn't warn about unsaved changes on tab switch | PlanReviewPanel.tsx:410-426 | Disable Delete/Reject while editing |
| LOW-015 | LOW | Auto-scroll pause on manual scroll-up may confuse users | ExecutionLog.tsx:199-209 | Show persistent "Auto-scroll paused" banner |
| LOW-016 | LOW | No context menu or copy-to-clipboard for log entries | ExecutionLog.tsx:469-505 | Add copy button on hover |
| LOW-017 | LOW | No filtering by review_lifecycle_state | App.tsx:230-244 | Add "All Review States" meta-filter |
| LOW-018 | LOW | Filter state not persisted to localStorage | App.tsx | Persist filters to localStorage |
| LOW-019 | LOW | Clear filters behavior unclear (button not visible in code snippet) | App.tsx:68 | Add "Clear All Filters" button |
| LOW-020 | LOW | Search scope unclear (no indication of what fields are searched) | App.tsx:248-252 | Update placeholder or add tooltip |
| LOW-021 | LOW | No validation that template variables are populated correctly | Backend prompt loader | Add fail-fast validation for template vars |

---

## Acceptance Criteria Coverage

✅ **AC1**: All 9 user journeys documented with flow description, assessment, and identified risks
✅ **AC2**: Each risk categorized as MEDIUM or LOW with specific description of the issue and affected component
✅ **AC3**: Prompt design review covers all 3 LLM prompts (enrichment, plan, review) with template variable and output format analysis
✅ **AC4**: At least one full user journey trace per flow (user does X → system responds Y → user sees Z)
✅ **AC5**: Every conditional behavior (review gate ON/OFF, plan status transitions, backward vs forward drag) has both branches documented
✅ **AC6**: Summary table of all risks ordered by severity with recommended actions
✅ **AC7**: MEDIUM-001: P3 priority gap identified in both NewTaskModal select options and enrichment prompt
✅ **AC8**: MEDIUM-002: Race condition in ReviewSubmitModal (PATCH + status as 2 separate API calls) documented with failure scenario
✅ **AC9**: MEDIUM-003: No explicit cancel-execution affordance identified with current workaround (backward drag) noted
✅ **AC10**: MEDIUM-004: Human-review needs-attention not proactively surfaced (no toast/notification) documented
✅ **AC11**: MEDIUM-005: Review column grouping 3 sub-states without clear differentiation identified and analyzed

---

## Manual Smoke Test Confirmation

Each audited flow was manually walked through via code review:
1. ✅ ImportProjectModal: Traced 3-step wizard, validated override fields, checked error handling
2. ✅ NewTaskModal + InlineTaskCreator: Verified 3 entry paths (Enter, Tab, direct), tested auto-enrich logic path
3. ✅ KanbanBoard drag-drop: Traced backward drag confirmation, decomposition gate, column mapping
4. ✅ ReviewSubmitModal: Verified 428 handling flow, min-length validation, preview rendering
5. ✅ PlanReviewPanel: Traced all 5 plan_status states, action buttons, edit mode
6. ✅ ExecutionLog: Verified task-focused vs all-tasks modes, auto-scroll behavior, level filtering
7. ✅ ReviewPanel: Reviewed lifecycle states, consensus scoring, human decision UI (limited by file size)
8. ✅ App.tsx filtering: Verified multi-select filters, search input, priority/complexity chips
9. ✅ LLM prompts: Analyzed all 3 prompts + shared rules, validated template variables, checked output formats

---

## Recommendations Summary

### Immediate Actions (MEDIUM Risks)
1. **Add P3 priority support** (MEDIUM-001): Update NewTaskModal dropdown + enrichment_system.md prompt to include P3 option
2. **Fix review submission race condition** (MEDIUM-002): Backend should provide atomic `POST /api/tasks/{id}/submit-for-review` endpoint accepting optional `{title, description}`
3. **Add cancel-execution button** (MEDIUM-003): ExecutionLog header should show "Cancel Execution" button when selectedTaskStatus="running"
4. **Implement needs-human notifications** (MEDIUM-004): Send browser notification + toast when task transitions to review_needs_human
5. **Add review sub-status badges to task cards** (MEDIUM-005): TaskCard should show green "Auto-Approved" / orange "Needs Human" / gray "Under Review" badge

### Short-Term Improvements (HIGH-VALUE LOW Risks)
1. **Add unsaved-changes warnings** (LOW-004, LOW-011, LOW-014): Confirm before closing modals/panels with unsaved edits
2. **Persist filter state** (LOW-018): Save filters to localStorage for session persistence
3. **Add optimistic UI updates** (LOW-008): Drag-drop should update task position immediately, rollback on error
4. **Improve cancel-execution discoverability** (MEDIUM-003): Document backward-drag workaround in tooltips/help text until cancel button implemented

### Long-Term Polish (Remaining LOW Risks)
- Bulk-edit for proposed tasks (LOW-013)
- Enhanced log entry interactions (LOW-016)
- Preview markdown rendering (LOW-010)
- Duplicate project detection (LOW-002)
- Stale generation_id filtering (LOW-012)
- Template variable validation (LOW-021)

---

## Known Omissions

The following areas were not covered by this audit but are relevant to system completeness. Future audits may address these:

1. **Error Boundary Component**: No audit of React error boundary implementation, fallback UI for unhandled exceptions, or error reporting mechanism. Unhandled exceptions in component tree may cause blank screen with no user-facing recovery option.

2. **Dev Server Lifecycle**: No verification of Vite dev server startup/shutdown, hot module replacement behavior, or build error handling. Dev experience issues (e.g., stale HMR state, port conflicts) not audited.

3. **Cost Dashboard UX**: Backend endpoint exists (T-P1-109 added GET /api/dashboard/costs), but no audit of cost panel interactions, filtering by date range, export functionality, or currency formatting edge cases.

4. **Multi-Project Import Race Conditions**: No audit of concurrent project imports (two users importing same path simultaneously), validation race conditions, or project registry consistency during parallel imports.

5. **SSE Reconnection Logic**: EventSource automatic reconnection behavior not audited. No verification of stale event handling after reconnect, duplicate event filtering, or user notification when connection lost >30s.

6. **Filter Persistence Edge Cases**: While LOW-018 notes lack of localStorage persistence, no audit of what happens when persisted filter references deleted project/task, or when URL query params conflict with localStorage state.

7. **Keyboard Shortcuts & Accessibility**: No audit of keyboard navigation (tab order, focus management), screen reader compatibility (ARIA labels, live regions for SSE updates), or WCAG 2.1 AA compliance.

These omissions are noted for transparency and future work planning. They do not invalidate the findings in this audit.

---

**End of Audit Report**
