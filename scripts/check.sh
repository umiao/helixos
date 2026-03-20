#!/usr/bin/env bash
set -e
echo "[CHECK] Running ruff..."
ruff check src/ tests/
echo "[CHECK] Running tests..."
python -m pytest tests/ -x -q
echo "[CHECK] All checks passed."
