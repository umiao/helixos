"""SQLAlchemy 2.0 async database layer for HelixOS.

Provides async engine creation, session management, and ORM table
definitions mapping to the Pydantic models in models.py.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import Float, Index, Integer, String, Text, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".helixos" / "state.db"


# ---------------------------------------------------------------------------
# ORM Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# ---------------------------------------------------------------------------
# ORM Table Models
# ---------------------------------------------------------------------------


class TaskRow(Base):
    """SQLAlchemy ORM model for tasks."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    local_task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    original_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="backlog")
    executor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="code")
    depends_on_json: Mapped[str] = mapped_column(Text, default="[]")
    review_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(default=False)
    deleted_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="idle",
    )
    review_lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_started",
    )
    plan_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="none",
    )
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    complexity: Mapped[str] = mapped_column(String(8), nullable=False, default="S")
    replan_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_epoch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_generation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    has_proposed_tasks: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_project_status", "project_id", "status"),
    )


class DependencyRow(Base):
    """SQLAlchemy ORM model for cross-project dependencies."""

    __tablename__ = "dependencies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    upstream_task: Mapped[str] = mapped_column(String(128), nullable=False)
    downstream_task: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fulfilled: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        Index("ix_deps_downstream", "downstream_task"),
    )


class ExecutionLogRow(Base):
    """Persistent execution log entries for tasks."""

    __tablename__ = "execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="executor")
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_exec_logs_task_ts", "task_id", "timestamp"),
    )


class ProjectSettingsRow(Base):
    """Per-project runtime settings (DB-backed, survives restarts)."""

    __tablename__ = "project_settings"

    project_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    execution_paused: Mapped[bool] = mapped_column(default=False)
    review_gate_enabled: Mapped[bool] = mapped_column(default=True)


class ReviewHistoryRow(Base):
    """Persistent review history entries for tasks."""

    __tablename__ = "review_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_model: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewer_focus: Mapped[str] = mapped_column(String(128), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggestions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    consensus_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    human_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    human_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    plan_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_started",
    )
    timestamp: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_turns_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_review_hist_task_ts", "task_id", "timestamp"),
    )


class UIPreferenceRow(Base):
    """Key-value storage for UI preferences (e.g., selected projects)."""

    __tablename__ = "ui_preferences"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def task_row_to_dict(row: TaskRow) -> dict:
    """Convert a TaskRow ORM object to a dict suitable for Task.model_validate."""
    return {
        "id": row.id,
        "project_id": row.project_id,
        "local_task_id": row.local_task_id,
        "title": row.title,
        "original_title": getattr(row, "original_title", None),
        "description": row.description or "",
        "status": row.status,
        "executor_type": row.executor_type,
        "depends_on": json.loads(row.depends_on_json) if row.depends_on_json else [],
        "review": json.loads(row.review_json) if row.review_json else None,
        "execution": json.loads(row.execution_json) if row.execution_json else None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
        "review_status": getattr(row, "review_status", "idle"),
        "review_lifecycle_state": getattr(row, "review_lifecycle_state", "not_started"),
        "plan_status": getattr(row, "plan_status", "none"),
        "plan_json": getattr(row, "plan_json", None),
        "complexity": getattr(row, "complexity", "S"),
        "replan_attempt": getattr(row, "replan_attempt", 0),
        "execution_epoch_id": getattr(row, "execution_epoch_id", None),
        "plan_generation_id": getattr(row, "plan_generation_id", None),
        "has_proposed_tasks": getattr(row, "has_proposed_tasks", False),
    }


def task_dict_to_row_kwargs(data: dict) -> dict:
    """Convert a Task-like dict to keyword args for TaskRow construction."""
    return {
        "id": data["id"],
        "project_id": data["project_id"],
        "local_task_id": data["local_task_id"],
        "title": data["title"],
        "original_title": data.get("original_title"),
        "description": data.get("description", ""),
        "status": data["status"] if isinstance(data["status"], str) else data["status"].value,
        "executor_type": (
            data["executor_type"]
            if isinstance(data["executor_type"], str)
            else data["executor_type"].value
        ),
        "depends_on_json": json.dumps(data.get("depends_on", [])),
        "review_json": (
            json.dumps(data["review"]) if data.get("review") is not None else None
        ),
        "execution_json": (
            json.dumps(data["execution"]) if data.get("execution") is not None else None
        ),
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "completed_at": data.get("completed_at"),
        "review_status": data.get("review_status", "idle"),
        "review_lifecycle_state": data.get("review_lifecycle_state", "not_started"),
        "plan_status": data.get("plan_status", "none"),
        "plan_json": data.get("plan_json"),
        "complexity": data.get("complexity", "S"),
        "replan_attempt": data.get("replan_attempt", 0),
        "execution_epoch_id": data.get("execution_epoch_id"),
        "plan_generation_id": data.get("plan_generation_id"),
        "has_proposed_tasks": data.get("has_proposed_tasks", False),
    }


# ---------------------------------------------------------------------------
# Engine + Session
# ---------------------------------------------------------------------------


def _make_url(db_path: Path) -> str:
    """Build an aiosqlite connection URL from a file path."""
    return f"sqlite+aiosqlite:///{db_path}"


def create_engine(db_path: Path = DEFAULT_DB_PATH):
    """Create an async SQLAlchemy engine for the given SQLite path."""
    url = _make_url(db_path)
    return create_async_engine(url, echo=False)


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to *engine*."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(engine) -> None:
    """Create all tables and add any missing columns to existing tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_missing_columns)
        # Backfill original_title for existing rows
        await conn.execute(
            text("UPDATE tasks SET original_title = title WHERE original_title IS NULL")
        )
    logger.info("Database tables initialized")


def _migrate_missing_columns(connection) -> None:
    """Add columns that exist in ORM models but not in the DB schema.

    SQLAlchemy's create_all() only creates missing tables, not missing
    columns.  This bridges the gap for single-file SQLite databases
    without requiring a full migration framework like alembic.
    """
    inspector = sa_inspect(connection)

    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue  # table will be created by create_all

        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}

        for column in table.columns:
            if column.name in existing_cols:
                continue

            # Build ALTER TABLE ADD COLUMN
            col_type = column.type.compile(dialect=connection.dialect)
            nullable = "NULL" if column.nullable else "NOT NULL"
            default = ""
            if column.default is not None:
                val = column.default.arg
                if isinstance(val, bool):
                    default = f" DEFAULT {1 if val else 0}"
                elif isinstance(val, (int, float)):
                    default = f" DEFAULT {val}"
                elif isinstance(val, str):
                    default = f" DEFAULT '{val}'"

            sql = (
                f"ALTER TABLE {table.name} "
                f"ADD COLUMN {column.name} {col_type} {nullable}{default}"
            )
            connection.execute(text(sql))
            logger.info(
                "Migrated: ALTER TABLE %s ADD COLUMN %s", table.name, column.name
            )


@asynccontextmanager
async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Async context manager yielding an AsyncSession.

    Commits on clean exit, rolls back on exception.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# UI Preferences helpers
# ---------------------------------------------------------------------------


async def get_preference(
    session: AsyncSession,
    key: str,
) -> str | None:
    """Retrieve a UI preference by key. Returns None if not found."""
    from sqlalchemy import select
    stmt = select(UIPreferenceRow).where(UIPreferenceRow.key == key)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row.value if row else None


async def set_preference(
    session: AsyncSession,
    key: str,
    value: str,
) -> None:
    """Set a UI preference. Creates or updates the key."""
    from datetime import datetime, timezone
    from sqlalchemy import select

    stmt = select(UIPreferenceRow).where(UIPreferenceRow.key == key)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()

    timestamp = datetime.now(timezone.utc).isoformat()

    if row:
        row.value = value
        row.updated_at = timestamp
    else:
        new_row = UIPreferenceRow(key=key, value=value, updated_at=timestamp)
        session.add(new_row)
