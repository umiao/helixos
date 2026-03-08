"""Project management route endpoints.

Endpoints: GET /api/projects, GET /api/projects/{project_id},
GET /api/filesystem/browse, POST /api/projects/validate,
POST /api/projects/import, POST /api/projects/{project_id}/sync,
POST /api/sync-all.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from src.api_helpers import _project_to_response, _task_to_response
from src.config import ProjectRegistry, load_config
from src.config_writer import add_project_to_config, suggest_next_project_id
from src.port_registry import PortRegistry
from src.project_validator import validate_project_directory
from src.scheduler import Scheduler
from src.schemas import (
    BrowseEntry,
    BrowseResponse,
    ErrorResponse,
    ImportProjectRequest,
    ImportProjectResponse,
    ProjectDetailResponse,
    ProjectResponse,
    SyncAllResponse,
    SyncResponse,
    ValidateProjectRequest,
    ValidateProjectResponse,
)
from src.sync.tasks_parser import sync_project_tasks
from src.task_manager import TaskManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/projects")
async def list_projects(request: Request) -> list[ProjectResponse]:
    """List all registered projects."""
    registry: ProjectRegistry = request.app.state.registry
    scheduler: Scheduler = request.app.state.scheduler
    projects = registry.list_projects()
    return [
        _project_to_response(
            p,
            execution_paused=scheduler.is_project_paused(p.id),
            review_gate_enabled=scheduler.is_review_gate_enabled(p.id),
        )
        for p in projects
    ]


@router.get(
    "/api/projects/{project_id}",
    responses={404: {"model": ErrorResponse}},
)
async def get_project(project_id: str, request: Request) -> ProjectDetailResponse:
    """Get a project with its tasks."""
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager

    try:
        project = registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    tasks = await task_manager.list_tasks(project_id=project_id)

    scheduler: Scheduler = request.app.state.scheduler

    return ProjectDetailResponse(
        id=project.id,
        name=project.name,
        repo_path=str(project.repo_path) if project.repo_path else None,
        tasks_file=project.tasks_file,
        executor_type=project.executor_type,
        max_concurrency=project.max_concurrency,
        claude_md_path=str(project.claude_md_path) if project.claude_md_path else None,
        execution_paused=scheduler.is_project_paused(project_id),
        review_gate_enabled=scheduler.is_review_gate_enabled(project_id),
        is_primary=project.is_primary,
        tasks=[_task_to_response(t) for t in tasks],
    )


# ------------------------------------------------------------------
# Filesystem browse endpoint
# ------------------------------------------------------------------


@router.get(
    "/api/filesystem/browse",
    responses={400: {"model": ErrorResponse}},
)
async def browse_directory(
    path: str | None = None,
) -> BrowseResponse:
    """Browse a directory, sandboxed to the user's home directory.

    Returns subdirectories with project indicator flags (has .git, TASKS.md,
    CLAUDE.md).  Hidden directories (starting with '.') are excluded.
    The path must be within the user's home directory.
    """
    home = Path.home().resolve()
    target = Path(path).expanduser().resolve() if path else home

    # Sandbox: target must be home or a descendant of home
    try:
        target.relative_to(home)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Path is outside the home directory: {target}",
        ) from None

    if not target.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Not a directory: {target}",
        )

    # Compute parent (None if already at home)
    parent: str | None = None
    if target != home:
        parent = str(target.parent)

    entries: list[BrowseEntry] = []
    try:
        for item in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            if not item.is_dir():
                continue
            # Skip hidden directories
            if item.name.startswith("."):
                continue
            entries.append(BrowseEntry(
                name=item.name,
                path=str(item),
                is_dir=True,
                has_git=(item / ".git").is_dir(),
                has_tasks_md=(item / "TASKS.md").is_file(),
                has_claude_md=(item / "CLAUDE.md").is_file(),
            ))
    except PermissionError:
        raise HTTPException(
            status_code=400,
            detail=f"Permission denied: {target}",
        ) from None

    return BrowseResponse(
        path=str(target),
        parent=parent,
        entries=entries,
    )


# ------------------------------------------------------------------
# Project onboarding endpoints
# ------------------------------------------------------------------


@router.post(
    "/api/projects/validate",
    responses={400: {"model": ErrorResponse}},
)
async def validate_project(
    body: ValidateProjectRequest,
    request: Request,
) -> ValidateProjectResponse:
    """Validate a directory for import as a managed project."""
    config_path: Path = request.app.state._config_path  # noqa: SLF001

    directory = Path(body.path)
    if not directory.is_absolute():
        directory = directory.expanduser().resolve()

    suggested_id = suggest_next_project_id(config_path, directory.name)
    result = validate_project_directory(directory, suggested_id)

    return ValidateProjectResponse(
        valid=result.valid,
        name=result.name,
        path=result.path,
        has_git=result.has_git,
        has_tasks_md=result.has_tasks_md,
        has_claude_config=result.has_claude_config,
        suggested_id=result.suggested_id,
        warnings=result.warnings,
        limited_mode_reasons=result.limited_mode_reasons,
    )


@router.post(
    "/api/projects/import",
    responses={
        400: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def import_project(
    body: ImportProjectRequest,
    request: Request,
) -> ImportProjectResponse:
    """Import a project into the orchestrator.

    Writes to orchestrator_config.yaml via ruamel.yaml (preserving
    comments), reloads the ProjectRegistry, auto-assigns a port,
    and triggers sync if TASKS.md is present.
    """
    config_path: Path = request.app.state._config_path  # noqa: SLF001
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager
    port_registry: PortRegistry = request.app.state.port_registry

    directory = Path(body.path).expanduser().resolve()

    # Validate path
    if not directory.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path: {directory} is not a directory",
        )

    # Determine project ID
    name = body.name or directory.name
    project_id = body.project_id or suggest_next_project_id(config_path, name)

    # Check for duplicate
    try:
        registry.get_project(project_id)
        raise HTTPException(
            status_code=409,
            detail=f"Project already exists: {project_id}",
        )
    except KeyError:
        pass  # Good -- not a duplicate

    # Build project data for YAML
    project_data: dict[str, object] = {
        "name": name,
        "repo_path": str(directory),
        "executor_type": "code",
        "tasks_file": "TASKS.md",
        "max_concurrency": 1,
    }
    if body.project_type != "other":
        project_data["project_type"] = body.project_type
    if body.launch_command is not None:
        project_data["launch_command"] = body.launch_command
    if body.preferred_port is not None:
        project_data["preferred_port"] = body.preferred_port

    # Auto-set claude_md_path if CLAUDE.md exists in the project directory
    claude_md_file = directory / "CLAUDE.md"
    if claude_md_file.is_file():
        project_data["claude_md_path"] = str(claude_md_file)

    # Write to YAML (atomic, comment-preserving)
    try:
        add_project_to_config(config_path, project_id, project_data)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    # Reload config and registry
    new_config = load_config(config_path)
    request.app.state.config = new_config
    new_registry = ProjectRegistry(new_config)
    request.app.state.registry = new_registry

    # Auto-assign port
    assigned_port: int | None = None
    try:
        assigned_port = port_registry.assign_port(
            project_id,
            body.project_type,
            preferred_port=body.preferred_port,
        )
    except (ValueError, RuntimeError):
        logger.warning(
            "Could not assign port for project %s", project_id, exc_info=True,
        )

    warnings: list[str] = []
    has_tasks_md = (directory / "TASKS.md").is_file()

    if not (directory / ".git").is_dir():
        warnings.append("No .git directory -- git operations will not work")
    if not has_tasks_md:
        warnings.append("No TASKS.md -- task sync skipped")
    if not (directory / "CLAUDE.md").is_file():
        warnings.append("No CLAUDE.md -- Claude context unavailable")

    # Auto-sync if TASKS.md present
    sync_result_resp = None
    synced = False
    if has_tasks_md:
        try:
            sync_result = await sync_project_tasks(
                project_id, task_manager, new_registry,
            )
            sync_result_resp = SyncResponse(
                project_id=project_id,
                added=sync_result.added,
                updated=sync_result.updated,
                unchanged=sync_result.unchanged,
                skipped=sync_result.skipped,
                warnings=sync_result.warnings,
            )
            synced = True
        except Exception:
            logger.warning(
                "Auto-sync failed for project %s", project_id, exc_info=True,
            )
            warnings.append("Auto-sync failed -- run sync manually")

    return ImportProjectResponse(
        project_id=project_id,
        name=name,
        repo_path=str(directory),
        port=assigned_port,
        synced=synced,
        sync_result=sync_result_resp,
        warnings=warnings,
    )


# ------------------------------------------------------------------
# Sync endpoints
# ------------------------------------------------------------------


@router.post(
    "/api/projects/{project_id}/sync",
    responses={404: {"model": ErrorResponse}},
)
async def sync_project(project_id: str, request: Request) -> SyncResponse:
    """Re-parse TASKS.md for a specific project."""
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager

    try:
        registry.get_project(project_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {project_id}",
        ) from None

    result = await sync_project_tasks(project_id, task_manager, registry)

    return SyncResponse(
        project_id=project_id,
        added=result.added,
        updated=result.updated,
        unchanged=result.unchanged,
        skipped=result.skipped,
        warnings=result.warnings,
    )


@router.post("/api/sync-all")
async def sync_all(request: Request) -> SyncAllResponse:
    """Re-parse TASKS.md for all projects."""
    registry: ProjectRegistry = request.app.state.registry
    task_manager: TaskManager = request.app.state.task_manager

    results: list[SyncResponse] = []
    for project in registry.list_projects():
        result = await sync_project_tasks(project.id, task_manager, registry)
        results.append(SyncResponse(
            project_id=project.id,
            added=result.added,
            updated=result.updated,
            unchanged=result.unchanged,
            skipped=result.skipped,
            warnings=result.warnings,
        ))

    return SyncAllResponse(results=results)
