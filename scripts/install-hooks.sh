#!/usr/bin/env bash
# Install git hooks for the HelixOS project.
# Usage: bash scripts/install-hooks.sh

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
    echo "[ERROR] Not inside a git repository."
    exit 1
fi

HOOK_SRC="$REPO_ROOT/scripts/pre-commit"
HOOK_DST="$REPO_ROOT/.git/hooks/pre-commit"

if [ ! -f "$HOOK_SRC" ]; then
    echo "[ERROR] Hook source not found: $HOOK_SRC"
    exit 1
fi

cp "$HOOK_SRC" "$HOOK_DST"
chmod +x "$HOOK_DST"
echo "[OK] pre-commit hook installed to .git/hooks/pre-commit"
