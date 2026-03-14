"""Tests for T-P1-119: replan decision flow and execution prompt plan_json enrichment.

Part 1: Replan decision handling in review endpoint
Part 2: Execution prompt enrichment with structured plan_json data
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import (
    OrchestratorConfig,
    OrchestratorSettings,
    PlanValidationConfig,
    ProjectConfig,
)
from src.db import Base
from src.executors.code_executor import CodeExecutor, _format_plan_json_for_prompt
from src.history_writer import HistoryWriter
from src.models import LLMReview, ReviewState, TaskStatus
from src.task_manager import TaskManager
from tests.factories import make_task

SAMPLE_PLAN_JSON = json.dumps({
    "plan": "Implement feature X",
    "steps": [
        {
            "step": "Add new model field",
            "files": ["src/models.py", "src/db.py"],
        },
        {
            "step": "Update API endpoint",
            "files": ["src/routes/tasks.py"],
        },
        "Write tests",
    ],
    "acceptance_criteria": [
        "New field persists to DB",
        "API returns field in response",
        "All tests pass",
    ],
})


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def test_engine():
    """In-memory async engine for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(test_engine):
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )


@pytest.fixture
async def task_manager(session_factory):
    """Provide a TaskManager backed by the in-memory DB."""
    return TaskManager(session_factory)


@pytest.fixture
def mock_config() -> OrchestratorConfig:
    """Minimal OrchestratorConfig for tests."""
    return OrchestratorConfig(
        orchestrator=OrchestratorSettings(
            plan_validation=PlanValidationConfig(),
        ),
        projects={
            "proj-a": ProjectConfig(
                name="Test Project",
                repo_path=None,
                tasks_file="TASKS.md",
            ),
        },
    )


@pytest.fixture
async def app(task_manager, session_factory, mock_config):
    """Create test FastAPI app with required app.state attributes."""
    from fastapi import FastAPI

    from src.api import api_router
    from src.events import EventBus, sse_router

    application = FastAPI()
    application.include_router(api_router)
    application.include_router(sse_router)

    # Wire up app state
    application.state.task_manager = task_manager
    application.state.session_factory = session_factory
    application.state.event_bus = EventBus()
    application.state.scheduler = MagicMock()
    application.state.scheduler.is_review_gate_enabled = MagicMock(return_value=False)
    application.state.scheduler.force_tick = AsyncMock()
    application.state.history_writer = AsyncMock(spec=HistoryWriter)
    application.state.review_pipeline = MagicMock()
    application.state.registry = MagicMock()
    application.state.config = mock_config

    # Make registry.get_project return a mock project
    mock_project = MagicMock()
    mock_project.repo_path = None
    mock_project.tasks_file = "TASKS.md"
    application.state.registry.get_project = MagicMock(return_value=mock_project)

    yield application

    # Cancel lingering background tasks to prevent unawaited coroutine warnings
    await asyncio.sleep(0)
    for t in asyncio.all_tasks():
        if t is not asyncio.current_task() and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t


@pytest.fixture
async def client(app):
    """AsyncClient for making test requests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ==================================================================
# Part 1: Replan decision flow
# ==================================================================


class TestReplanDecision:
    """Tests for the 'replan' decision in the review decide endpoint."""

    async def test_replan_accepted_as_valid_decision(
        self, client: AsyncClient, task_manager: TaskManager, app,
    ):
        """'replan' should be accepted as a valid decision value."""
        task = make_task(
            task_id="proj-a:T-P0-RP1",
            project_id="proj-a",
            local_task_id="T-P0-RP1",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=True), \
             patch("src.routes.reviews.generate_task_plan", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = {"plan": "new plan", "steps": [], "acceptance_criteria": []}
            resp = await client.post(
                "/api/tasks/proj-a:T-P0-RP1/review/decide",
                json={"decision": "replan", "reason": "Plan needs more detail"},
            )
        # Should not return 400 (invalid decision)
        assert resp.status_code != 400

    async def test_replan_increments_attempt(
        self, client: AsyncClient, task_manager: TaskManager, app,
    ):
        """Replan should increment replan_attempt on the task."""
        task = make_task(
            task_id="proj-a:T-P0-RP2",
            project_id="proj-a",
            local_task_id="T-P0-RP2",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
            replan_attempt=0,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=True), \
             patch("src.routes.reviews.generate_task_plan", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = {"plan": "new plan", "steps": [], "acceptance_criteria": []}
            resp = await client.post(
                "/api/tasks/proj-a:T-P0-RP2/review/decide",
                json={"decision": "replan"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["replan_attempt"] == 1

    async def test_replan_max_attempts_enforced(
        self, client: AsyncClient, task_manager: TaskManager, app,
    ):
        """Replan at max attempts should return 409."""
        from src.routes.reviews import MAX_REPLAN_ATTEMPTS

        task = make_task(
            task_id="proj-a:T-P0-RP3",
            project_id="proj-a",
            local_task_id="T-P0-RP3",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
            replan_attempt=MAX_REPLAN_ATTEMPTS,  # Already at max
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-RP3/review/decide",
            json={"decision": "replan"},
        )
        assert resp.status_code == 409
        assert "Maximum replan attempts" in resp.json()["detail"]

    async def test_replan_sets_plan_status_generating(
        self, client: AsyncClient, task_manager: TaskManager, app,
    ):
        """Replan should set plan_status to 'generating'."""
        task = make_task(
            task_id="proj-a:T-P0-RP4",
            project_id="proj-a",
            local_task_id="T-P0-RP4",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=True), \
             patch("src.routes.reviews.generate_task_plan", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = {"plan": "new plan", "steps": [], "acceptance_criteria": []}
            resp = await client.post(
                "/api/tasks/proj-a:T-P0-RP4/review/decide",
                json={"decision": "replan"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_status"] == "generating"

    async def test_replan_persists_decision_to_history(
        self, client: AsyncClient, task_manager: TaskManager, app,
    ):
        """Replan should persist the decision to review history."""
        task = make_task(
            task_id="proj-a:T-P0-RP5",
            project_id="proj-a",
            local_task_id="T-P0-RP5",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=True), \
             patch("src.routes.reviews.generate_task_plan", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = {"plan": "new plan", "steps": [], "acceptance_criteria": []}
            await client.post(
                "/api/tasks/proj-a:T-P0-RP5/review/decide",
                json={"decision": "replan", "reason": "Need better ACs"},
            )

        # Verify history_writer was called
        hw = app.state.history_writer
        hw.write_review_decision.assert_called_once_with(
            "proj-a:T-P0-RP5", "replan", reason="Need better ACs",
        )

    async def test_replan_requires_sdk_available(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """Replan should return 503 when Claude SDK is not available."""
        task = make_task(
            task_id="proj-a:T-P0-RP6",
            project_id="proj-a",
            local_task_id="T-P0-RP6",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=False):
            resp = await client.post(
                "/api/tasks/proj-a:T-P0-RP6/review/decide",
                json={"decision": "replan"},
            )
        assert resp.status_code == 503

    async def test_approve_decision_moves_to_queued(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """approve should move task to QUEUED."""
        task = make_task(
            task_id="proj-a:T-P0-EXapprove",
            project_id="proj-a",
            local_task_id="T-P0-EXapprove",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-EXapprove/review/decide",
            json={"decision": "approve", "reason": "test"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    async def test_reject_decision_triggers_replan(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """reject should trigger replan (not move to BACKLOG)."""
        task = make_task(
            task_id="proj-a:T-P0-EXreject",
            project_id="proj-a",
            local_task_id="T-P0-EXreject",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        with patch("src.routes.reviews.is_claude_cli_available", return_value=True), \
             patch("src.routes.reviews.generate_task_plan", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = {"plan": "new plan", "steps": [], "acceptance_criteria": []}
            resp = await client.post(
                "/api/tasks/proj-a:T-P0-EXreject/review/decide",
                json={"decision": "reject", "reason": "test"},
            )
        assert resp.status_code == 200
        # reject now triggers replan; task stays in REVIEW_NEEDS_HUMAN
        assert resp.json()["status"] == "review_needs_human"
        assert resp.json()["plan_status"] == "generating"

    async def test_invalid_decision_returns_400(
        self, client: AsyncClient, task_manager: TaskManager,
    ):
        """Invalid decision value should return 400."""
        task = make_task(
            task_id="proj-a:T-P0-INV1",
            project_id="proj-a",
            local_task_id="T-P0-INV1",
            status=TaskStatus.REVIEW_NEEDS_HUMAN,
        )
        task = task.model_copy(update={
            "review": ReviewState(
                rounds_total=1, rounds_completed=1,
                consensus_score=0.5, human_decision_needed=True,
            ),
        })
        await task_manager.create_task(task)

        resp = await client.post(
            "/api/tasks/proj-a:T-P0-INV1/review/decide",
            json={"decision": "invalid_value"},
        )
        assert resp.status_code == 400


# ==================================================================
# Part 2: generate_task_plan review_feedback injection
# ==================================================================


class TestGenerateTaskPlanReviewFeedback:
    """Tests for review_feedback parameter in generate_task_plan."""

    @pytest.mark.asyncio
    async def test_review_feedback_injected_into_prompt(self):
        """When review_feedback is provided, it should appear in the prompt."""
        from src.enrichment import generate_task_plan

        feedback = "The plan lacks error handling details."
        captured_prompt = None

        async def mock_call_plan_sdk(
            prompt, options, *args, **kwargs
        ):
            nonlocal captured_prompt
            captured_prompt = prompt
            return (
                json.dumps({
                    "plan": "test",
                    "steps": [{"step": "step 1", "files": []}],
                    "acceptance_criteria": ["AC1"],
                }),
                [],
            )

        with patch("src.enrichment._call_plan_sdk", side_effect=mock_call_plan_sdk), \
             patch("src.enrichment.get_session_context", return_value=""):
            await generate_task_plan(
                title="Test task",
                description="Some description",
                review_feedback=feedback,
            )

        assert captured_prompt is not None
        assert "Review Feedback" in captured_prompt
        assert feedback in captured_prompt

    @pytest.mark.asyncio
    async def test_no_feedback_no_injection(self):
        """When review_feedback is None, no feedback block in prompt."""
        from src.enrichment import generate_task_plan

        captured_prompt = None

        async def mock_call_plan_sdk(
            prompt, options, *args, **kwargs
        ):
            nonlocal captured_prompt
            captured_prompt = prompt
            return (
                json.dumps({
                    "plan": "test",
                    "steps": [{"step": "step 1", "files": []}],
                    "acceptance_criteria": ["AC1"],
                }),
                [],
            )

        with patch("src.enrichment._call_plan_sdk", side_effect=mock_call_plan_sdk), \
             patch("src.enrichment.get_session_context", return_value=""):
            await generate_task_plan(
                title="Test task",
                review_feedback=None,
            )

        assert captured_prompt is not None
        assert "Review Feedback" not in captured_prompt


# ==================================================================
# Part 3: Execution prompt plan_json enrichment
# ==================================================================


class TestFormatPlanJsonForPrompt:
    """Tests for _format_plan_json_for_prompt standalone function."""

    def test_none_plan_json_returns_empty(self):
        """None plan_json -> empty string."""
        assert _format_plan_json_for_prompt(None) == ""

    def test_empty_string_returns_empty(self):
        """Empty plan_json -> empty string."""
        assert _format_plan_json_for_prompt("") == ""

    def test_malformed_json_returns_empty(self):
        """Invalid JSON -> empty string (graceful fallback)."""
        assert _format_plan_json_for_prompt("{bad json}") == ""

    def test_steps_formatted_with_numbers(self):
        """Steps should be numbered in the output."""
        result = _format_plan_json_for_prompt(SAMPLE_PLAN_JSON)
        assert "## Implementation Steps" in result
        assert "1. Add new model field" in result
        assert "2. Update API endpoint" in result
        assert "3. Write tests" in result

    def test_files_included_in_steps(self):
        """Files associated with steps should appear."""
        result = _format_plan_json_for_prompt(SAMPLE_PLAN_JSON)
        assert "src/models.py" in result
        assert "src/db.py" in result
        assert "src/routes/tasks.py" in result

    def test_acceptance_criteria_formatted(self):
        """Acceptance criteria should appear as checklist."""
        result = _format_plan_json_for_prompt(SAMPLE_PLAN_JSON)
        assert "## Acceptance Criteria" in result
        assert "- [ ] New field persists to DB" in result
        assert "- [ ] API returns field in response" in result
        assert "- [ ] All tests pass" in result

    def test_no_steps_no_section(self):
        """If plan has no steps, Implementation Steps section omitted."""
        data = json.dumps({"plan": "test", "acceptance_criteria": ["AC1"]})
        result = _format_plan_json_for_prompt(data)
        assert "## Implementation Steps" not in result
        assert "## Acceptance Criteria" in result

    def test_no_criteria_no_section(self):
        """If plan has no ACs, Acceptance Criteria section omitted."""
        data = json.dumps({"plan": "test", "steps": [{"step": "step 1"}]})
        result = _format_plan_json_for_prompt(data)
        assert "## Implementation Steps" in result
        assert "## Acceptance Criteria" not in result

    def test_string_steps_work(self):
        """_format_plan_json_for_prompt should handle string steps."""
        data = {"plan": "test", "steps": ["step 1"], "acceptance_criteria": ["AC1"]}
        result = _format_plan_json_for_prompt(json.dumps(data))
        assert "step 1" in result
        assert "AC1" in result


class TestBuildPromptWithPlanJson:
    """Tests for _build_prompt integration with plan_json enrichment."""

    def test_prompt_includes_plan_steps_when_available(self):
        """_build_prompt should include plan steps from plan_json."""
        config = OrchestratorSettings()
        executor = CodeExecutor(config)
        task = make_task(
            description="Do a test thing",
            plan_json=SAMPLE_PLAN_JSON,
        )
        prompt = executor._build_prompt(task)
        assert "## Implementation Steps" in prompt
        assert "Add new model field" in prompt
        assert "## Acceptance Criteria" in prompt

    def test_prompt_works_without_plan_json(self):
        """_build_prompt should work normally when plan_json is None."""
        config = OrchestratorSettings()
        executor = CodeExecutor(config)
        task = make_task(description="Do a test thing")
        prompt = executor._build_prompt(task)
        assert "Do a test thing" in prompt
        assert "## Implementation Steps" not in prompt

    def test_prompt_works_with_malformed_plan_json(self):
        """_build_prompt should not crash with malformed plan_json."""
        config = OrchestratorSettings()
        executor = CodeExecutor(config)
        task = make_task(
            description="Do a test thing",
            plan_json="{invalid",
        )
        prompt = executor._build_prompt(task)
        # Should still have the basic content
        assert "Do a test thing" in prompt
        # Should NOT have plan sections (graceful fallback)
        assert "## Implementation Steps" not in prompt

    def test_prompt_includes_both_plan_and_review_feedback(self):
        """Both plan_json and review_feedback should appear in prompt."""
        config = OrchestratorSettings()
        executor = CodeExecutor(config)
        task = make_task(
            description="Do a test thing",
            plan_json=SAMPLE_PLAN_JSON,
        )
        feedback = "## Previous Review Feedback\nFix the tests"
        prompt = executor._build_prompt(task, review_feedback=feedback)
        assert "## Implementation Steps" in prompt
        assert "## Previous Review Feedback" in prompt
        assert "Fix the tests" in prompt


# ==================================================================
# Part 4: Task model replan_attempt field
# ==================================================================


class TestReplanAttemptField:
    """Tests for replan_attempt field on Task model."""

    def test_default_replan_attempt_is_zero(self):
        """New tasks should have replan_attempt=0."""
        task = make_task()
        assert task.replan_attempt == 0

    def test_replan_attempt_roundtrip(self):
        """replan_attempt should survive model_copy updates."""
        task = make_task(replan_attempt=1)
        assert task.replan_attempt == 1
        updated = task.model_copy(update={"replan_attempt": 2})
        assert updated.replan_attempt == 2

    async def test_replan_attempt_persists_to_db(
        self, task_manager: TaskManager,
    ):
        """replan_attempt should persist through DB round-trip."""
        task = make_task(
            task_id="proj-a:T-P0-DB1",
            project_id="proj-a",
            local_task_id="T-P0-DB1",
            replan_attempt=1,
        )
        await task_manager.create_task(task)
        loaded = await task_manager.get_task("proj-a:T-P0-DB1")
        assert loaded is not None
        assert loaded.replan_attempt == 1


# ==================================================================
# Part 5: _build_replan_feedback helper
# ==================================================================


class TestBuildReplanFeedback:
    """Tests for _build_replan_feedback helper function."""

    def test_with_user_reason(self):
        """User reason should appear in feedback."""
        from src.routes.reviews import _build_replan_feedback
        task = make_task()
        result = _build_replan_feedback(task, "Please add error handling")
        assert "Please add error handling" in result

    def test_without_review_state(self):
        """Should produce feedback even without review state."""
        from src.routes.reviews import _build_replan_feedback
        task = make_task()
        result = _build_replan_feedback(task, "")
        assert "rejected" in result.lower()

    def test_with_review_suggestions(self):
        """Review suggestions from LLMReview should appear in feedback."""
        from src.routes.reviews import _build_replan_feedback
        task = make_task()
        review = ReviewState(
            rounds_total=1, rounds_completed=1,
            consensus_score=0.5, human_decision_needed=True,
            reviews=[
                LLMReview(
                    model="test-model",
                    focus="feasibility",
                    verdict="reject",
                    summary="Plan lacks detail on testing strategy",
                    suggestions=["Add unit tests", "Handle edge cases"],
                    timestamp=datetime.now(UTC),
                ),
            ],
        )
        task = task.model_copy(update={"review": review})
        result = _build_replan_feedback(task, "")
        assert "testing strategy" in result
        assert "Add unit tests" in result
        assert "Handle edge cases" in result
