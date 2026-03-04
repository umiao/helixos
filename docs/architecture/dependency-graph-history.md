# Dependency Graph (Historical)

> Relocated from TASKS.md as part of T-P0-51 (lifecycle model + archive separation).
> This graph documents the dependency relationships between all implemented tasks.
> All tasks in this graph are completed.

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

T-P0-33 [M] Fix review panel data bugs [DONE] (no deps)
  |
  +--> T-P0-34 [M] Request Changes + feedback loop [DONE] (needs T-P0-33)
         |
         +--> T-P0-35 [M] Inline plan editing + versioned history [DONE] (needs T-P0-34)
                |
                +--> T-P0-36 [M] Claude --plan integration [P1] (needs T-P0-35)

--- P0-CORE (review state machine) ---

T-P0-40 [M] ReviewLifecycleState enum (no deps)
  |
  +--> T-P0-41 [M] Pipeline emits lifecycle state (needs T-P0-40)
         |
         +--> T-P0-42 [M] ReviewPanel state-driven (needs T-P0-40 + T-P0-41)

T-P0-43 [S] Soft-delete sync deleted_source (no deps)

--- P0-BEHAVIOR (gating + selection) ---

T-P0-44 [M] Plan validity model + review gate (no deps)
  |
  +--> T-P0-39 [S] Block review without plan [subsumed] (needs T-P0-44)

T-P0-45 [S] Default project selection is_primary (no deps)

T-P0-38 [S] Backward-drag confirmation dialog (no deps)

--- P1-UX (polish) ---

T-P0-46 [M] MarkdownRenderer abstraction (no deps) [DONE]
T-P0-47 [M] No Plan badges + visual guidance (no deps, pairs with T-P0-44)
```
