# Claude Code Workflow Guide

A practitioner's guide to building robust, self-enforcing AI-assisted development workflows
with Claude Code. Based on patterns developed across real production projects.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Enforcing Code Quality](#2-enforcing-code-quality)
3. [Extended Thinking & Reflection](#3-extended-thinking--reflection)
4. [Human-AI Cooperation: Structured Handoff](#4-human-ai-cooperation-structured-handoff)
5. [Self-Learning Loops](#5-self-learning-loops)
6. [Autonomous Multi-Session Execution](#6-autonomous-multi-session-execution)
7. [Session Continuity](#7-session-continuity)
8. [Skills & Agents](#8-skills--agents)
9. [Day-to-Day Loop](#9-day-to-day-loop)
10. [Lessons & Anti-Patterns](#10-lessons--anti-patterns)

---

## 1. Architecture Overview

### The Problem with Instructions Alone

CLAUDE.md (the project-level instructions file) is powerful. You write rules, and Claude
follows them -- most of the time. But instructions are suggestions; hooks are enforcement.
A CLAUDE.md rule saying "always run tests before stopping" works until context pressure
builds up, the conversation gets long, and Claude skips the step to save tokens. A stop
hook that runs `pytest` and blocks exit on failure works every time.

The gap between "Claude usually follows instructions" and "the system always enforces
invariants" is where bugs, forgotten steps, and quality drift live. Closing that gap
requires architecture, not longer instructions.

### The Five Pillars

A complete Claude Code workflow rests on five pillars:

| Pillar | File | Purpose |
|--------|------|---------|
| **Rules** | `CLAUDE.md` | What Claude should do (instructions, constraints, conventions) |
| **Tasks** | `TASKS.md` | What needs to be done (backlog, priorities, dependencies) |
| **Journal** | `PROGRESS.md` | What was done (append-only session log) |
| **Knowledge** | `LESSONS.md` | What was learned (mistakes, patterns, gotchas) |
| **Guardrails** | `.claude/hooks/` | What must be enforced (automated checks, gates) |

The first four are passive -- they're files Claude reads and writes. The fifth is active:
hooks run automatically at specific lifecycle events and can block operations that violate
your invariants. Together they form a closed loop: rules define expectations, tasks drive
work, the journal records progress, knowledge captures learning, and guardrails enforce
the rules mechanically.

### Why Five and Not One?

Each pillar serves a different temporal need:

- **Rules** are stable (change rarely, read every session)
- **Tasks** are medium-term (change per session, track across weeks)
- **Journal** is append-only (never edited, always grows)
- **Knowledge** is curated (only entries that cross a pain threshold)
- **Guardrails** are permanent (once a check is added, it runs every time)

Putting everything in CLAUDE.md creates a bloated file that exceeds the useful context
window. The recommended max for CLAUDE.md is ~120 lines of always-relevant content.
Mode-specific rules (autonomous mode, exit protocol details) belong in separate files
loaded conditionally by hooks.

### Hook Lifecycle Points

Claude Code provides four hook events:

| Event | When it fires | Common uses |
|-------|--------------|-------------|
| `PreToolUse` | Before a tool executes | Block dangerous commands, guard secrets |
| `PostToolUse` | After a tool executes | Validate YAML, warn on schema changes |
| `Stop` | When Claude tries to end the session | Enforce lint, tests, documentation |
| `SessionStart` | When a new session begins | Load context, display task status |

Hooks can be `command` type (run a script, exit code determines pass/fail) or `prompt`
type (an LLM evaluates a condition and returns a JSON verdict). Both are configured in
`.claude/settings.json`.

---

## 2. Enforcing Code Quality

### Stop Hooks as Exit Gates

The most impactful hooks are stop hooks -- they fire when Claude tries to end a session
and can block the exit if checks fail. This creates a "you can't leave until the room
is clean" dynamic that dramatically improves output quality.

A typical stop hook chain runs three checks in sequence:

1. **Prompt-based evaluation**: An LLM reviews whether Claude completed the work,
   verified results, and updated documentation (PROGRESS.md, TASKS.md)
2. **Lint check**: Runs `ruff check` (or your linter of choice) and blocks on violations
3. **Test check**: Runs `pytest` and blocks on failures

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Evaluate whether Claude should be allowed to stop. Rules: ...",
            "timeout": 30
          },
          {
            "type": "command",
            "command": "python .claude/hooks/lint_check.py",
            "timeout": 30
          },
          {
            "type": "command",
            "command": "python .claude/hooks/test_check.py",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

When a stop hook blocks (exit code 2), Claude receives the stderr output and must fix
the issue before trying to stop again. This creates a feedback loop: Claude writes code,
tries to stop, gets blocked by failing tests, fixes the tests, tries again. The loop
continues until all checks pass.

### PreToolUse: Blocking Dangerous Commands

PreToolUse hooks intercept tool calls before execution. Two essential guards:

**Dangerous command blocker**: Intercepts Bash commands matching patterns like
`rm -rf /`, `git push --force main`, `git reset --hard`, `DROP TABLE`, and `curl | bash`.
The hook outputs a JSON block message and Claude sees the rejection, preventing
accidental damage.

```python
DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+/\b"),
    re.compile(r"\bgit\s+push\s+--force\s+(origin\s+)?main\b"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
    # ... more patterns
]
```

**Secret guard**: Intercepts Write/Edit operations that target `.env` files or contain
API key patterns (OpenAI `sk-...`, AWS `AKIA...`, GitHub `ghp_...`, etc.). Prevents
Claude from accidentally committing secrets to source files.

Both hooks output `{"decision": "block", "reason": "..."}` to stdout when blocking,
and exit 0 silently when allowing.

### PostToolUse: Validation After Changes

PostToolUse hooks fire after a tool completes. They're ideal for non-blocking warnings:

**File watch warnings**: When files in critical paths (e.g., `src/models/`,
`src/database/schema.py`) are modified, emit a stderr warning reminding Claude to
run tests. Non-blocking (always exit 0) but ensures Claude sees the reminder.

**YAML validation**: After any Write/Edit to a `.yaml`/`.yml` file, parse it with
PyYAML and warn on syntax errors. Catches malformed YAML immediately rather than
at runtime.

### Caching via Git Fingerprint

Stop hooks run every time Claude tries to stop, which can be slow if lint + test
takes 30+ seconds. The solution: cache the last passing state.

```python
def check_stop_cache(cache_name: str) -> bool:
    """Check if no files changed since last pass."""
    fingerprint = hashlib.sha256(
        subprocess.check_output(["git", "status", "--porcelain"])
    ).hexdigest()[:16]
    return stored_fingerprint == fingerprint
```

After a successful pass, write the git status fingerprint to `.claude/last_lint_pass`
(or `last_test_pass`). On next stop attempt, if the fingerprint matches, skip the check.
Any file change invalidates the cache automatically.

### Shared hook_utils.py

Every hook needs the same infrastructure: UTF-8 stream initialization (critical on
Windows where the default is cp1252), safe JSON parsing from stdin with diagnostics,
and a top-level exception wrapper. Rather than duplicating this in every hook, extract
it into a shared `hook_utils.py` module:

```python
def run_hook(hook_name: str, main_fn: Callable) -> None:
    """Entry point for all hooks."""
    init_utf8_streams()
    hook_input = safe_read_stdin(hook_name)
    if hook_input is None:
        sys.exit(0)  # Never block on infrastructure errors
    try:
        main_fn(hook_input)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[HOOK ERROR] {hook_name}: {exc}", file=sys.stderr)
        sys.exit(0)  # Never block user due to hook bugs
```

The critical design principle: **hooks must never crash**. Infrastructure errors (bad
stdin, missing fields, encoding problems) should produce a diagnostic warning to stderr
and exit 0 (allow the operation). Only intentional blocks (failing tests, lint errors)
should exit 2.

---

## 3. Extended Thinking & Reflection

### Prompt-Based Stop Hooks

The most powerful reflection mechanism is a prompt-type stop hook. When Claude tries to
stop, an LLM evaluates whether the work is truly complete. The prompt encodes your exit
rules:

```
Rule 1 - Work completeness: Did Claude finish the request? Unresolved errors or TODOs = block.
Rule 2 - Sanity check: If code was written, was it verified? Unverified work = block.
Rule 3 - PROGRESS.md: Was a session entry appended? Missing = block.
Rule 4 - TASKS.md: Was task status updated? Missing = block.
Rule 5 - LESSONS.md: Only flag if a significant bug wasn't logged.
```

The hook responds with `{"ok": true}` or `{"ok": false, "reason": "Rule N: ..."}`.
When blocked, Claude sees the specific rule violation and can fix it.

This is "extended thinking" in the truest sense -- Claude is forced to reflect on its
own work before being allowed to leave. The prompt acts as a reviewer that checks
process compliance, not just code correctness.

**Important schema note**: Prompt hooks expect exactly `{"ok": true}` or
`{"ok": false, "reason": "..."}`. Not `{"decision": "approve"}` or any other schema.
If you get "JSON validation failed" errors, check the schema first -- it's almost
certainly a contract mismatch, not LLM non-determinism.

### LESSONS.md as Forced Post-Mortems

LESSONS.md is not a diary -- it's a curated knowledge base with a deliberate entry
threshold: only log things that cost more than 10 minutes to debug, or would bite
someone again. Each entry follows a structured format:

```markdown
### [YYYY-MM-DD] Short descriptive title
- **Context**: What I was trying to do
- **What went wrong**: The core insight
- **Fix**: How to do it right
- **Tags**: #tag1 #tag2
```

The tags enable machine-readable filtering. The SessionStart hook can filter lessons
by tags relevant to the current task, showing only the 2-3 most applicable entries
rather than the entire history.

### /review and /improve Skills

Skills (see Section 8) provide on-demand reflection:

- **/review**: Triggers a code review against project invariants. Spawns a reviewer
  agent that checks type hints, docstrings, secrets, test coverage, and convention
  compliance. Returns structured findings: Critical/Warning/Suggestion.

- **/improve**: Scans for quality improvements (dead code, untested modules, complexity
  hotspots). Generates actionable task entries for discovered issues.

Both skills use subagents to avoid polluting the main conversation context with
review details. The main session gets a summary; the detailed analysis runs in
a separate context.

---

## 4. Human-AI Cooperation: Structured Handoff

### Design Principle

This section describes the **Structured Handoff** paradigm: a named, repeatable
pattern for tasks that require human input before the AI can proceed. The core
principle is that the AI never wastes a session on uncompletable work, and the
human always knows exactly what is needed and where to put it. Both sides have
clear responsibilities and a machine-enforced protocol connecting them.

### Four-Step Lifecycle

Every NEEDS-INPUT task moves through four stages:

```
 1. TAG               2. GUIDE             3. VALIDATE          4. UNBLOCK
 Developer adds       /collect-input       input-reviewer       /collect-input
 [NEEDS-INPUT]        guides human         agent runs           unblock <task-id>
 tag in TASKS.md  --> through the      --> task-specific     --> removes tag,
                      per-task spec        checks on files       task is ready
                      (requirements,       ([PASS]/[FAIL]/
                      templates,           [WARN] verdicts)
                      examples)
```

The tag is the contract. The guide makes the contract actionable. The validator
ensures quality. The unblock is the handshake that releases the task back to
the AI. No step can be skipped -- premature unblocking (step 4 without step 3)
is an anti-pattern caught by the input-reviewer agent.

### The NEEDS-INPUT Protocol

Some tasks require human-provided files before Claude can proceed: test fixtures,
configuration preferences, API credentials setup, etc. These tasks are tagged in
TASKS.md with a `[NEEDS-INPUT: description]` marker:

```markdown
#### T-P1-3: JD structured parsing with LLM
- `[NEEDS-INPUT: 3-5 real JD fixture files in tests/fixtures/jd/]`
```

In autonomous mode, the orchestrator skips these tasks automatically. In interactive
mode, the `/collect-input` skill guides the human through providing the needed files.

### The /collect-input Skill

This skill handles the full lifecycle of human input collection:

| Subcommand | Purpose |
|-----------|---------|
| `status` | Show all NEEDS-INPUT tasks with file presence check |
| `<task-id>` | Guided walkthrough for one task (requirements, templates, examples) |
| `validate <task-id>` | Run automated validation checks on provided files |
| `unblock <task-id>` | After validation passes, remove the NEEDS-INPUT tag |

The validation step spawns an **input-reviewer agent** that runs task-specific checks.
For example, validating JD fixture files checks: file count (>= 3), minimum length
(>= 200 chars), presence of skills keywords, years-of-experience patterns, and coverage
of sponsorship language.

### Infrastructure Layout

```
docs/human_input/
  README.md              # Master checklist (all tasks needing input)
  T-P1-3_jd_fixtures.md  # Per-task specification
  T-P1-4_scoring_config.md
  ...
```

The master checklist tracks status with checkboxes (`[ ]` / `[x]`). The session_context
hook parses this file to display an `[INPUT]` section at startup showing which tasks
need human attention.

### Session Context Integration

The SessionStart hook includes an `[INPUT]` line in its output:

```
[INPUT] Human input needed: T-P1-3 (not started), T-P1-4 (blocked), T-P1-6b (blocked)
```

This gives Claude immediate visibility into which tasks require human action, preventing
wasted time on tasks that can't proceed.

### Multi-Project Blocking Awareness

When running autonomous mode across multiple projects, each project maintains its
own `session_state.json` with a `skipped_tasks` array. Tasks skipped due to
NEEDS-INPUT are recorded with their reason. To triage human effort across all
projects at once, aggregate the skip records:

```bash
# Aggregate NEEDS-INPUT skips across all projects
for f in ~/projects/*/.claude/session_state.json; do
  python3 -c "
import json, sys, os
state = json.load(open(sys.argv[1], encoding='utf-8'))
proj = os.path.basename(os.path.dirname(os.path.dirname(sys.argv[1])))
for s in state.get('skipped_tasks', []):
    if 'NEEDS-INPUT' in s.get('reason', ''):
        print(f'  {proj}: {s[\"task\"]} -- {s[\"reason\"]}')
" "$f"
done
```

The principle: **AI tags, skips, and surfaces -- human triages across projects,
provides input, unblocks, and resumes.** The AI never blocks waiting for human
action; the human never has to guess what the AI needs. The protocol makes both
sides' obligations explicit and machine-verifiable.

### Best Practices

- **One spec file per task**: Each NEEDS-INPUT task should have its own file in
  `docs/human_input/` (e.g., `T-P1-3_jd_fixtures.md`) with exact requirements,
  file format, minimum count, and content criteria.
- **Machine-checkable validation rules**: Every spec should define checks that the
  input-reviewer agent can run automatically (file count, minimum length, required
  patterns, format compliance). Vague criteria like "good quality" are not actionable.
- **Include an example file**: Place a `_example.txt` or `*.example` at the target
  location so the human has a concrete reference, not just a description.
- **Reference spec from TASKS.md**: The `[NEEDS-INPUT: ...]` tag in TASKS.md should
  name both the deliverable and the spec file, e.g.,
  `[NEEDS-INPUT: 3-5 JD files in tests/fixtures/jd/ -- see docs/human_input/T-P1-3_jd_fixtures.md]`
- **Keep the master checklist current**: `docs/human_input/README.md` should list all
  NEEDS-INPUT tasks with `[ ]`/`[x]` status so both human and SessionStart hook can
  parse it at a glance.

### Anti-Patterns

- **Silent skip**: Autonomous mode skips a NEEDS-INPUT task but does not record it
  in `session_state.json` or surface it via `[INPUT]`. The human never learns the
  task is blocked. Fix: always log skips and always show the `[INPUT]` line.
- **Premature unblock**: Running `/collect-input unblock` before validation passes.
  The task appears unblocked but fails immediately when the AI attempts it. Fix:
  the unblock step must require a passing validation run.
- **Vague specs**: A spec that says "provide some test data" without file count,
  format, content criteria, or an example. The human guesses, provides wrong input,
  validation fails, time is wasted. Fix: every spec must be machine-checkable.
- **Over-blocking**: Tagging a task as NEEDS-INPUT when the AI could generate
  reasonable defaults or synthetic data. Only tag when the input genuinely requires
  human judgment or access (real credentials, domain-specific examples, subjective
  preferences). Fix: before tagging, ask "can the AI produce a reasonable default?"

---

## 5. Self-Learning Loops

### Three Tiers of Knowledge

Knowledge flows through three tiers with increasing persistence and scope:

| Tier | File | Scope | Loaded |
|------|------|-------|--------|
| **Session** | `LESSONS.md` | Per-project | By SessionStart hook (filtered by task) |
| **Cross-session** | `~/.claude/.../memory/MEMORY.md` | Per-project, cross-conversation | Auto-loaded every session |
| **Global** | `~/.claude/CLAUDE.md` | All projects | Auto-loaded every session |

### LESSONS.md: Per-Session Knowledge

Written during sessions when something goes wrong or a useful pattern is discovered.
The threshold is deliberate: only log if the bug took >10 minutes to debug, the
behavior was surprising, or the pattern would help future sessions.

Example entry:
```markdown
### [2026-02-26] Hooks must defend against bad stdin
- **Context**: 7 hooks used bare json.load(sys.stdin). /clear triggered hooks with empty input.
- **What went wrong**: No error handling. Silent failures with zero diagnostic info.
- **Fix**: Always wrap stdin parsing in try/except. Use shared hook_utils.py.
- **Tags**: #hooks #json #error-handling
```

### MEMORY.md: Cross-Session Knowledge

Claude Code's auto-memory system persists a `MEMORY.md` file across conversations.
This is where patterns confirmed across multiple sessions graduate to. Unlike
LESSONS.md (which is project-specific and append-only), MEMORY.md is curated and
updated -- outdated entries are removed, wrong conclusions are corrected.

Good candidates for MEMORY.md:
- Stable patterns confirmed across multiple interactions
- Key architectural decisions and file paths
- User preferences for workflow and communication style
- Solutions to recurring problems

Bad candidates:
- Session-specific context (current task, in-progress work)
- Unverified conclusions from reading a single file
- Anything that duplicates CLAUDE.md instructions

### Tag-Based Lesson Filtering

The SessionStart hook filters LESSONS.md entries by relevance to the current task.
A tag map connects task IDs to relevant tags:

```python
tag_map = {
    "T-P1-3": ["#llm", "#parsing", "#pydantic"],
    "T-P1-6b": ["#scraping", "#selenium", "#ats"],
}
```

When starting a session for T-P1-3, only lessons tagged with `#llm`, `#parsing`, or
`#pydantic` are shown, keeping the context focused. If no tag matches, the last 3
entries are shown as fallback.

For the generalized template, use keyword-based filtering instead of a hardcoded
tag map: extract keywords from the task title/description and match against lesson tags.

---

## 6. Autonomous Multi-Session Execution

### The Three-Layer Architecture

Autonomous execution uses three layers that separate concerns cleanly:

```
Layer 1: Orchestrator (autonomous_run.sh)
  - Launches Claude Code sessions in a loop
  - Tracks consecutive failures
  - Checks session_state.json for completion
  - Handles lockfile for concurrent run protection

Layer 2: Session Rules (autonomous.md)
  - Loaded by SessionStart hook when mode=autonomous
  - One task per session, highest priority unblocked
  - Git commit per task, retry logic, stop conditions

Layer 3: State Tracking (session_state.json)
  - Persists between sessions
  - Records current task, retry count, completed/skipped tasks
  - "all_done" flag stops the orchestrator loop
```

### The Orchestrator Script

The bash orchestrator is intentionally simple -- it doesn't understand task logic,
just session lifecycle:

```bash
while [ $session_count -lt $MAX_SESSIONS ]; do
  start_sha=$(git rev-parse HEAD)

  claude -p "Autonomous mode. Pick ONE unblocked task, complete it, commit, stop." \
    --allowedTools "Read,Write,Edit,Bash,Glob,Grep,Task" \
    --max-turns 200

  # Check if all done
  if python3 -c "import json; state=json.load(open('.claude/session_state.json')); exit(0 if state.get('all_done') else 1)"; then
    break
  fi

  # Track failures (distinguish context exhaustion from real failures)
  current_sha=$(git rev-parse HEAD)
  if [ "$current_sha" = "$start_sha" ]; then
    consecutive_failures=$((consecutive_failures + 1))
  fi
done
```

Key features:
- **PID lockfile**: Prevents concurrent runs (`.claude/autonomous.lock`)
- **Progress detection**: Compares git SHA before/after session. New commits = progress
  (context exhaustion, not failure). No commits = real failure.
- **Consecutive failure limit**: Stops after 2 consecutive failures to prevent infinite loops

### Per-Task Git Commits

Each completed task gets its own git commit with format `[T-XX-N] Brief description`.
This gives the user:
- Clean per-task history to review individually
- Easy revert if a task's changes are wrong
- Clear audit trail of what happened in each autonomous session

### Retry Logic

Each task gets 2 attempts. If it fails twice:
1. Mark as BLOCKED in TASKS.md with the failure reason
2. Log the failure to LESSONS.md
3. Record the skip in session_state.json
4. Move to the next task

### NEEDS-INPUT Skip

Tasks tagged `[NEEDS-INPUT]` are treated as blocked in autonomous mode. The orchestrator
skips them and logs to session_state.json. When the human provides the input and runs
`/collect-input unblock <task-id>`, the tag is removed and the next autonomous run
picks it up.

### Continuing Partial Tasks

For large (L-complexity) tasks that exceed a single session's context window:
1. The session creates a WIP checkpoint commit: `[T-XX-N WIP] partial progress`
2. Updates `.claude/checkpoint.json` with subtask progress
3. The next session reads the checkpoint and continues from where it left off

---

## 7. Session Continuity

### The SessionStart Hook

The most important hook for session continuity is SessionStart. It fires once at the
beginning of every session and outputs a context summary that Claude sees as its first
piece of information. This replaces the need for Claude to read multiple files at startup.

A well-designed SessionStart hook outputs:

```
=== SESSION CONTEXT ===

[AUTONOMOUS] Mode active. Completed: T-P0-5. Current: T-P1-1 (attempt 1/2).

[PROGRESS] RECENT PROGRESS:
## 2026-02-26 -- [T-P0-5] Pipeline tracking
- What I did: Created PipelineRun and FilterLog tables...
- Status: [DONE]

[TASKS] CURRENT TASKS:
[CURRENT TASK]
#### T-P1-1: User profile system
- Full details here for the current task...

T-P1-3: JD parsing [P1, L, depends: T-P1-2, NEEDS-INPUT]
T-P1-4: Scoring engine [P1, M, depends: T-P1-1, T-P1-3]

[CHECKPOINT] T-P1-1: 3/5 subtasks done. Next: create CLI command.

[INPUT] Human input needed: T-P1-3 (not started)

[TIP] RECENT LESSONS:
### [2026-02-26] Hook stdin error handling...

[AUTONOMOUS RULES]
# Autonomous Mode Rules (loaded from autonomous.md)...

=== END CONTEXT ===
```

### Two-Tier Task Detail

The hook uses two levels of detail for tasks:

- **Current task** (from session_state.json): Full block including acceptance criteria,
  dependencies, and file list
- **Other tasks**: One-line summary with priority, complexity, and dependency status

This keeps the context focused on the active work while maintaining awareness of the
broader backlog.

### checkpoint.json for Large Tasks

For L-complexity tasks that span multiple sessions, a structured checkpoint file
tracks granular progress:

```json
{
  "task": "T-P1-3",
  "subtasks": [
    {"name": "Create parser module", "done": true},
    {"name": "Implement extraction logic", "done": true},
    {"name": "Add hallucination guard", "done": false},
    {"name": "Write golden tests", "done": false}
  ],
  "last_working_file": "src/parsers/jd_parser.py",
  "last_working_line": 142
}
```

The SessionStart hook reads this and shows: `[CHECKPOINT] T-P1-3: 2/4 subtasks done.
Next: Add hallucination guard. Last working: src/parsers/jd_parser.py:142`

### PROGRESS.md as Append-Only Log

Every session appends an entry to PROGRESS.md, never edits previous entries. The format
is standardized:

```markdown
## YYYY-MM-DD HH:MM -- [T-XX-N] Brief Title
- **What I did**: 1-3 sentences
- **Deliverables**: Files created/modified
- **Sanity check result**: What was verified
- **Status**: [DONE] / [PARTIAL] / [BLOCKED]
- **Request**: Cross off task / No change
```

The stop hook enforces this: if substantive work was done but no PROGRESS.md entry
exists, the exit is blocked.

---

## 8. Skills & Agents

### Skills Orchestrate, Agents Execute

**Skills** are slash commands (e.g., `/review`, `/sanity-check`) defined in
`.claude/skills/<name>/SKILL.md`. They describe a multi-step procedure that Claude
follows when the user invokes the command. Skills are user-facing and orchestrate
the workflow.

**Agents** are sub-agent definitions in `.claude/agents/<name>.md`. They describe
specialized roles (reviewer, test runner, refactor advisor) that skills can spawn
via the Task tool. Agents run in separate context windows and return structured results.

The separation matters: skills own the user interaction and workflow; agents own the
analysis. A skill can spawn multiple agents, aggregate results, and present them
to the user.

### Core Skill Patterns

**/sanity-check**: Manually trigger the stop hook checks without stopping. Runs lint,
tests, and checks PROGRESS.md/TASKS.md freshness. Useful mid-session to verify you're
on track before wrapping up.

**/review**: Triggers code review against project invariants. Runs `git diff` to
identify changes, spawns the reviewer agent, and presents findings in a structured
Critical/Warning/Suggestion format.

**/e2e-test**: Runs the full test suite with verbose output. Parses results, identifies
failing tests, and suggests fixes by reading both the test file and the source it tests.

**/improve**: Idle-time improvement scanner. Runs lint, type checking, coverage gap
analysis, and dead code detection. Spawns the refactor-advisor agent for deeper analysis.
Generates TASKS.md entries for discovered issues.

**/collect-input**: Manages the NEEDS-INPUT lifecycle. Status, guided walkthroughs,
validation, and unblocking. Spawns the input-reviewer agent for file validation.

### Agent Patterns

**reviewer.md**: Checks recent changes against project invariants. Reviews type hints,
docstrings, secrets, rate limiting, test coverage. Outputs structured findings with
file:line references and severity levels.

**test-runner.md**: Runs tests, parses failures, reads both test and source to identify
root causes. Suggests minimal targeted fixes.

**refactor-advisor.md**: Scans for code duplication, complexity hotspots, dead code,
untested modules. Prioritizes by impact. Generates task entries for high-priority items.

**input-reviewer.md**: Validates human-provided files against task-specific checklists.
Each task section defines its own validation rules (file count, content patterns,
format requirements).

---

## 9. Day-to-Day Loop

### Interactive Workflow

The typical interactive session follows this flow:

```
SessionStart hook fires
  |
  v
Claude sees context summary (tasks, progress, lessons)
  |
  v
User gives instruction or Claude picks a task
  |
  v
Claude works (reads, edits, runs code)
  |  |
  |  +--> PreToolUse hooks guard each operation
  |  +--> PostToolUse hooks validate after changes
  |
  v
Claude tries to stop
  |
  v
Stop hooks fire (prompt + lint + test)
  |
  +--[PASS]--> Session ends cleanly
  |
  +--[FAIL]--> Claude sees failure message
                  |
                  v
              Claude fixes the issue
                  |
                  v
              Claude tries to stop again (loop)
```

The feedback loop between "try to stop" and "fix and retry" is the core enforcement
mechanism. Claude can't escape until quality gates pass.

### Autonomous Workflow

```
autonomous_run.sh launches
  |
  v
Session 1 starts --> SessionStart hook loads state
  |
  v
Claude picks highest-priority unblocked task
  |
  v
Works, completes, commits, updates state
  |
  v
Session ends --> orchestrator checks session_state.json
  |
  +--[all_done=true]--> Stop
  |
  +--[more tasks]--> Session 2 starts (fresh context)
  |
  +--[failure, no progress]--> Increment failure counter
       |
       +--[< max failures]--> Retry
       +--[>= max failures]--> Stop
```

Each session is independent with fresh context. State flows through files
(session_state.json, TASKS.md, PROGRESS.md, git commits), not through memory.

### The Ephemeral vs Persistent Principle

Any system with session-scoped lifetime (in-memory task tools, conversation text,
runtime-injected context) is NOT a substitute for persistent artifacts (files on disk,
git commits, PROGRESS.md entries). Only persistent artifacts survive sessions.

This is the single most important principle for multi-session workflows:
**if you did work but didn't write it to a file, you didn't do it.**

---

## 10. Lessons & Anti-Patterns

### Schema Mismatch != LLM Non-Determinism

When a hook returns "JSON validation failed", the instinct is to blame the LLM for
producing inconsistent output. But "validation failed" (schema mismatch) is not
"parse error" (bad JSON). The fix is almost always checking the expected schema
against the actual schema, not adding retry logic or abandoning the approach.

**Protocol when System A rejects System B's output**:
1. Read the exact error message
2. Read System A's documentation for the expected schema
3. Compare actual vs expected
4. Fix the minimal delta

Never attribute to non-determinism what can be explained by a schema mismatch.

### Never Use Bare json.load(sys.stdin)

stdin from Claude Code is an external IPC boundary. Treat it like untrusted network
input:
- Always wrap in try/except with diagnostic output (hook name, exception, input preview)
- On parse failure: warn to stderr + exit 0 (never block the user for infrastructure errors)
- Use a shared utility function, not copy-pasted try/except blocks

### Emoji on Windows

Windows console encoding (cp1252) cannot handle emoji characters. A single emoji
in a hook's stderr output causes `UnicodeEncodeError` which crashes the hook, which
blocks the user. Use ASCII text tags (`[DONE]`, `[FAIL]`, `[WARN]`) everywhere:
code, docs, configs, hook output, agent output.

The project should include an emoji scanner in both the stop hook and CI pipeline
to catch emoji before they cause runtime failures.

### CLAUDE.md Bloat

As a project grows, CLAUDE.md tends to accumulate rules for every edge case.
Past ~120 lines, the file becomes counterproductive: Claude spends tokens reading
rarely-relevant rules, and the important invariants get lost in the noise.

**Solution**: Keep CLAUDE.md to ~120 lines of always-relevant content. Extract
mode-specific rules (autonomous mode, exit protocol) to separate files that hooks
load conditionally:

```
CLAUDE.md                           # Always loaded (~120 lines)
docs/workflow/autonomous.md          # Loaded by SessionStart when mode=autonomous
docs/workflow/exit-protocol.md       # Loaded by stop hook on demand
```

### Ephemeral Tool Confusion

Claude Code's `TaskCreate`/`TaskUpdate`/`TaskList` tools are session-only in-memory
tools. They do NOT modify TASKS.md. Using them feels like tracking tasks but produces
zero persistent artifacts. Always edit TASKS.md directly for task state that must
survive the session.

### The Overcorrection Trap

When something breaks, the instinct is to rewrite the entire approach. A hook fails?
"Never use prompt hooks." A test flakes? "Rewrite the test framework."

The correct response is almost always minimal: read the error, check the contract,
fix the delta. Solutions should be proportional to problems. The fix for a schema
mismatch is correcting one field name, not building a 93-line workaround.

### Documentation Before Self-Assessment

Before analyzing whether work is complete, first write the PROGRESS.md entry. The
entry forces you to enumerate deliverables and verify they exist on disk. Without
the entry, self-assessment is just conversation that dies with the session.

**Overclaim detection checklist**:
1. Does the artifact exist on disk? (check with `ls`)
2. Is it referenced by the system that allegedly uses it? (check with `grep`)
3. Has it been tested end-to-end?
4. If it's "infrastructure for future use" -- say so. Don't claim it's done.

---

## Appendix: Quick Reference

### File Structure

```
your-project/
  CLAUDE.md              # Project rules
  TASKS.md               # Task backlog
  PROGRESS.md            # Session log
  LESSONS.md             # Knowledge base
  .claude/
    settings.json        # Hook wiring
    settings.local.json  # Permission allowlist
    hooks/               # Hook scripts
    agents/              # Agent definitions
    skills/              # Skill definitions
  docs/
    workflow/            # Extended documentation
    human_input/         # Input collection specs
  scripts/
    autonomous_run.sh    # Orchestrator
```

### Common Commands

| Command | Purpose |
|---------|---------|
| `/sanity-check` | Run all exit gate checks without stopping |
| `/review` | Code review against invariants |
| `/e2e-test` | Run full test suite with analysis |
| `/improve` | Scan for quality improvements |
| `/collect-input status` | Check human input task status |
| `bash scripts/autonomous_run.sh 10` | Run 10 autonomous sessions |

### Hook Quick Reference

| Hook | Type | File | Blocks? |
|------|------|------|---------|
| Dangerous commands | PreToolUse | `block_dangerous.py` | Yes |
| Secret guard | PreToolUse | `secret_guard.py` | Yes |
| File watch | PostToolUse | `file_watch_warn.py` | No (warning) |
| YAML validate | PostToolUse | `yaml_validate.py` | No (warning) |
| Exit evaluation | Stop (prompt) | settings.json | Yes |
| Lint check | Stop | `lint_check.py` | Yes |
| Test check | Stop | `test_check.py` | Yes |
| Session context | SessionStart | `session_context.py` | No |
