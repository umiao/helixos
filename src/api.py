"""FastAPI application for HelixOS orchestrator.

Defines the FastAPI app with lifespan handler, CORS middleware, static
file serving, and all REST API endpoints per PRD Section 10.  Delegates
business logic to TaskManager, Scheduler, ReviewPipeline, and TasksParser.

Route endpoints are split into domain modules under src/routes/.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api_helpers import (  # noqa: F401 -- re-exported for backward compat
    CONFIG_PATH,
    _project_to_response,
    _task_to_response,
)
from src.config import ProjectRegistry, load_config
from src.db import create_engine, create_session_factory, init_db
from src.env_loader import EnvLoader
from src.events import EventBus, sse_router
from src.history_writer import HistoryWriter
from src.port_registry import PortRegistry
from src.process_manager import ProcessManager
from src.process_monitor import ProcessMonitor
from src.project_settings import ProjectSettingsStore
from src.review_pipeline import ReviewPipeline
from src.scheduler import Scheduler
from src.subprocess_registry import SubprocessRegistry
from src.task_manager import TaskManager

logger = logging.getLogger(__name__)

# Defense-in-depth: set ProactorEventLoop on Windows for subprocess support.
# When running under uvicorn with --reload, uvicorn's setup_event_loop()
# overrides this policy BEFORE importing our module (sets SelectorEventLoop).
# The real fix is scripts/run_server.py which calls uvicorn.run(loop="none").
# This policy still protects non-uvicorn usage (pytest, direct import).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Route module imports (no circular dependency -- routes import from api_helpers)
from src.routes import (  # noqa: E402
    dashboard_router,
    execution_router,
    projects_router,
    reviews_router,
    tasks_router,
)

# Backward-compatible aggregate router for tests that do
# ``from src.api import api_router``.
api_router = APIRouter()
api_router.include_router(projects_router)
api_router.include_router(tasks_router)
api_router.include_router(execution_router)
api_router.include_router(reviews_router)
api_router.include_router(dashboard_router)


# ------------------------------------------------------------------
# Startup helpers
# ------------------------------------------------------------------


async def _reset_zombie_plan_status(task_manager: TaskManager) -> int:
    """Reset tasks stuck with plan_status='generating' to 'failed'.

    Called at startup to clean up zombies from a previous crash.
    Uses set_plan_state for transition validation and invariant enforcement.
    Returns the number of tasks reset.
    """
    count = 0
    all_tasks = await task_manager.list_tasks()
    for t in all_tasks:
        if t.plan_status == "generating":
            await task_manager.set_plan_state(t.id, "failed")
            count += 1
    return count


# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: init services on startup, cleanup on shutdown.

    Startup:
        1. Load config from orchestrator_config.yaml
        2. Create DB engine and init tables
        3. Create all service objects (TaskManager, Registry, EnvLoader, etc.)
        4. Run startup_recovery
        5. Start scheduler tick loop

    Shutdown:
        1. Stop scheduler
        2. Dispose DB engine
    """
    # Load config
    config = load_config(CONFIG_PATH)

    # Ensure data directories exist (e.g. ~/.helixos/ for state.db and .env)
    config.orchestrator.state_db_path.parent.mkdir(parents=True, exist_ok=True)
    config.orchestrator.unified_env_path.parent.mkdir(parents=True, exist_ok=True)

    # Database
    engine = create_engine(config.orchestrator.state_db_path)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    # Services
    task_manager = TaskManager(session_factory)
    registry = ProjectRegistry(config)
    env_loader = EnvLoader(config.orchestrator.unified_env_path)
    event_bus = EventBus()
    history_writer = HistoryWriter(session_factory)

    # Port registry
    ports_path = config.orchestrator.state_db_path.parent / "ports.json"
    port_registry = PortRegistry(config.orchestrator.port_ranges, ports_path)

    # Subprocess registry (shared limit across Scheduler + ProcessManager)
    subprocess_registry = SubprocessRegistry(
        max_total=config.orchestrator.max_total_subprocesses,
    )

    # Project settings store (execution_paused persistence)
    settings_store = ProjectSettingsStore(session_factory)

    # Scheduler
    scheduler = Scheduler(
        config=config,
        task_manager=task_manager,
        registry=registry,
        env_loader=env_loader,
        event_bus=event_bus,
        history_writer=history_writer,
        settings_store=settings_store,
    )

    # Process manager (dev server lifecycle)
    process_manager = ProcessManager(
        config=config,
        registry=registry,
        port_registry=port_registry,
        subprocess_registry=subprocess_registry,
        event_bus=event_bus,
    )

    # Claude CLI check -- verify claude is available before creating pipeline
    review_pipeline: ReviewPipeline | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await proc.communicate()
        if proc.returncode == 0:
            claude_version = stdout_bytes.decode("utf-8").strip()
            logger.info("Claude CLI available: %s", claude_version)
            review_pipeline = ReviewPipeline(
                config=config.review_pipeline,
                threshold=config.orchestrator.review_consensus_threshold,
                history_writer=history_writer,
                stream_log_dir=config.orchestrator.stream_log_dir,
            )
        else:
            logger.warning(
                "Claude CLI exited with code %d -- review pipeline disabled",
                proc.returncode,
            )
    except NotImplementedError:
        logger.warning(
            "asyncio.create_subprocess_exec raised NotImplementedError -- "
            "this typically means uvicorn is using SelectorEventLoop on Windows. "
            "Use 'python scripts/run_server.py' to start with the correct "
            "event loop policy. Review pipeline disabled."
        )
    except (FileNotFoundError, OSError):
        logger.warning(
            "Claude CLI not found in PATH -- review pipeline disabled"
        )

    # Startup recovery
    recovered = await scheduler.startup_recovery()
    if recovered > 0:
        logger.info("Recovered %d orphaned tasks", recovered)

    # AC6: Reset zombie plan_status="generating" to "failed" on startup
    zombie_count = await _reset_zombie_plan_status(task_manager)
    if zombie_count > 0:
        logger.info("Reset %d zombie plan_status=generating tasks to failed", zombie_count)

    # Orphan cleanup for subprocesses and ports
    subprocess_orphans = subprocess_registry.cleanup_dead()
    if subprocess_orphans:
        logger.info("Cleaned up %d orphaned subprocesses", len(subprocess_orphans))
    port_orphans = port_registry.cleanup_orphans()
    if port_orphans:
        logger.info("Cleaned up %d orphaned port assignments", len(port_orphans))
    pm_orphans = process_manager.cleanup_orphans()
    if pm_orphans:
        logger.info("Cleaned up %d orphaned dev servers", len(pm_orphans))

    # Purge old execution_logs and review_history entries
    purge_counts = await history_writer.purge_old_entries(
        retention_days=config.orchestrator.log_retention_days,
    )
    if purge_counts["execution_logs"] or purge_counts["review_history"]:
        logger.info(
            "Purged %d execution logs + %d review history entries (retention=%dd)",
            purge_counts["execution_logs"],
            purge_counts["review_history"],
            config.orchestrator.log_retention_days,
        )

    # Clean up stale 0-byte log files from previous runs
    from src.executors.code_executor import cleanup_empty_log_files

    empty_removed = cleanup_empty_log_files(config.orchestrator.stream_log_dir)
    if empty_removed > 0:
        logger.info("Removed %d empty log files from %s", empty_removed, config.orchestrator.stream_log_dir)

    # Process monitor (background failure detection)
    process_monitor = ProcessMonitor(
        subprocess_registry=subprocess_registry,
        process_manager=process_manager,
        event_bus=event_bus,
    )

    # Start scheduler and process monitor
    await scheduler.start()
    await process_monitor.start()

    # Store on app.state for endpoint access
    app.state._config_path = CONFIG_PATH
    app.state.config = config
    app.state.task_manager = task_manager
    app.state.registry = registry
    app.state.env_loader = env_loader
    app.state.event_bus = event_bus
    app.state.scheduler = scheduler
    app.state.review_pipeline = review_pipeline
    app.state.history_writer = history_writer
    app.state.port_registry = port_registry
    app.state.subprocess_registry = subprocess_registry
    app.state.process_manager = process_manager
    app.state.process_monitor = process_monitor
    app.state.settings_store = settings_store
    app.state.engine = engine
    app.state.session_factory = session_factory

    logger.info("HelixOS API started")
    yield

    # Shutdown order: ProcessMonitor -> ProcessManager -> Scheduler -> DB
    await process_monitor.stop()
    await process_manager.stop_all()
    await scheduler.stop()
    await engine.dispose()
    logger.info("HelixOS API stopped")


# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="HelixOS",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS for Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # SSE router (from T-P0-9)
    app.include_router(sse_router)

    # API routes (domain-specific modules)
    app.include_router(projects_router)
    app.include_router(tasks_router)
    app.include_router(execution_router)
    app.include_router(reviews_router)
    app.include_router(dashboard_router)

    # Static mount for frontend/dist/ (after API routes so API takes priority)
    frontend_dist = Path("frontend/dist")
    if frontend_dist.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(frontend_dist), html=True),
            name="static",
        )

    return app


# Default app instance for ``uvicorn src.api:app``
app = create_app()
