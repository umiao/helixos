#!/bin/bash
# Autonomous task runner.
# Runs Claude Code sessions in a loop. Each session starts with a FRESH context
# and picks up state from .claude/session_state.json + SessionStart hook.
# Each completed task gets a git commit. When a session ends (context full,
# max turns, or no more tasks), a new session starts clean.
#
# Usage: bash scripts/autonomous_run.sh [max_sessions]
# Default: 5 sessions. Each session gets up to 200 agent turns.
# Ctrl+C to stop at any time. Progress is saved in PROGRESS.md and git history.

set -euo pipefail
cd "$(dirname "$0")/.."


# --- Robustness: ignore SIGPIPE, always log to a file ---
# Prevents silent death when launched via Claude Code's run_in_background:
# after an inner `claude -p` session returns, any echo to a closed stdout fd
# fires SIGPIPE, and `set -e` terminates the script. See root LESSONS.md
# [2026-04-11] for the full forensic timeline.
trap '' PIPE
mkdir -p logs
exec > >(tee -a logs/autonomous.log) 2>&1
# --- PID lockfile for concurrent run protection ---
LOCKFILE=".claude/autonomous.lock"
if [ -f "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE")" 2>/dev/null; then
  echo "[orchestrator] Another instance is running (PID $(cat "$LOCKFILE")). Exiting."
  exit 1
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

MAX_SESSIONS=${1:-5}

# Reset stale all_done at orchestrator startup (T-P1-257).
# A previous run may have legitimately drained the queue and set all_done=true,
# but new tasks may have been added since. Inner Claude sessions trust
# session_state and will no-op without re-checking task_db. So: if task_db.py
# reports unblocked work AND state has all_done=true, force all_done=false.
# If task_db is genuinely empty, leave the flag alone (loop will no-op once
# and exit, preserving existing behavior).
STATE_FILE=".claude/session_state.json"
if [ -f "$STATE_FILE" ]; then
  if python .claude/hooks/task_db.py has-unblocked > /dev/null 2>&1; then
    python -c "
import json
with open('$STATE_FILE', encoding='utf-8') as f:
    state = json.load(f)
if state.get('all_done', False):
    state['all_done'] = False
    state['note'] = 'Reset by orchestrator: task_db has unblocked work'
    with open('$STATE_FILE', 'w', encoding='utf-8') as f2:
        json.dump(state, f2, indent=2)
    print('[orchestrator] Reset stale all_done=true (task_db has unblocked tasks)')
" 2>/dev/null || true
  fi
fi

session_count=0
consecutive_failures=0
MAX_CONSECUTIVE_FAILURES=2

echo "[orchestrator] Starting autonomous run (max $MAX_SESSIONS sessions)"
echo "[orchestrator] Progress: check git log, PROGRESS.md, TASKS.md"
echo "[orchestrator] Press Ctrl+C to stop. Work is saved after each task."
echo ""

while [ $session_count -lt $MAX_SESSIONS ]; do
  session_count=$((session_count + 1))
  echo "--- Session $session_count/$MAX_SESSIONS ---"

  # Capture commit SHA before session for progress detection
  start_sha=$(git rev-parse HEAD)

  # Sync Claude Code additionalDirectories from orchestrator config
  python -c "
import sys; sys.path.insert(0, '.')
from src.settings_sync import sync_additional_directories
result = sync_additional_directories()
print(f'[orchestrator] Synced {len(result)} additional directories')
" || echo "[orchestrator] Warning: settings sync failed (non-fatal)"

  claude -p "Autonomous mode. Read TASKS.md, pick ONE highest-priority unblocked task, \
    and complete it. After completing the task: \
    1) run tests, 2) update PROGRESS.md, update tasks via task_db.py, 3) git commit \
    with message format '[T-XX-N] description', 4) update .claude/session_state.json, \
    then stop. If no unblocked tasks remain, set all_done=true in session_state.json \
    and stop." \
    --allowedTools "Read,Write,Edit,Bash,Glob,Grep,Task" \
    --max-turns 200

  exit_code=$?

  if [ $exit_code -eq 0 ]; then
    consecutive_failures=0
    # Check if all tasks done
    if python -c "
import json, sys
try:
    with open('.claude/session_state.json', encoding='utf-8') as f:
        state = json.load(f)
    if state.get('all_done', False):
        sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
      echo "[orchestrator] All tasks complete!"
      break
    fi
    echo "[orchestrator] Session ended. Continuing in next session..."
  else
    # Git stash on failed session
    git stash push -m "auto-stash: failed session $session_count" 2>/dev/null || true

    # Distinguish context exhaustion from real failure
    current_sha=$(git rev-parse HEAD)
    if [ "$current_sha" != "$start_sha" ]; then
      # New commits were made -- task is progressing (context exhaustion, not failure)
      echo "[orchestrator] Session made progress (new commits). Not counting as failure."
      consecutive_failures=0
    else
      consecutive_failures=$((consecutive_failures + 1))
      echo "[orchestrator] Session failed ($consecutive_failures/$MAX_CONSECUTIVE_FAILURES)"
      if [ $consecutive_failures -ge $MAX_CONSECUTIVE_FAILURES ]; then
        echo "[orchestrator] Too many consecutive failures. Stopping."
        break
      fi
    fi
  fi
  echo ""
done

echo ""
echo "[orchestrator] Finished after $session_count session(s)"
echo "[orchestrator] Review: git log --oneline -20"
echo "[orchestrator] Status: cat TASKS.md"
