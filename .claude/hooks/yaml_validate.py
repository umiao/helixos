"""PostToolUse hook: validate YAML files after write."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hook_utils import run_hook  # noqa: E402

try:
    import yaml  # type: ignore[import-untyped]

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def main(hook_input: dict) -> None:
    """Parse YAML files after write to catch syntax errors early."""
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Only check YAML files
    if not file_path.endswith((".yaml", ".yml")):
        sys.exit(0)

    if not HAS_YAML:
        print(
            "[YAML VALIDATOR] PyYAML not installed, skipping validation.",
            file=sys.stderr,
        )
        sys.exit(0)

    try:
        with open(file_path, encoding="utf-8") as f:
            yaml.safe_load(f)
    except FileNotFoundError:
        # File may not exist yet during Edit operations
        sys.exit(0)
    except yaml.YAMLError as e:
        print(
            f"[YAML VALIDATOR] Invalid YAML in {file_path}:\n{e}",
            file=sys.stderr,
        )
        # Non-blocking warning -- don't prevent the edit, just warn
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    run_hook("yaml_validate", main)
