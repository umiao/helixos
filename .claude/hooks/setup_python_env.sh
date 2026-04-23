#!/bin/bash
# SessionStart hook: ensure Anaconda Python is in PATH for all Bash tool calls.
# The Bash tool runs non-login, non-interactive shells that skip .bashrc/.bash_profile.
# CLAUDE_ENV_FILE is the only mechanism to inject env vars into the Bash tool.

if [ -n "$CLAUDE_ENV_FILE" ]; then
  echo 'export PATH="/c/Anaconda:/c/Anaconda/Scripts:/c/Anaconda/Library/bin:$PATH"' >> "$CLAUDE_ENV_FILE"
fi

exit 0
