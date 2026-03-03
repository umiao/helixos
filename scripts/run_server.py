"""Launch uvicorn with correct Windows asyncio settings.

Uses uvicorn.run() instead of CLI because uvicorn 0.27.0 CLI excludes
'none' from --loop choices, while the Python API accepts it.

Usage:
    python scripts/run_server.py
    python scripts/run_server.py --host 0.0.0.0 --port 9000 --no-reload
"""

from __future__ import annotations

import argparse
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the server launcher."""
    parser = argparse.ArgumentParser(
        description="Launch the HelixOS uvicorn server",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000)",
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable auto-reload (default: reload enabled)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point: configure event loop policy, then call uvicorn.run()."""
    args = parse_args(argv)

    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    import uvicorn

    uvicorn.run(
        "src.api:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        loop="none" if sys.platform == "win32" else "auto",
    )


if __name__ == "__main__":
    main()
