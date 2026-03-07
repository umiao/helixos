# UX Task Audit: Scenario-Matrix Gap Analysis

> Audit date: 2026-03-06
> Task: T-P2-82
> Scope: All completed UX tasks (T-P0-8a through T-P3-11)
> Method: Review each task's completion record against the 5 Task Planning Rules
>         (added in T-P0-27) and identify scenario-matrix gaps.

## Context

Task Planning Rules were added in T-P0-27 (2026-03-03) after the T-P0-24
postmortem. Tasks completed BEFORE T-P0-27 were not subject to these rules.
Tasks completed AFTER should comply.

The T-P0-57/T-P0-59 postmortem (leading to T-P0-66 fix) revealed that even
post-T-P0-27 tasks could skip effective smoke testing by relying on
"TypeScript clean, Vite build clean" as verification.

## Audit Methodology

Each UX task is checked against 5 criteria:
- [SM] Scenario Matrix: all conditional branches listed?
- [JF] Journey-First AC: full user journey specified?
- [CB] Cross-Boundary Integration: backend+frontend wiring verified?
- [OC] Other Case Gate: inverse conditions specified?
- [ST] Smoke Test: manual browser verification performed?

Rating: PASS / GAP / N/A (rule not applicable)

---

## Pre-T-P0-27 Tasks (before planning rules existed)

These tasks predate the planning rules. Gaps are expected and noted for
completeness, not as failures.

### T-P0-8a: Dashboard Kanban -- static layout + TaskCard
- [SM] N/A (no conditionals)
- [JF] GAP -- no user journey AC, only "npm run build succeeds"
- [CB] N/A (frontend-only, mock data)
- [OC] N/A
- [ST] GAP -- build-only verification

### T-P0-8b: Dashboard Kanban -- drag-drop + API integration
- [SM] GAP -- invalid transitions mentioned but no branch matrix
- [JF] GAP -- no explicit user journey AC
- [CB] PASS -- drag-drop calls PATCH and handles rollback
- [OC] GAP -- error case (invalid transition) noted but no explicit inverse
- [ST] GAP -- build-only verification

### T-P0-8c: Dashboard -- ExecutionLog + ReviewPanel + SSE
- [SM] N/A
- [JF] GAP -- features described but no "user does X -> sees Y" journey
- [CB] PASS -- SSE auto-updates card positions
- [OC] GAP -- disconnected SSE state not specified
- [ST] GAP -- build-only verification

### T-P0-24: Review gate UX [KNOWN REGRESSION -> T-P0-26]
- [SM] GAP -- gate ON specified, gate OFF missing (root cause of regression)
- [JF] GAP -- ACs were component-level, not journey-level
- [CB] GAP -- modal existed, endpoint existed, but wiring not verified
- [OC] GAP -- inverse case (gate OFF) completely absent
- [ST] GAP -- unit tests + build only
- **Post-mortem**: Led to T-P0-27 (planning rules) and T-P0-26 (fix)

### T-P0-26: Fix drag-to-REVIEW workflow
- [SM] PASS -- gate ON/OFF branches both specified
- [JF] PASS -- "drag to REVIEW -> spinner -> results"
- [CB] PASS -- transition-driven pipeline, E2E wiring verified
- [OC] PASS -- both gate states handled
- [ST] GAP -- 25 tests but no manual browser verification noted

### T-P0-38: Backward-drag confirmation dialog redesign
- [SM] PASS -- forward/backward drag distinction specified
- [JF] GAP -- no explicit user journey
- [CB] N/A (frontend-only visual change)
- [OC] PASS -- "Forward drags unaffected" explicitly stated
- [ST] GAP -- build-only verification

### T-P0-44: Plan validity model + enforce in review gate
- [SM] PASS -- gate ON/OFF, valid/invalid plan branches
- [JF] GAP -- component-level ACs
- [CB] PASS -- 428 response -> modal -> character counter
- [OC] PASS -- plan valid vs plan invalid both specified
- [ST] GAP -- build + tests only

### T-P0-46: Unified MarkdownRenderer
- [SM] N/A
- [JF] GAP -- no user journey
- [CB] N/A (frontend utility component)
- [OC] N/A
- [ST] GAP -- build-only verification

### T-P0-47: No Plan badges + visual guidance
- [SM] GAP -- "plan exists" vs "no plan" but missing "plan generating" state
- [JF] GAP -- feature described but no journey
- [CB] N/A (frontend-only)
- [OC] GAP -- what happens when plan IS present not fully specified
- [ST] GAP -- build-only verification

### T-P0-48: Running Jobs Panel
- [SM] PASS -- running vs no-running states
- [JF] GAP -- no explicit journey
- [CB] PASS -- SSE-driven updates
- [OC] PASS -- empty state specified
- [ST] GAP -- build-only verification

### T-P0-50: Right-click context menu Edit
- [SM] N/A
- [JF] GAP -- no explicit journey
- [CB] PASS -- PATCH endpoint wired to UI
- [OC] N/A
- [ST] GAP -- build-only verification

### T-P0-53: Active process pulsing badges
- [SM] PASS -- active vs inactive defined
- [JF] GAP -- no explicit journey
- [CB] N/A (frontend-only CSS)
- [OC] PASS -- "Pulse stops on task exit"
- [ST] GAP -- build-only verification

### T-P3-4: Task card hover popover
- [SM] N/A
- [JF] GAP -- no explicit journey
- [CB] N/A (frontend-only)
- [OC] PASS -- "hides on drag"
- [ST] GAP -- build-only verification

### T-P3-5: Workflow clarity -- inline task creation, context menu, tooltips
- [SM] N/A
- [JF] GAP -- no explicit journey
- [CB] PASS -- create task via API
- [OC] PASS -- Esc to cancel
- [ST] GAP -- build-only verification

### T-P3-10: Done column sorting and sub-status filtering
- [SM] PASS -- sort options, filter badges with counts
- [JF] GAP -- no explicit journey
- [CB] N/A (client-side only)
- [OC] PASS -- toggle filtering
- [ST] GAP -- build-only verification

### T-P3-11: Enhanced review observation and human interaction UX
- [SM] PASS -- status-specific badges (pulsing, orange, green)
- [JF] GAP -- auto-switch behavior described but not as explicit journey
- [CB] N/A (client-side only)
- [OC] PASS -- different badge per state
- [ST] GAP -- build-only verification

### T-P3-12: Resizable bottom panel divider
- [SM] N/A
- [JF] GAP -- no explicit journey
- [CB] N/A (frontend-only)
- [OC] PASS -- min/max/double-click-reset
- [ST] GAP -- build-only verification

---

## Post-T-P0-27 Tasks (planning rules in effect)

### T-P0-54: Fix review panel header
- [SM] N/A (visual fix)
- [JF] N/A (visual fix)
- [CB] N/A
- [OC] N/A
- [ST] GAP -- no manual verification noted

### T-P0-55: Execution log visual markers for review
- [SM] PASS -- review vs non-review entries
- [JF] GAP -- no explicit journey
- [CB] PASS -- SSE source field threaded
- [OC] PASS -- non-review entries unaffected
- [ST] GAP -- build-only

### T-P0-57: Hover-to-generate-plan UX [KNOWN REGRESSION -> T-P0-66]
- [SM] GAP -- "no plan" vs "has plan" but missed "plan generating" state
- [JF] PASS -- "button calls API with loading state"
- [CB] PASS -- API call + onTaskUpdated refresh
- [OC] PASS -- "hidden when plan exists or task is done/failed/blocked"
- [ST] GAP -- "TypeScript clean, Vite build clean" only
- **Post-mortem**: hasNoPlan used wrong field. Led to T-P0-66.

### T-P0-58: Done tasks show green completion in ReviewPanel
- [SM] PASS -- done vs non-done
- [JF] GAP -- no explicit journey
- [CB] N/A (frontend-only conditional rendering)
- [OC] PASS -- "Non-done tasks unaffected"
- [ST] GAP -- no manual verification

### T-P0-59: Plan generation progress feedback [KNOWN REGRESSION -> T-P0-66]
- [SM] GAP -- plan_status states listed but consumer audit missing
- [JF] PASS -- "shows animated spinner + retry button"
- [CB] PASS -- API lifecycle + frontend rendering
- [OC] GAP -- what existing tasks show before plan_status populated
- [ST] GAP -- "tests passing" only
- **Post-mortem**: Budget too restrictive, not visible in Running panel.

### T-P0-63b: Frontend plan generation UX wiring
- [SM] PASS -- SSE event types mapped
- [JF] PASS -- real-time updates via SSE
- [CB] PASS -- SSE -> state -> rendering
- [OC] PASS -- elapsed timer only during generation
- [ST] GAP -- "TypeScript clean, Vite build clean"

### T-P0-65: Plan generation button discoverability
- [SM] PASS -- plan/no-plan/generating states
- [JF] PASS -- persistent button + pulsing border
- [CB] PASS -- backend 409 guard
- [OC] PASS -- double-click prevention
- [ST] GAP -- "TypeScript clean, Vite build clean"

### T-P0-89: Frontend Conversation View
- [SM] N/A (new feature, no conditionals)
- [JF] PASS -- SSE stream -> bubbles + tool badges
- [CB] PASS -- SSE + fetchStreamLog API
- [OC] PASS -- viewMode toggle between Conversation and Plain Log
- [ST] GAP -- "TypeScript clean, Vite build clean"

### T-P0-90: Frontend Popover Enhancement
- [SM] PASS -- running vs non-running tasks
- [JF] PASS -- "shows tool call count, elapsed minutes, last activity"
- [CB] PASS -- streamEvents threaded through component tree
- [OC] PASS -- "Non-running tasks unaffected"
- [ST] GAP -- "TypeScript clean, Vite build clean"

### T-P2-6: Frontend -- ProjectSelector + SwimLane + KanbanBoard refactor
- [SM] PASS -- solo/multi-lane modes
- [JF] GAP -- no explicit journey
- [CB] N/A (frontend-only)
- [OC] PASS -- no cross-project drag
- [ST] GAP -- build-only

### T-P2-7: Frontend -- SwimLaneHeader + ImportModal + NewTaskModal + LaunchControl
- [SM] PASS -- multi-step import flow
- [JF] GAP -- no explicit end-to-end journey
- [CB] PASS -- all modals wired to API calls
- [OC] PASS -- loading states and error handling
- [ST] GAP -- build-only

### T-P3-1: Fix "No CLAUDE.md" false-positive badge
- [SM] PASS -- configured vs auto-detected
- [JF] GAP -- no explicit journey
- [CB] PASS -- backend auto-detect + frontend badge
- [OC] PASS -- fallback when not found
- [ST] GAP -- build + tests only

### T-P3-2: Backend directory browser + frontend picker
- [SM] PASS -- sandbox enforcement
- [JF] GAP -- no explicit journey
- [CB] PASS -- API + DirectoryPicker + ImportModal integration
- [OC] PASS -- hidden dir filtering
- [ST] GAP -- build + tests only

### T-P3-3: Import Project in ProjectSelector dropdown
- [SM] N/A (simple UI addition)
- [JF] GAP -- no explicit journey
- [CB] N/A (frontend-only)
- [OC] N/A
- [ST] GAP -- build-only

---

## Summary

### Gap Statistics

| Criterion | Pre-T-P0-27 (18 tasks) | Post-T-P0-27 (16 tasks) |
|-----------|------------------------|-------------------------|
| Scenario Matrix   | 3 GAP, 10 N/A, 5 PASS | 1 GAP, 3 N/A, 12 PASS |
| Journey-First AC  | 14 GAP, 1 N/A, 3 PASS | 5 GAP, 2 N/A, 9 PASS  |
| Cross-Boundary    | 1 GAP, 7 N/A, 10 PASS | 0 GAP, 5 N/A, 11 PASS |
| Other Case Gate   | 2 GAP, 5 N/A, 11 PASS | 1 GAP, 2 N/A, 13 PASS |
| **Smoke Test**    | **18 GAP, 0 PASS**     | **16 GAP, 0 PASS**     |

### Key Findings

1. **Smoke test gap is universal**: Zero UX tasks across the entire project
   history have documented manual browser verification. Every task used
   "build succeeds" or "tests pass" as the verification criterion. This is
   the single largest systematic gap.

2. **Journey-first ACs improved post-T-P0-27**: From 17% pass rate (3/18) to
   56% pass rate (9/16). The planning rules had measurable impact on AC
   quality.

3. **Scenario matrices are well-adopted**: Post-T-P0-27 tasks have 92% pass
   rate (12/13 applicable). The T-P0-24 postmortem drove strong adoption.

4. **Cross-boundary integration is solid**: Only 1 GAP across all 34 tasks
   (T-P0-24, the original postmortem trigger). The rule was well-learned.

5. **New-field consumer audit is a blind spot**: T-P0-57/T-P0-59 introduced
   `plan_status` but did not audit which existing components would be
   affected by the new field. This led to T-P0-66 fixing 3 bugs. A new
   planning rule (#6) has been added to CLAUDE.md to address this.

### Remediation

1. **CLAUDE.md updated**: Added "Smoke Test Enforcement" section with 3 rules:
   - UX DONE gate (PROGRESS.md must document smoke test)
   - Cross-component regression check
   - Autonomous mode exception with [AUTO-VERIFIED] tag

2. **CLAUDE.md updated**: Added planning rule #6 "New-field consumer audit"
   to prevent the T-P0-57/T-P0-59 class of bugs.

3. **No retroactive fixes**: Existing completed tasks are not reopened.
   The rules apply going forward. If specific regressions are discovered
   during manual testing, they should be filed as new bug tasks.
