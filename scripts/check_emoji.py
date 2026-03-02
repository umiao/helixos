"""Standalone emoji scanner for CI. Exits non-zero if emoji found in project files."""
import os
import re
import sys

# Regex matching common emoji ranges (mirrors .claude/hooks/lint_check.py)
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

_SCAN_EXTENSIONS = {
    ".py", ".md", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".txt",
    ".json", ".html", ".css", ".js", ".ts", ".sh", ".bat", ".ps1",
}

_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".ruff_cache", "data", ".claude",
}


def scan_emoji(root: str) -> list[str]:
    """Walk project tree and return list of 'file:line: <match>' for any emoji found."""
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _SCAN_EXTENSIONS:
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, start=1):
                        if _EMOJI_RE.search(line):
                            rel = os.path.relpath(fpath, root)
                            preview = line.rstrip()[:120]
                            hits.append(f"  {rel}:{lineno}: {preview}")
            except OSError:
                continue
    return hits


def main() -> int:
    """Run emoji scan on project root. Returns 0 if clean, 1 if emoji found."""
    # Project root is two levels up from scripts/
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = scan_emoji(root)
    if hits:
        report = "\n".join(hits[:30])
        print(f"[FAIL] Found emoji in {len(hits)} location(s):\n{report}")
        return 1
    print("[OK] No emoji found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
