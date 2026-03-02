"""Stop hook: run tests before allowing Claude to exit.

<!-- CUSTOMIZE: Update TEST_COMMAND and TEST_PATHS for your project -->
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import check_stop_cache, run_hook, write_stop_cache  # noqa: E402

# <!-- CUSTOMIZE: Set your test command and paths -->
TEST_COMMAND = ["python", "-m", "pytest"]
TEST_PATHS = ["tests/"]
TEST_FLAGS = ["-x", "-q", "--tb=short"]


def main(hook_input: dict) -> None:
    """Run tests on the test suite, blocking exit if tests fail."""
    # --- Cache check: skip if no files changed since last pass ---
    if check_stop_cache("test"):
        print("[TEST GUARD] No files changed since last pass -- skipping (cached PASS)", file=sys.stderr)
        sys.exit(0)

    try:
        result = subprocess.run(
            TEST_COMMAND + TEST_PATHS + TEST_FLAGS,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("[TEST GUARD] Tests timed out after 120s", file=sys.stderr)
        sys.exit(2)

    if result.returncode not in (0, 5):  # 0 = pass, 5 = no tests collected
        # Show last 30 lines of output to keep it concise
        output_lines = (result.stdout + result.stderr).strip().splitlines()
        summary = "\n".join(output_lines[-30:])
        print(
            f"[TEST GUARD] Tests failed. Fix them before stopping:\n{summary}",
            file=sys.stderr,
        )
        sys.exit(2)  # exit 2 = block exit

    # All tests passed -- write cache
    write_stop_cache("test")
    sys.exit(0)


if __name__ == "__main__":
    run_hook("test_check", main)
