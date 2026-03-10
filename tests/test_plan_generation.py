"""Tests for plan generation logic in src/enrichment.py.

Tests cover:
- _parse_plan with valid/invalid/malformed JSON
- _validate_plan_structure with various plan shapes
- format_plan_as_text formatting
- generate_task_plan with mocked SDK (success, failure, retries, streaming)
- _validate_plan_structure with PlanValidationConfig limits
- _check_soft_limits warning behavior
- Plan validation retry loop
- ProposedTask files field
- PlanValidationConfig defaults and custom values
- _strip_markdown_fences and markdown fallback parsing
- complexity_hint parameter in generate_task_plan
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.config import (
    OrchestratorSettings,
    PlanValidationConfig,
)
from src.enrichment import (
    MAX_TASKS_PER_PLAN,
    PlanGenerationError,
    PlanGenerationErrorType,
    ProposedTask,
    _check_soft_limits,
    _parse_plan,
    _validate_plan_structure,
    format_plan_as_text,
    generate_task_plan,
)
from src.sdk_adapter import ClaudeEvent, ClaudeEventType
from tests.factories import make_plan_events, mock_sdk_events

# Reusable valid plan data for tests that need to pass structural validation
# but aren't testing the plan content itself.
_VALID_STEPS = [{"step": "Implement feature", "files": ["src/main.py"]}]
_VALID_AC = ["Feature works as expected"]


def _make_error_event(message: str) -> ClaudeEvent:
    """Create a ClaudeEvent for an SDK error."""
    return ClaudeEvent(
        type=ClaudeEventType.ERROR,
        error_message=message,
    )


# ------------------------------------------------------------------
# Unit tests: _parse_plan
# ------------------------------------------------------------------


class TestParsePlan:
    """Tests for the _parse_plan function."""

    def test_valid_json(self) -> None:
        """Parse valid plan JSON."""
        text = json.dumps({
            "plan": "Add caching layer",
            "steps": [{"step": "Add Redis", "files": ["src/cache.py"]}],
            "acceptance_criteria": ["Cache hit rate > 80%"],
        })
        result = _parse_plan(text)
        assert result["plan"] == "Add caching layer"
        assert len(result["steps"]) == 1
        assert result["steps"][0]["step"] == "Add Redis"
        assert result["steps"][0]["files"] == ["src/cache.py"]
        assert result["acceptance_criteria"] == ["Cache hit rate > 80%"]
        assert result["proposed_tasks"] == []

    def test_steps_without_files(self) -> None:
        """Steps without files key get empty files list."""
        text = json.dumps({
            "plan": "Refactor",
            "steps": [{"step": "Split module"}],
            "acceptance_criteria": ["Tests pass"],
        })
        result = _parse_plan(text)
        assert result["steps"][0]["files"] == []

    def test_invalid_json_returns_raw(self) -> None:
        """Non-JSON text falls back to raw text as plan."""
        result = _parse_plan("This is just text")
        assert result["plan"] == "This is just text"
        assert result["steps"] == []
        assert result["acceptance_criteria"] == []

    def test_empty_string(self) -> None:
        """Empty string returns empty plan."""
        result = _parse_plan("")
        assert result["plan"] == ""
        assert result["steps"] == []

    def test_missing_fields_falls_back(self) -> None:
        """Missing required fields trigger Pydantic rejection, falls back to raw text."""
        text = json.dumps({"plan": "Just a plan"})
        result = _parse_plan(text)
        # Pydantic rejects incomplete data, fallback returns raw text as plan
        assert result["plan"] == text
        assert result["steps"] == []
        assert result["acceptance_criteria"] == []

    def test_invalid_steps_rejected(self) -> None:
        """Steps with invalid items cause Pydantic rejection, falls back to raw text."""
        text = json.dumps({
            "plan": "p",
            "steps": [
                {"step": "valid"},
                {"notastep": "invalid"},
                "just a string",
            ],
            "acceptance_criteria": [],
        })
        result = _parse_plan(text)
        # Pydantic rejects the invalid step items, entire plan falls back
        assert result["plan"] == text
        assert result["steps"] == []
        assert result["proposed_tasks"] == []

    def test_with_proposed_tasks(self) -> None:
        """Parse plan JSON with proposed_tasks."""
        text = json.dumps({
            "plan": "Decompose auth feature",
            "steps": [{"step": "Plan subtasks"}],
            "acceptance_criteria": ["Sub-tasks created"],
            "proposed_tasks": [
                {
                    "title": "Add JWT middleware",
                    "description": "Create auth middleware for JWT validation",
                    "suggested_priority": "P0",
                    "suggested_complexity": "S",
                    "dependencies": [],
                    "acceptance_criteria": ["Middleware validates tokens"],
                },
                {
                    "title": "Add login endpoint",
                    "description": "POST /login with credentials",
                    "suggested_priority": "P1",
                    "suggested_complexity": "M",
                    "dependencies": ["Add JWT middleware"],
                    "acceptance_criteria": ["Login returns JWT"],
                },
            ],
        })
        result = _parse_plan(text)
        assert len(result["proposed_tasks"]) == 2
        assert result["proposed_tasks"][0]["title"] == "Add JWT middleware"
        assert result["proposed_tasks"][0]["suggested_priority"] == "P0"
        assert result["proposed_tasks"][1]["dependencies"] == ["Add JWT middleware"]

    def test_proposed_tasks_missing_defaults_to_empty(self) -> None:
        """Plan without proposed_tasks field returns empty list."""
        text = json.dumps({
            "plan": "Simple plan",
            "steps": [{"step": "Do it"}],
            "acceptance_criteria": ["Done"],
        })
        result = _parse_plan(text)
        assert result["proposed_tasks"] == []


# ------------------------------------------------------------------
# Unit tests: _validate_plan_structure
# ------------------------------------------------------------------


class TestValidatePlanStructure:
    """Tests for the _validate_plan_structure function."""

    def test_valid_plan(self) -> None:
        """Complete plan passes validation."""
        plan_data = {
            "plan": "Add caching layer",
            "steps": [{"step": "Add Redis", "files": ["src/cache.py"]}],
            "acceptance_criteria": ["Cache hit rate > 80%"],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is True
        assert reason == "ok"

    def test_empty_plan_text(self) -> None:
        """Empty plan text fails validation."""
        plan_data = {"plan": "", "steps": [{"step": "x"}], "acceptance_criteria": ["y"]}
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert reason == "empty_plan_text"

    def test_whitespace_plan_text(self) -> None:
        """Whitespace-only plan text fails validation."""
        plan_data = {"plan": "   ", "steps": [{"step": "x"}], "acceptance_criteria": ["y"]}
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert reason == "empty_plan_text"

    def test_empty_steps(self) -> None:
        """Empty steps list fails validation."""
        plan_data = {"plan": "A plan", "steps": [], "acceptance_criteria": ["y"]}
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert reason == "empty_steps"

    def test_empty_acceptance_criteria(self) -> None:
        """Empty acceptance_criteria fails validation."""
        plan_data = {"plan": "A plan", "steps": [{"step": "x"}], "acceptance_criteria": []}
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert reason == "empty_acceptance_criteria"

    def test_missing_keys(self) -> None:
        """Missing keys treated as empty."""
        is_valid, reason = _validate_plan_structure({})
        assert is_valid is False
        assert reason == "empty_plan_text"

    def test_too_many_proposed_tasks(self) -> None:
        """More than MAX_TASKS_PER_PLAN proposed tasks fails validation."""
        plan_data = {
            "plan": "Big plan",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": f"Task {i}", "description": f"Desc {i}"}
                for i in range(MAX_TASKS_PER_PLAN + 1)
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert "too_many_proposed_tasks" in reason

    def test_max_proposed_tasks_at_limit(self) -> None:
        """Exactly MAX_TASKS_PER_PLAN proposed tasks passes validation."""
        plan_data = {
            "plan": "Plan at limit",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": f"Task {i}", "description": f"Desc {i}"}
                for i in range(MAX_TASKS_PER_PLAN)
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is True
        assert reason == "ok"


# ------------------------------------------------------------------
# Unit tests: format_plan_as_text
# ------------------------------------------------------------------


class TestFormatPlanAsText:
    """Tests for the format_plan_as_text function."""

    def test_full_plan(self) -> None:
        """Format a complete plan with all sections."""
        plan_data = {
            "plan": "Add user auth with JWT tokens.",
            "steps": [
                {"step": "Create auth middleware", "files": ["src/auth.py"]},
                {"step": "Add login endpoint", "files": ["src/api.py"]},
            ],
            "acceptance_criteria": ["Login returns JWT", "Protected routes reject unauthenticated"],
        }
        text = format_plan_as_text(plan_data)
        assert "Add user auth with JWT tokens." in text
        assert "## Implementation Steps" in text
        assert "1. Create auth middleware" in text
        assert "   - src/auth.py" in text
        assert "2. Add login endpoint" in text
        assert "## Acceptance Criteria" in text
        assert "- Login returns JWT" in text

    def test_empty_plan(self) -> None:
        """Empty plan data produces empty string."""
        text = format_plan_as_text({"plan": "", "steps": [], "acceptance_criteria": []})
        assert text == ""

    def test_plan_only(self) -> None:
        """Plan with no steps or criteria."""
        text = format_plan_as_text({
            "plan": "Just a summary.",
            "steps": [],
            "acceptance_criteria": [],
        })
        assert text == "Just a summary."

    def test_plan_with_proposed_tasks(self) -> None:
        """Plan with proposed tasks includes Proposed Tasks section."""
        text = format_plan_as_text({
            "plan": "Auth decomposition.",
            "steps": [{"step": "Plan subtasks"}],
            "acceptance_criteria": ["Sub-tasks created"],
            "proposed_tasks": [
                {"title": "Add JWT middleware", "description": "Create auth middleware"},
                {"title": "Add login endpoint", "description": "POST /login"},
            ],
        })
        assert "## Proposed Tasks" in text
        assert "1. Add JWT middleware" in text
        assert "Create auth middleware" in text
        assert "2. Add login endpoint" in text

    # ---- Edge-case regression tests (T-P1-146) ----

    def test_whitespace_only_plan(self) -> None:
        """Whitespace-only plan text produces empty output."""
        text = format_plan_as_text({"plan": "   \n\t  ", "steps": [], "acceptance_criteria": []})
        # strip() in format_plan_as_text should produce empty string
        assert text.strip() == ""

    def test_nested_markdown_content(self) -> None:
        """Plan summary with nested markdown (bold, italic, inline code)."""
        text = format_plan_as_text({
            "plan": "Add **bold** and *italic* and `code` features.",
            "steps": [{"step": "Update `parser.py` with **new** rules"}],
            "acceptance_criteria": ["*Italic* text renders"],
        })
        assert "**bold**" in text
        assert "*italic*" in text
        assert "`code`" in text
        assert "`parser.py`" in text

    def test_code_block_in_steps(self) -> None:
        """Steps containing code fences are preserved."""
        text = format_plan_as_text({
            "plan": "Refactor config loader.",
            "steps": [
                {"step": "Add ```python\nimport os\n``` to config.py", "files": ["config.py"]},
            ],
            "acceptance_criteria": [],
        })
        assert "config.py" in text
        assert "import os" in text

    def test_very_long_content(self) -> None:
        """Very long plan text is preserved without truncation."""
        long_summary = "A" * 5000
        text = format_plan_as_text({
            "plan": long_summary,
            "steps": [],
            "acceptance_criteria": [],
        })
        assert len(text) >= 5000
        assert text == long_summary

    def test_missing_keys(self) -> None:
        """Missing optional keys produce valid output."""
        text = format_plan_as_text({})
        assert text == ""

    def test_non_dict_steps(self) -> None:
        """Steps that are plain strings (not dicts) still render."""
        text = format_plan_as_text({
            "plan": "Summary.",
            "steps": ["First step", "Second step"],
            "acceptance_criteria": [],
        })
        assert "1. First step" in text
        assert "2. Second step" in text


# ------------------------------------------------------------------
# Unit tests: generate_task_plan (async)
# ------------------------------------------------------------------


class TestGenerateTaskPlan:
    """Tests for the generate_task_plan async function (SDK-based)."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful plan generation returns structured data."""
        events = make_plan_events(
            "Implement dark mode",
            [{"step": "Add theme context", "files": ["src/theme.ts"]}],
            ["Theme toggle works"],
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ):
            result = await generate_task_plan("Add dark mode")
            assert result["plan"] == "Implement dark mode"
            assert len(result["steps"]) == 1
            assert result["acceptance_criteria"] == ["Theme toggle works"]

    @pytest.mark.asyncio
    async def test_with_repo_path(self, tmp_path: Path) -> None:
        """repo_path is passed as add_dirs in QueryOptions."""
        repo = tmp_path / "repo"
        repo.mkdir()
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task", repo_path=repo)
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert str(repo) in options.add_dirs

    @pytest.mark.asyncio
    async def test_without_repo_path(self) -> None:
        """No add_dirs when repo_path is None."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert options.add_dirs == []

    @pytest.mark.asyncio
    async def test_plan_disables_cli_hooks(self) -> None:
        """Plan agent uses setting_sources=[] to disable CLI hooks."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert options.setting_sources == []

    @pytest.mark.asyncio
    async def test_plan_injects_session_context(self) -> None:
        """Plan agent system prompt includes session context."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert "Session Context" in options.system_prompt

    @pytest.mark.asyncio
    async def test_sdk_error_raises(self) -> None:
        """SDK error event raises PlanGenerationError."""
        events = [_make_error_event("SDK error")]

        with (
            patch(
                "src.enrichment.run_claude_query",
                return_value=mock_sdk_events(*events),
            ),
            pytest.raises(PlanGenerationError, match="Claude SDK error"),
        ):
            await generate_task_plan("Broken task")

    @pytest.mark.asyncio
    async def test_with_description(self) -> None:
        """Existing description is included in the prompt."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task", description="Existing desc")
            prompt_arg = mock_query.call_args[0][0]
            assert "Existing desc" in prompt_arg

    @pytest.mark.asyncio
    async def test_on_log_callback_called(self) -> None:
        """on_log callback is called for SDK events."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )
        logged: list[str] = []

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Task", on_log=logged.append,
            )
            assert result["plan"] == "A valid plan summary"
            # on_log should have been called (at least [DONE] for result event)
            assert len(logged) >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_on_no_output(self) -> None:
        """Heartbeat emitted when no SDK events for heartbeat_seconds."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )
        logged: list[str] = []

        # Create an async generator that delays before yielding
        async def _slow_events() -> AsyncIterator[ClaudeEvent]:
            await asyncio.sleep(0.3)  # Exceed heartbeat timeout
            for ev in events:
                yield ev

        with patch(
            "src.enrichment.run_claude_query",
            return_value=_slow_events(),
        ):
            await generate_task_plan(
                "Task", on_log=logged.append, heartbeat_seconds=0.1,
            )
            heartbeats = [line for line in logged if "[PROGRESS] heartbeat" in line]
            assert len(heartbeats) >= 1

    @pytest.mark.asyncio
    async def test_on_raw_artifact_called(self) -> None:
        """on_raw_artifact callback is called with serialized events."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )
        artifacts: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifacts.append(content)

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ):
            await generate_task_plan(
                "Task", on_raw_artifact=capture_artifact,
            )
            assert len(artifacts) == 1
            assert len(artifacts[0]) > 0

    @pytest.mark.asyncio
    async def test_on_raw_artifact_called_even_on_failure(self) -> None:
        """on_raw_artifact persists output even when SDK returns error."""
        events = [
            ClaudeEvent(type=ClaudeEventType.TEXT, text="partial output"),
            _make_error_event("SDK error"),
        ]
        artifacts: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifacts.append(content)

        with (
            patch(
                "src.enrichment.run_claude_query",
                return_value=mock_sdk_events(*events),
            ),
            pytest.raises(PlanGenerationError, match="Claude SDK error"),
        ):
            await generate_task_plan(
                "Broken task", on_raw_artifact=capture_artifact,
            )
        # Raw artifact should still be persisted despite error
        assert len(artifacts) == 1
        assert "partial output" in artifacts[0]

    @pytest.mark.asyncio
    async def test_structural_validation_rejects_empty_plan(self) -> None:
        """Plan with empty steps is rejected after parsing (no retries)."""
        events = make_plan_events("Plan text", [], ["criteria"])

        with (
            patch(
                "src.enrichment.run_claude_query",
                return_value=mock_sdk_events(*events),
            ),
            pytest.raises(PlanGenerationError, match="Plan validation failed.*empty_steps"),
        ):
            await generate_task_plan(
                "Task",
                plan_validation=PlanValidationConfig(max_validation_retries=0),
            )

    @pytest.mark.asyncio
    async def test_query_options_configured(self) -> None:
        """QueryOptions are configured with model, system_prompt, json_schema."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert options.model == "claude-opus-4-6"
            assert options.permission_mode == "plan"
            assert options.system_prompt is not None
            assert options.json_schema is not None

    @pytest.mark.asyncio
    async def test_system_prompt_includes_project_context(self) -> None:
        """System prompt includes CLAUDE.md rules and TASKS.md schema."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            prompt = options.system_prompt
            # TASKS.md schema context
            assert "T-P{priority}-{number}" in prompt
            assert "Acceptance Criteria" in prompt
            # CLAUDE.md project rules
            assert "Scenario matrix" in prompt
            assert "Journey-first ACs" in prompt
            assert "proposed_tasks" in prompt

    @pytest.mark.asyncio
    async def test_json_schema_includes_proposed_tasks(self) -> None:
        """JSON schema includes proposed_tasks array definition."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            schema = json.loads(options.json_schema)
            assert "proposed_tasks" in schema["properties"]
            pt_schema = schema["properties"]["proposed_tasks"]
            assert pt_schema["type"] == "array"
            assert pt_schema["maxItems"] == 10
            item_props = pt_schema["items"]["properties"]
            assert "title" in item_props
            assert "description" in item_props
            assert "files" in item_props
            assert "suggested_priority" in item_props
            assert "suggested_complexity" in item_props
            assert "dependencies" in item_props
            assert "acceptance_criteria" in item_props

    @pytest.mark.asyncio
    async def test_on_stream_event_callback(self) -> None:
        """on_stream_event callback is called for each SDK event."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )
        received: list[dict] = []

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Task", on_stream_event=received.append,
            )
            assert result["plan"] == "A valid plan summary"
            assert len(received) >= 1
            result_events = [e for e in received if e.get("type") == "result"]
            assert len(result_events) == 1

    @pytest.mark.asyncio
    async def test_on_stream_event_none_is_safe(self) -> None:
        """on_stream_event=None does not crash."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Task", on_stream_event=None,
            )
            assert result["plan"] == "A valid plan summary"

    @pytest.mark.asyncio
    async def test_multi_event_stream(self) -> None:
        """Multiple SDK events are dispatched to on_stream_event."""
        events = [
            ClaudeEvent(type=ClaudeEventType.INIT, session_id="test-session"),
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Planning..."),
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                structured_output={
                    "plan": "Multi-event plan",
                    "steps": [{"step": "Step 1", "files": []}],
                    "acceptance_criteria": ["AC 1"],
                },
            ),
        ]
        received: list[dict] = []

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Task", on_stream_event=received.append,
            )
            assert result["plan"] == "Multi-event plan"
            assert len(received) == 3
            assert received[0]["type"] == "init"
            assert received[1]["type"] == "text"
            assert received[2]["type"] == "result"

    @pytest.mark.asyncio
    async def test_jsonl_file_persistence(self, tmp_path: Path) -> None:
        """JSONL log files are created when stream_log_dir + task_id given."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ):
            await generate_task_plan(
                "Task",
                stream_log_dir=tmp_path,
                task_id="test-task-1",
            )

        log_dir = tmp_path / "test-task-1"
        assert log_dir.exists()

        jsonl_files = list(log_dir.glob("plan_stream_*.jsonl"))
        assert len(jsonl_files) == 1
        content = jsonl_files[0].read_text(encoding="utf-8").strip()
        assert len(content) > 0
        # Each line should be valid JSON
        for line in content.split("\n"):
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

        raw_files = list(log_dir.glob("plan_raw_*.log"))
        assert len(raw_files) == 1


# ------------------------------------------------------------------
# Unit tests: _validate_plan_structure with PlanValidationConfig
# ------------------------------------------------------------------


class TestValidatePlanStructureWithConfig:
    """Tests for _validate_plan_structure with configurable limits."""

    def test_hard_ceiling_max_proposed_tasks(self) -> None:
        """Exceeding max_proposed_tasks (hard ceiling) fails validation."""
        config = PlanValidationConfig(max_proposed_tasks=5)
        plan_data = {
            "plan": "Plan",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": f"Task {i}", "description": f"Desc {i}"}
                for i in range(6)
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data, config)
        assert is_valid is False
        assert "too_many_proposed_tasks" in reason

    def test_at_max_proposed_tasks_passes(self) -> None:
        """Exactly max_proposed_tasks passes validation."""
        config = PlanValidationConfig(max_proposed_tasks=5)
        plan_data = {
            "plan": "Plan",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": f"Task {i}", "description": f"Desc {i}"}
                for i in range(5)
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data, config)
        assert is_valid is True

    def test_dependency_cycle_detected(self) -> None:
        """Circular dependencies in proposed_tasks fails validation."""
        plan_data = {
            "plan": "Plan",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": "A", "description": "Task A", "dependencies": ["B"]},
                {"title": "B", "description": "Task B", "dependencies": ["A"]},
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert "dependency_cycle_detected" in reason

    def test_three_way_cycle_detected(self) -> None:
        """Three-way circular dependency fails validation."""
        plan_data = {
            "plan": "Plan",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": "A", "description": "a", "dependencies": ["B"]},
                {"title": "B", "description": "b", "dependencies": ["C"]},
                {"title": "C", "description": "c", "dependencies": ["A"]},
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is False
        assert "dependency_cycle_detected" in reason

    def test_no_cycle_passes(self) -> None:
        """Valid DAG dependencies pass validation."""
        plan_data = {
            "plan": "Plan",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": "A", "description": "a", "dependencies": []},
                {"title": "B", "description": "b", "dependencies": ["A"]},
                {"title": "C", "description": "c", "dependencies": ["A", "B"]},
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is True

    def test_external_dependency_ignored_for_cycle_check(self) -> None:
        """Dependencies on external tasks (not in proposed) are ignored for cycle check."""
        plan_data = {
            "plan": "Plan",
            "steps": [{"step": "x"}],
            "acceptance_criteria": ["y"],
            "proposed_tasks": [
                {"title": "A", "description": "a", "dependencies": ["T-P0-1"]},
                {"title": "B", "description": "b", "dependencies": ["A"]},
            ],
        }
        is_valid, reason = _validate_plan_structure(plan_data)
        assert is_valid is True

    def test_default_config_uses_10_max(self) -> None:
        """Default PlanValidationConfig uses max_proposed_tasks=10."""
        config = PlanValidationConfig()
        assert config.max_proposed_tasks == 10
        assert config.soft_max_proposed_tasks == 8
        assert config.max_validation_retries == 2


# ------------------------------------------------------------------
# Unit tests: _check_soft_limits
# ------------------------------------------------------------------


class TestCheckSoftLimits:
    """Tests for _check_soft_limits (warning-only, non-blocking)."""

    def test_no_warnings_within_limits(self, caplog: pytest.LogCaptureFixture) -> None:
        """No warnings emitted when within soft limits."""
        config = PlanValidationConfig(
            soft_max_proposed_tasks=8,
            soft_max_files_per_task=8,
            soft_max_steps_per_task=12,
        )
        plan_data = {
            "proposed_tasks": [
                {"title": "T", "description": "d", "files": ["a.py"]}
            ],
            "steps": [{"step": "s"}],
        }
        with caplog.at_level(logging.WARNING, logger="src.enrichment"):
            _check_soft_limits(plan_data, config)
        assert len(caplog.records) == 0

    def test_warns_on_too_many_tasks(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning emitted when proposed_tasks exceeds soft max."""
        config = PlanValidationConfig(soft_max_proposed_tasks=2)
        plan_data = {
            "proposed_tasks": [
                {"title": f"T{i}", "description": "d"} for i in range(3)
            ],
            "steps": [{"step": "s"}],
        }
        with caplog.at_level(logging.WARNING, logger="src.enrichment"):
            _check_soft_limits(plan_data, config)
        assert any("3 proposed tasks exceeds soft max 2" in r.message for r in caplog.records)

    def test_warns_on_too_many_files(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning emitted when a task has too many files."""
        config = PlanValidationConfig(soft_max_files_per_task=2)
        plan_data = {
            "proposed_tasks": [
                {"title": "T", "description": "d", "files": ["a", "b", "c"]}
            ],
            "steps": [{"step": "s"}],
        }
        with caplog.at_level(logging.WARNING, logger="src.enrichment"):
            _check_soft_limits(plan_data, config)
        assert any("3 files" in r.message for r in caplog.records)

    def test_warns_on_too_many_steps(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning emitted when plan has too many steps."""
        config = PlanValidationConfig(soft_max_steps_per_task=2)
        plan_data = {
            "proposed_tasks": [],
            "steps": [{"step": f"s{i}"} for i in range(3)],
        }
        with caplog.at_level(logging.WARNING, logger="src.enrichment"):
            _check_soft_limits(plan_data, config)
        assert any("3 steps" in r.message for r in caplog.records)


# ------------------------------------------------------------------
# Unit tests: generate_task_plan validation retry
# ------------------------------------------------------------------


class TestPlanValidationRetry:
    """Tests for validation retry loop in generate_task_plan."""

    @pytest.mark.asyncio
    async def test_retry_on_validation_failure(self) -> None:
        """Retries on validation failure, succeeds on 2nd attempt."""
        bad_events = make_plan_events("Plan text", [], ["criteria"])
        good_events = make_plan_events(
            "Good plan", _VALID_STEPS, _VALID_AC,
        )

        call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> AsyncIterator[ClaudeEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                async for ev in mock_sdk_events(*bad_events):
                    yield ev
            else:
                async for ev in mock_sdk_events(*good_events):
                    yield ev

        with patch(
            "src.enrichment.run_claude_query",
            side_effect=_side_effect,
        ):
            result = await generate_task_plan(
                "Task",
                plan_validation=PlanValidationConfig(max_validation_retries=2),
            )
            assert result["plan"] == "Good plan"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self) -> None:
        """All retries exhausted raises VALIDATION_FAILURE."""
        bad_events = make_plan_events("Plan text", [], ["criteria"])

        async def _always_bad(*args: Any, **kwargs: Any) -> AsyncIterator[ClaudeEvent]:
            async for ev in mock_sdk_events(*bad_events):
                yield ev

        with (
            patch(
                "src.enrichment.run_claude_query",
                side_effect=_always_bad,
            ),
            pytest.raises(PlanGenerationError) as exc_info,
        ):
            await generate_task_plan(
                "Task",
                plan_validation=PlanValidationConfig(max_validation_retries=1),
            )
        assert exc_info.value.error_type == PlanGenerationErrorType.VALIDATION_FAILURE
        assert "2 attempts" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_retry_appends_error_to_prompt(self) -> None:
        """On retry, validation error is appended to prompt."""
        bad_events = make_plan_events("Plan text", [], ["criteria"])
        good_events = make_plan_events(
            "Fixed plan", _VALID_STEPS, _VALID_AC,
        )

        prompts_received: list[str] = []

        async def _side_effect(prompt: str, *args: Any, **kwargs: Any) -> AsyncIterator[ClaudeEvent]:
            prompts_received.append(prompt)
            if len(prompts_received) == 1:
                async for ev in mock_sdk_events(*bad_events):
                    yield ev
            else:
                async for ev in mock_sdk_events(*good_events):
                    yield ev

        with patch(
            "src.enrichment.run_claude_query",
            side_effect=_side_effect,
        ):
            await generate_task_plan(
                "Task",
                plan_validation=PlanValidationConfig(max_validation_retries=2),
            )

        assert len(prompts_received) == 2
        # First prompt is original
        assert "Previous Attempt Failed" not in prompts_received[0]
        # Second prompt includes feedback
        assert "Previous Attempt Failed" in prompts_received[1]
        assert "empty_steps" in prompts_received[1]

    @pytest.mark.asyncio
    async def test_no_retry_when_retries_zero(self) -> None:
        """max_validation_retries=0 means no retry."""
        bad_events = make_plan_events("Plan text", [], ["criteria"])
        call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> AsyncIterator[ClaudeEvent]:
            nonlocal call_count
            call_count += 1
            async for ev in mock_sdk_events(*bad_events):
                yield ev

        with (
            patch(
                "src.enrichment.run_claude_query",
                side_effect=_side_effect,
            ),
            pytest.raises(PlanGenerationError),
        ):
            await generate_task_plan(
                "Task",
                plan_validation=PlanValidationConfig(max_validation_retries=0),
            )
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_cycle_detection(self) -> None:
        """Retry triggered when proposed_tasks have cyclic dependencies."""
        cyclic_events = [
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                structured_output={
                    "plan": "Plan with cycles",
                    "steps": [{"step": "s1"}],
                    "acceptance_criteria": ["ac1"],
                    "proposed_tasks": [
                        {"title": "A", "description": "a", "dependencies": ["B"]},
                        {"title": "B", "description": "b", "dependencies": ["A"]},
                    ],
                },
            ),
        ]
        good_events = make_plan_events(
            "Fixed plan", _VALID_STEPS, _VALID_AC,
        )
        call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> AsyncIterator[ClaudeEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                async for ev in mock_sdk_events(*cyclic_events):
                    yield ev
            else:
                async for ev in mock_sdk_events(*good_events):
                    yield ev

        with patch(
            "src.enrichment.run_claude_query",
            side_effect=_side_effect,
        ):
            result = await generate_task_plan(
                "Task",
                plan_validation=PlanValidationConfig(max_validation_retries=2),
            )
            assert result["plan"] == "Fixed plan"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_on_log_emits_retry_message(self) -> None:
        """on_log callback receives retry message on validation failure."""
        bad_events = make_plan_events("Plan text", [], ["criteria"])
        good_events = make_plan_events(
            "Fixed plan", _VALID_STEPS, _VALID_AC,
        )
        logged: list[str] = []

        call_count = 0

        async def _side_effect(*args: Any, **kwargs: Any) -> AsyncIterator[ClaudeEvent]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                async for ev in mock_sdk_events(*bad_events):
                    yield ev
            else:
                async for ev in mock_sdk_events(*good_events):
                    yield ev

        with patch(
            "src.enrichment.run_claude_query",
            side_effect=_side_effect,
        ):
            await generate_task_plan(
                "Task",
                on_log=logged.append,
                plan_validation=PlanValidationConfig(max_validation_retries=2),
            )
        retry_msgs = [line for line in logged if "[RETRY]" in line]
        assert len(retry_msgs) == 1
        assert "empty_steps" in retry_msgs[0]


# ------------------------------------------------------------------
# Unit tests: ProposedTask files field
# ------------------------------------------------------------------


class TestProposedTaskFilesField:
    """Tests for the ProposedTask files field (added in T-P1-114)."""

    def test_files_field_present(self) -> None:
        """ProposedTask has files field."""
        task = ProposedTask(title="T", description="D", files=["a.py", "b.py"])
        assert task.files == ["a.py", "b.py"]

    def test_files_default_empty(self) -> None:
        """files defaults to empty list."""
        task = ProposedTask(title="T", description="D")
        assert task.files == []

    def test_files_in_model_dump(self) -> None:
        """files field appears in model_dump output."""
        task = ProposedTask(title="T", description="D", files=["x.py"])
        dumped = task.model_dump()
        assert dumped["files"] == ["x.py"]


# ------------------------------------------------------------------
# Unit tests: PlanValidationConfig
# ------------------------------------------------------------------


class TestPlanValidationConfig:
    """Tests for PlanValidationConfig pydantic model."""

    def test_defaults(self) -> None:
        """Default values match task spec."""
        config = PlanValidationConfig()
        assert config.max_proposed_tasks == 10
        assert config.soft_max_proposed_tasks == 8
        assert config.soft_max_steps_per_task == 12
        assert config.soft_max_files_per_task == 8
        assert config.max_validation_retries == 2

    def test_custom_values(self) -> None:
        """Custom values are accepted."""
        config = PlanValidationConfig(
            max_proposed_tasks=5,
            soft_max_proposed_tasks=3,
            soft_max_steps_per_task=6,
            soft_max_files_per_task=4,
            max_validation_retries=1,
        )
        assert config.max_proposed_tasks == 5
        assert config.max_validation_retries == 1

    def test_loaded_from_yaml(self) -> None:
        """PlanValidationConfig is parsed from OrchestratorSettings."""
        settings = OrchestratorSettings(
            plan_validation=PlanValidationConfig(max_proposed_tasks=7),
        )
        assert settings.plan_validation.max_proposed_tasks == 7

    def test_default_in_orchestrator_settings(self) -> None:
        """OrchestratorSettings has plan_validation with defaults."""
        settings = OrchestratorSettings()
        assert settings.plan_validation.max_proposed_tasks == 10


# ------------------------------------------------------------------
# Unit tests: _strip_markdown_fences and _parse_plan fallback
# ------------------------------------------------------------------


class TestStripMarkdownFences:
    """Tests for _strip_markdown_fences helper."""

    def test_plain_json_unchanged(self) -> None:
        """Plain JSON string is returned unchanged."""
        from src.enrichment import _strip_markdown_fences

        text = '{"plan": "hello"}'
        assert _strip_markdown_fences(text) == text

    def test_json_code_fence(self) -> None:
        """JSON inside ```json ... ``` is extracted."""
        from src.enrichment import _strip_markdown_fences

        text = '```json\n{"plan": "hello"}\n```'
        assert _strip_markdown_fences(text) == '{"plan": "hello"}'

    def test_plain_code_fence(self) -> None:
        """JSON inside ``` ... ``` (no language) is extracted."""
        from src.enrichment import _strip_markdown_fences

        text = '```\n{"plan": "hello"}\n```'
        assert _strip_markdown_fences(text) == '{"plan": "hello"}'

    def test_preamble_text(self) -> None:
        """Preamble text before JSON object is stripped."""
        from src.enrichment import _strip_markdown_fences

        text = 'Here is the plan:\n{"plan": "hello"}'
        result = _strip_markdown_fences(text)
        assert result.startswith('{"plan"')

    def test_preamble_with_fence(self) -> None:
        """Preamble + fenced JSON is extracted."""
        from src.enrichment import _strip_markdown_fences

        text = 'Here is the plan:\n```json\n{"plan": "hello"}\n```'
        assert _strip_markdown_fences(text) == '{"plan": "hello"}'


class TestParsePlanMarkdownFallback:
    """Tests for _parse_plan handling markdown-fenced JSON responses."""

    def test_markdown_fenced_json_parsed(self) -> None:
        """Plan with markdown fences is correctly parsed via fallback."""
        from src.enrichment import _parse_plan

        fenced = (
            '```json\n'
            '{"plan": "Do stuff", "steps": [{"step": "Step 1"}], '
            '"acceptance_criteria": ["AC1"]}\n'
            '```'
        )
        result = _parse_plan(fenced)
        assert result["plan"] == "Do stuff"
        assert len(result["steps"]) == 1
        assert result["acceptance_criteria"] == ["AC1"]

    def test_preamble_json_parsed(self) -> None:
        """Plan with preamble text before JSON is correctly parsed."""
        from src.enrichment import _parse_plan

        text = (
            'Here is the plan:\n'
            '{"plan": "Do stuff", "steps": [{"step": "Step 1"}], '
            '"acceptance_criteria": ["AC1"]}'
        )
        result = _parse_plan(text)
        assert result["plan"] == "Do stuff"
        assert len(result["steps"]) == 1

    def test_dict_input_unchanged(self) -> None:
        """Dict input bypasses fence stripping entirely."""
        from src.enrichment import _parse_plan

        data = {
            "plan": "Do stuff",
            "steps": [{"step": "Step 1"}],
            "acceptance_criteria": ["AC1"],
        }
        result = _parse_plan(data)
        assert result["plan"] == "Do stuff"


# ------------------------------------------------------------------
# Unit tests: complexity_hint in generate_task_plan
# ------------------------------------------------------------------


class TestComplexityHint:
    """Tests for complexity_hint parameter in generate_task_plan."""

    @pytest.mark.asyncio
    async def test_complexity_hint_in_system_prompt(self) -> None:
        """complexity_hint value appears in the rendered system prompt."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task", complexity_hint="M")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            # Phase 4 guidance should reference the complexity hint
            assert "Complexity hint: M" in options.system_prompt

    @pytest.mark.asyncio
    async def test_complexity_hint_default_s(self) -> None:
        """Default complexity_hint is S."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task")
            call_args = mock_query.call_args
            options = call_args[1].get("options") or call_args[0][1]
            assert "Complexity hint: S" in options.system_prompt

    @pytest.mark.asyncio
    async def test_complexity_hint_in_user_prompt(self) -> None:
        """complexity_hint value appears in the user prompt."""
        events = make_plan_events(
            "A valid plan summary", _VALID_STEPS, _VALID_AC,
        )

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ) as mock_query:
            await generate_task_plan("Task", complexity_hint="L")
            call_args = mock_query.call_args
            user_prompt = call_args[1].get("prompt") or call_args[0][0]
            assert "Complexity: L" in user_prompt
