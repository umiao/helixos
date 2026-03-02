# Claude Code Project Template

A production-ready Claude Code project template with self-enforcing quality gates,
autonomous multi-session workflows, and persistent state management.

---

## Design Philosophy

The template is built on five pillars that separate concerns and ensure consistency
across sessions:

| Pillar | File / Directory | Role |
|--------|-----------------|------|
| **Rules** | `CLAUDE.md` | Stable constraints and coding standards |
| **Tasks** | `TASKS.md` | Prioritized work backlog (single source of truth) |
| **Journal** | `PROGRESS.md` | Append-only session log |
| **Knowledge** | `LESSONS.md` | Curated post-mortems and patterns |
| **Guardrails** | `.claude/hooks/` | Automated enforcement at every lifecycle boundary |

**Core insight:** instructions are suggestions, hooks are enforcement.
Files are persistent, memory is ephemeral.

---

## Session Lifecycle

Every Claude Code session follows this flow. Hooks act as automated gates --
they catch violations before they persist.

```
SessionStart hook
    |
    v
[Context loaded: tasks, progress, lessons]
    |
    v
Claude works (read / edit / run)
    |  PreToolUse hooks guard each operation
    |  PostToolUse hooks validate after changes
    v
Claude tries to stop
    |
    +--[PASS: prompt + lint + test]--> Session ends
    |
    +--[FAIL]--> Claude sees error --> fixes --> retries
```

**PreToolUse hooks** fire before Bash commands, file writes, and edits:
- `block_dangerous.py` -- prevents destructive shell commands
- `secret_guard.py` -- rejects hardcoded secrets in code

**PostToolUse hooks** fire after changes land:
- `file_watch_warn.py` -- warns when critical files are modified
- `yaml_validate.py` -- validates YAML syntax
- `lint_check.py` -- runs ruff on changed Python files
- `test_check.py` -- runs pytest after code changes

**Stop hooks** enforce the exit protocol (progress logged, tasks updated, tests pass).

---

## Agent and Task System

Three layers work together: skills orchestrate, agents execute, hooks enforce.

```
User / Orchestrator
    |
    | invokes skill or gives instruction
    v
+---------------------+       +---------------------+
|   Skills            |       |   Hooks             |
|  /review            |       |  PreToolUse         |
|  /improve           |       |  PostToolUse        |
|  /sanity-check      |       |  Stop               |
|  /e2e-test          |       |  SessionStart       |
|  /collect-input     |       +----------+----------+
+----------+----------+                  |
           |                             | enforce
           | spawn                       v
+----------v----------+     +-----------+-----------+
|   Agents            |     |  State Files          |
|  reviewer           |     |  TASKS.md             |
|  test-runner        |     |  PROGRESS.md          |
|  refactor-advisor   |     |  LESSONS.md           |
|  input-reviewer     |     |  session_state.json   |
+---------------------+     |  checkpoint.json      |
                             +-----------------------+
```

**Skills** are user-facing commands that orchestrate multi-step workflows.
**Agents** run in isolated Task contexts with focused tool access.
**State files** persist across sessions so no context is lost.

---

## Autonomous Multi-Session Mode

The autonomous runner loops sessions until all tasks are done or failures
accumulate. Each session is self-contained and commits its own work.

```
autonomous_run.sh (loop)
    |
    v
Session N starts --> SessionStart loads state
    |
    v
Pick highest-priority unblocked task
    |
    v
Work, complete, git commit, update state
    |
    v
Session ends --> check session_state.json
    |
    +--[all_done]-----------------------> Stop
    +--[more tasks]---------------------> Session N+1
    +--[consecutive failures >= 2]------> Stop
```

See `docs/workflow/autonomous.md` for the full ruleset.

---

## Human-AI Cooperation: Structured Handoff

**Design principle:** Tasks needing human input are explicitly tagged, validated
before unblocking, and surfaced proactively -- never silently skipped.

The system uses a four-step lifecycle for any task that cannot proceed without
human-provided artifacts (fixture files, configuration preferences, credentials setup):

```
TAG                GUIDE              VALIDATE           UNBLOCK
TASKS.md adds      /collect-input     input-reviewer     /collect-input
[NEEDS-INPUT]  --> guides human   --> agent checks    --> unblock <id>
               --> through spec   --> files meet spec --> removes tag
```

**In autonomous mode**, the orchestrator skips NEEDS-INPUT tasks and records
them in `session_state.json` under `skipped_tasks`. The SessionStart hook
surfaces them in an `[INPUT]` line so neither Claude nor the user loses track.

**Across multiple projects**, each project's `session_state.json` records its
own NEEDS-INPUT skips. Aggregate them to triage human effort across projects:

```bash
# Show all NEEDS-INPUT skips across projects
for f in ~/projects/*/.claude/session_state.json; do
  python3 -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); \
    [print(f'{sys.argv[1]}: {s[\"task\"]} -- {s[\"reason\"]}') \
     for s in d.get('skipped_tasks',[]) if 'NEEDS-INPUT' in s.get('reason','')]" "$f"
done
```

For full details on the NEEDS-INPUT protocol, /collect-input skill, and validation
infrastructure, see [Section 4 of the workflow guide](claude-code-workflow-guide.md#4-human-ai-cooperation-structured-handoff).

---

## File Map

| Path | Purpose |
|------|---------|
| `CLAUDE.md` | Project rules, constraints, session workflow |
| `TASKS.md` | Task backlog with priorities and dependencies |
| `PROGRESS.md` | Append-only session log |
| `LESSONS.md` | Post-mortems and effective patterns |
| `claude-code-workflow-guide.md` | Comprehensive workflow design guide |
| `.claude/settings.json` | Hook wiring and lifecycle configuration |
| `.claude/settings.local.json` | Permission allowlist for autonomous mode |
| `.claude/hooks/` | Python hook scripts (9 hooks + template) |
| `.claude/agents/` | Agent definitions (reviewer, test-runner, refactor-advisor, input-reviewer) |
| `.claude/skills/` | Skill definitions (review, improve, sanity-check, e2e-test, collect-input) |
| `scripts/` | Utility scripts including autonomous runner |
| `src/` | Source code |
| `tests/` | Test files |
| `docs/` | Extended documentation and workflow specs |
| `pyproject.toml` | Python project configuration (ruff, mypy, pytest) |

---

## Quick Start

See `scripts/QUICKSTART.md` for the full setup guide. The essentials:

```bash
# 1. Clone and install
git clone <this-repo> my-project && cd my-project
pip install -r requirements.txt

# 2. Customize for your project
#    Edit CLAUDE.md -- set your project overview, tech stack, and invariants
#    Edit TASKS.md  -- replace example tasks with your backlog

# 3. Run interactively
claude

# 4. Run autonomously (loops until tasks are done)
bash scripts/autonomous_run.sh
```

---

## Full Guide

For deep details on the design philosophy, hook architecture, agent system,
and workflow patterns, see [`claude-code-workflow-guide.md`](claude-code-workflow-guide.md).
