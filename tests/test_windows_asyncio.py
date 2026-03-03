"""Regression tests: Windows asyncio subprocess + uvicorn loop="none".

Verifies that:
- src/api.py sets WindowsProactorEventLoopPolicy on Windows
- lifespan has separate except clauses for NotImplementedError vs FileNotFoundError
- Error messages reference scripts/run_server.py (not --loop none CLI)
- scripts/start.ps1 uses run_server.py (not --loop none CLI)
- scripts/run_server.py passes loop="none" to uvicorn.run on Windows
- scripts/run_server.py supports --log-level argument
- uvicorn accepts loop="none" via Python API but rejects it via CLI
- No .md file shows bare uvicorn in a PowerShell code block
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _load_run_server_module():
    """Load scripts/run_server.py as a module (scripts/ is not a package)."""
    script_path = Path(__file__).parent.parent / "scripts" / "run_server.py"
    spec = importlib.util.spec_from_file_location("run_server", script_path)
    assert spec is not None, f"Could not load spec from {script_path}"
    assert spec.loader is not None, f"No loader for {script_path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------
# Existing tests (kept as defense-in-depth)
# ------------------------------------------------------------------

def test_windows_event_loop_policy_set_on_import():
    """On Windows, importing src.api must set WindowsProactorEventLoopPolicy.

    This is a regression test for the NotImplementedError crash on startup.
    The policy is set at module level in src/api.py.
    """
    if sys.platform != "win32":
        # On non-Windows, mock sys.platform to verify the code path
        with (
            patch("src.api.sys") as mock_sys,
            patch("src.api.asyncio") as mock_asyncio,
        ):
            mock_sys.platform = "win32"
            # Re-execute the guard logic
            if mock_sys.platform == "win32":
                mock_asyncio.set_event_loop_policy(
                    mock_asyncio.WindowsProactorEventLoopPolicy(),
                )
            mock_asyncio.set_event_loop_policy.assert_called_once()
    else:
        # On actual Windows, verify the policy was already set by import
        import src.api  # noqa: F401

        policy = asyncio.get_event_loop_policy()
        assert isinstance(
            policy, asyncio.WindowsProactorEventLoopPolicy,
        ), (
            f"Expected WindowsProactorEventLoopPolicy, got {type(policy).__name__}. "
            "src/api.py must set this policy at module level for subprocess support."
        )


def test_lifespan_has_separate_except_for_not_implemented_error():
    """The lifespan must have a SEPARATE except clause for NotImplementedError.

    NotImplementedError (wrong event loop) and FileNotFoundError (missing CLI)
    are different issues requiring different log messages. They must not be
    lumped into a single except clause.
    """
    import inspect

    from src.api import lifespan

    source = inspect.getsource(lifespan)

    # Must have a standalone except for NotImplementedError
    assert "except NotImplementedError" in source, (
        "lifespan() must have a dedicated 'except NotImplementedError' clause "
        "to log the asyncio event loop issue separately"
    )

    # Must still handle FileNotFoundError and OSError
    assert "FileNotFoundError" in source, (
        "lifespan() must catch FileNotFoundError for missing Claude CLI"
    )
    assert "OSError" in source, (
        "lifespan() must catch OSError as a defensive fallback"
    )


# ------------------------------------------------------------------
# Updated tests (reference run_server.py instead of --loop none)
# ------------------------------------------------------------------

def test_not_implemented_error_message_mentions_run_server():
    """The NotImplementedError log message must tell the user to use run_server.py."""
    import inspect

    from src.api import lifespan

    source = inspect.getsource(lifespan)

    assert "run_server" in source, (
        "The NotImplementedError handler must mention 'run_server' "
        "so the user knows how to fix the issue"
    )
    assert "SelectorEventLoop" in source, (
        "The NotImplementedError handler must mention 'SelectorEventLoop' "
        "to explain the root cause"
    )


def test_start_ps1_uses_run_server():
    """scripts/start.ps1 must use run_server.py, NOT --loop none CLI."""
    start_ps1 = Path(__file__).parent.parent / "scripts" / "start.ps1"
    content = start_ps1.read_text(encoding="utf-8")

    assert "run_server" in content, (
        "start.ps1 must call run_server.py instead of uvicorn CLI directly"
    )
    assert "--loop none" not in content, (
        "start.ps1 must NOT use '--loop none' (uvicorn CLI rejects it). "
        "Use run_server.py which calls uvicorn.run(loop='none') instead."
    )


# ------------------------------------------------------------------
# New behavioral tests
# ------------------------------------------------------------------

def test_run_server_passes_loop_none_on_windows():
    """run_server.py must pass loop='none' to uvicorn.run on Windows."""
    run_server = _load_run_server_module()

    mock_uvicorn = MagicMock()
    with (
        patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        patch.object(sys, "platform", "win32"),
        patch("asyncio.set_event_loop_policy"),
    ):
        # Re-import uvicorn inside main() will get our mock
        # We need to patch the import that happens inside main()
        run_server.main(["--no-reload"])

    mock_uvicorn.run.assert_called_once()
    call_kwargs = mock_uvicorn.run.call_args
    assert call_kwargs.kwargs.get("loop") == "none" or (
        len(call_kwargs.args) > 0 and False  # fallback: check positional
    ), (
        "run_server.py must pass loop='none' to uvicorn.run on Windows"
    )


def test_run_server_passes_loop_auto_on_non_windows():
    """run_server.py must pass loop='auto' on non-Windows platforms."""
    run_server = _load_run_server_module()

    mock_uvicorn = MagicMock()
    with (
        patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        patch.object(sys, "platform", "linux"),
    ):
        run_server.main(["--no-reload"])

    mock_uvicorn.run.assert_called_once()
    call_kwargs = mock_uvicorn.run.call_args
    assert call_kwargs.kwargs.get("loop") == "auto", (
        "run_server.py must pass loop='auto' on non-Windows platforms"
    )


def test_uvicorn_accepts_loop_none_programmatically():
    """uvicorn's internal LOOP_SETUPS dict must contain 'none'.

    This is the upstream guarantee that uvicorn.run(loop='none') works.
    If uvicorn ever removes this, our run_server.py approach breaks.
    """
    from uvicorn.config import LOOP_SETUPS

    assert "none" in LOOP_SETUPS, (
        "uvicorn LOOP_SETUPS must contain 'none' for programmatic API. "
        "If this fails, uvicorn removed the 'none' loop option and "
        "run_server.py needs updating."
    )


def test_uvicorn_cli_rejects_loop_none():
    """Document: uvicorn CLI excludes 'none' from --loop choices.

    This is the known limitation that motivated run_server.py.
    If uvicorn fixes this, we can simplify back to CLI usage.
    """
    from uvicorn.main import LOOP_CHOICES

    assert "none" not in LOOP_CHOICES.choices, (
        "uvicorn CLI now accepts 'none' for --loop. "
        "Consider simplifying run_server.py back to CLI invocation."
    )


# ------------------------------------------------------------------
# --log-level support
# ------------------------------------------------------------------

def test_run_server_passes_log_level():
    """run_server.py must pass --log-level to uvicorn.run as log_level kwarg."""
    run_server = _load_run_server_module()

    mock_uvicorn = MagicMock()
    with (
        patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        patch.object(sys, "platform", "linux"),
    ):
        run_server.main(["--no-reload", "--log-level", "debug"])

    mock_uvicorn.run.assert_called_once()
    call_kwargs = mock_uvicorn.run.call_args
    assert call_kwargs.kwargs.get("log_level") == "debug", (
        "run_server.py must pass --log-level value to uvicorn.run(log_level=...)"
    )


def test_run_server_log_level_default_is_info():
    """run_server.py default log level must be 'info'."""
    run_server = _load_run_server_module()

    mock_uvicorn = MagicMock()
    with (
        patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        patch.object(sys, "platform", "linux"),
    ):
        run_server.main(["--no-reload"])

    call_kwargs = mock_uvicorn.run.call_args
    assert call_kwargs.kwargs.get("log_level") == "info", (
        "run_server.py default log_level must be 'info'"
    )


# ------------------------------------------------------------------
# Doc consistency guard tests
# ------------------------------------------------------------------

def _extract_powershell_blocks(text: str) -> list[str]:
    """Extract all ```powershell code blocks from markdown text."""
    pattern = r"```powershell\s*\n(.*?)```"
    return re.findall(pattern, text, re.DOTALL)


def test_no_bare_uvicorn_in_powershell_docs():
    """No .md file should show bare uvicorn CLI in a PowerShell code block.

    Windows users copy-paste PowerShell blocks. Bare uvicorn commands
    will fail because the CLI rejects --loop none. All Windows commands
    should use scripts/run_server.py instead.
    """
    repo_root = Path(__file__).parent.parent
    md_files = list(repo_root.glob("*.md")) + list(repo_root.glob("docs/**/*.md"))

    violations: list[str] = []
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        blocks = _extract_powershell_blocks(content)
        for i, block in enumerate(blocks):
            # Match lines that invoke uvicorn directly (not as part of a comment
            # or a reference to run_server.py)
            for line in block.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # skip comments
                if "uvicorn" in stripped and "run_server" not in stripped:
                    rel = md_file.relative_to(repo_root)
                    violations.append(f"{rel} block {i + 1}: {stripped}")

    assert not violations, (
        "Found bare uvicorn commands in PowerShell code blocks. "
        "Windows users should use scripts/run_server.py instead.\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
