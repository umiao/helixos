"""Stop hook: run lint check and emoji scan before allowing Claude to exit.

<!-- CUSTOMIZE: Update LINT_COMMAND and LINT_PATHS for your project -->
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402

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
    "\u200d"                 # zero-width joiner
    "\ufe0f"                 # variation selector-16
    "]"
)

# File extensions to scan for emoji
_SCAN_EXTENSIONS = {
    ".py", ".md", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".txt",
    ".json", ".html", ".css", ".js", ".ts", ".sh", ".bat", ".ps1",
}

# Extensions where emoji should block (code/config). Doc files only warn.
_CODE_EXTENSIONS = {
    ".py", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".json", ".html", ".css", ".js", ".ts", ".sh", ".bat", ".ps1",
}

# Directories to skip
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".ruff_cache", "data"}


def scan_emoji(root: str) -> tuple[list[str], list[str]]:
    """Walk project tree and return (code_hits, doc_hits) for any emoji found.

    code_hits: emoji in code/config files (should block)
    doc_hits: emoji in doc files like .md/.txt (warn only)
    """
    code_hits: list[str] = []
    doc_hits: list[str] = []
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
                            hit = f"  {rel}:{lineno}: {preview}"
                            if ext in _CODE_EXTENSIONS:
                                code_hits.append(hit)
                            else:
                                doc_hits.append(hit)
            except OSError:
                continue
    return code_hits, doc_hits


def main(hook_input: dict) -> None:
    """Run lint check and emoji scan, blocking exit on lint errors or emoji found."""
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
    code_hits, doc_hits = scan_emoji(project_root)
    if code_hits:
        report = "\n".join(code_hits[:20])
        print(
            f"[EMOJI GUARD] Found emoji in {len(code_hits)} code/config file(s). "
            f"Remove all emoji (use ASCII text tags like [DONE], [FAIL] instead):\n{report}",
            file=sys.stderr,
        )
        blocked = True
    if doc_hits:
        report = "\n".join(doc_hits[:10])
        print(
            f"[EMOJI GUARD] Found emoji in {len(doc_hits)} doc file(s) (warning only):\n{report}",
            file=sys.stderr,
        )

    if blocked:
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    run_hook("lint_check", main)
