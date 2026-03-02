"""Stop hook: run lint check and emoji scan before allowing Claude to exit.

<!-- CUSTOMIZE: Update LINT_COMMAND and LINT_PATHS for your project -->
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import check_stop_cache, run_hook, write_stop_cache  # noqa: E402

# <!-- CUSTOMIZE: Set your lint command and paths -->
LINT_COMMAND = ["ruff", "check"]
LINT_PATHS = ["src/", "tests/"]

# Regex matching common emoji ranges (emoticons, symbols, dingbats, transport, misc, flags, etc.)
_EMOJI_RE = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # misc symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f900-\U0001f9ff"  # supplemental symbols
    "\U0001fa00-\U0001fa6f"  # chess symbols
    "\U0001fa70-\U0001faff"  # symbols extended-A
    "\u2600-\u26ff"          # misc symbols
    "\u2700-\u27bf"          # dingbats
    "\u200d"                 # zero-width joiner
    "\ufe0f"                 # variation selector-16
    "]"
)

# File extensions to scan for emoji
_SCAN_EXTENSIONS = {
    ".py", ".md", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".txt",
    ".json", ".html", ".css", ".js", ".ts", ".sh", ".bat", ".ps1",
}

# Directories to skip
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".ruff_cache", "data"}


def scan_emoji(root: str) -> list[str]:
    """Walk project tree and return list of 'file:line: <match>' for any emoji found."""
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories in-place
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _SCAN_EXTENSIONS:
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, start=1):
                        matches = _EMOJI_RE.findall(line)
                        if matches:
                            rel = os.path.relpath(fpath, root)
                            preview = line.rstrip()[:120]
                            hits.append(f"  {rel}:{lineno}: {preview}")
            except OSError:
                continue
    return hits


def main(hook_input: dict) -> None:
    """Run lint check and emoji scan, blocking exit on lint errors or emoji found."""
    # --- Cache check: skip if no files changed since last pass ---
    if check_stop_cache("lint"):
        print("[LINT GUARD] No files changed since last pass -- skipping (cached PASS)", file=sys.stderr)
        sys.exit(0)

    blocked = False

    # --- Lint check ---
    try:
        result = subprocess.run(
            LINT_COMMAND + LINT_PATHS,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("[LINT GUARD] Lint check timed out after 120s", file=sys.stderr)
        sys.exit(2)

    if result.returncode != 0:
        print(
            f"[LINT GUARD] Lint check found issues. Fix them before stopping:\n{result.stdout}",
            file=sys.stderr,
        )
        blocked = True

    # --- Emoji scan ---
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    emoji_hits = scan_emoji(project_root)
    if emoji_hits:
        report = "\n".join(emoji_hits[:20])  # cap at 20 to keep output readable
        count = len(emoji_hits)
        print(
            f"[EMOJI GUARD] Found emoji in {count} location(s). "
            f"Remove all emoji (use ASCII text tags like [DONE], [FAIL] instead):\n{report}",
            file=sys.stderr,
        )
        blocked = True

    if blocked:
        sys.exit(2)

    # All checks passed -- write cache
    write_stop_cache("lint")
    sys.exit(0)


if __name__ == "__main__":
    run_hook("lint_check", main)
