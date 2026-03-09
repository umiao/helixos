"""Tests for plan-related Pydantic models and error taxonomy in src/enrichment.py.

Tests cover:
- PlanDataRoundTripIntegrity: _parse_plan -> format_plan_as_text round-trip
- SdkEventSerialization: raw artifact serialization of multiple events
- PlanGenerationErrorType enum properties (user_message, retryable, string values)
- PlanGenerationError exception class
- _classify_cli_error helper
- EnrichmentResult Pydantic validation
- ProposedTask Pydantic validation
- PlanResult and PlanStep Pydantic validation
- _parse_enrichment and _parse_plan with Pydantic validation logging
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.enrichment import (
    EnrichmentResult,
    PlanGenerationError,
    PlanGenerationErrorType,
    PlanResult,
    ProposedTask,
    _parse_enrichment,
    _parse_plan,
    format_plan_as_text,
    generate_task_plan,
)
from src.sdk_adapter import ClaudeEvent, ClaudeEventType
from tests.factories import mock_sdk_events

# ------------------------------------------------------------------
# Data integrity tests
# ------------------------------------------------------------------


class TestPlanDataRoundTripIntegrity:
    """All steps/files/ACs survive _parse_plan -> format_plan_as_text."""

    def test_plan_data_round_trip_integrity(self) -> None:
        """Structured plan data survives parse -> format round-trip."""
        inner = json.dumps({
            "plan": "Implement user auth with JWT tokens.",
            "steps": [
                {"step": "Add login endpoint", "files": ["src/auth.py", "src/api.py"]},
                {"step": "Add middleware", "files": ["src/middleware.py"]},
                {"step": "Write tests", "files": ["tests/test_auth.py"]},
            ],
            "acceptance_criteria": [
                "Login returns JWT",
                "Middleware validates token",
                "Tests pass",
            ],
        })
        plan_data = _parse_plan(inner)

        assert plan_data["plan"] == "Implement user auth with JWT tokens."
        assert len(plan_data["steps"]) == 3
        assert plan_data["steps"][0]["files"] == ["src/auth.py", "src/api.py"]
        assert len(plan_data["acceptance_criteria"]) == 3

        formatted = format_plan_as_text(plan_data)
        assert "Implement user auth with JWT tokens." in formatted
        assert "Add login endpoint" in formatted
        assert "src/auth.py" in formatted
        assert "Login returns JWT" in formatted
        assert "Tests pass" in formatted


class TestSdkEventSerialization:
    """SDK event dicts are properly serialized in raw artifacts."""

    @pytest.mark.asyncio
    async def test_multiple_events_serialized_to_artifact(self) -> None:
        """Multiple SDK events are serialized as newline-delimited JSON in artifact."""
        events = [
            ClaudeEvent(type=ClaudeEventType.INIT, session_id="s1"),
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Planning..."),
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                structured_output={
                    "plan": "Do the thing",
                    "steps": [{"step": "Step 1", "files": []}],
                    "acceptance_criteria": ["AC1"],
                },
            ),
        ]
        artifact_content: list[str] = []

        async def capture_artifact(content: str) -> None:
            artifact_content.append(content)

        with patch(
            "src.enrichment.run_claude_query",
            return_value=mock_sdk_events(*events),
        ):
            result = await generate_task_plan(
                "Test", description="desc",
                on_raw_artifact=capture_artifact,
            )

        assert len(artifact_content) == 1
        # Each event is a separate JSON line
        lines = artifact_content[0].split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

        assert result["plan"] == "Do the thing"
        assert len(result["steps"]) == 1


# ------------------------------------------------------------------
# Error taxonomy tests (T-P1-74)
# ------------------------------------------------------------------


class TestPlanGenerationErrorType:
    """Tests for PlanGenerationErrorType enum properties."""

    def test_all_types_have_user_message(self) -> None:
        """Every error type has a non-empty user_message."""
        for et in PlanGenerationErrorType:
            assert len(et.user_message) > 0

    def test_retryable_types(self) -> None:
        """Timeout, parse_failure, cli_error are retryable."""
        assert PlanGenerationErrorType.TIMEOUT.retryable is True
        assert PlanGenerationErrorType.PARSE_FAILURE.retryable is True
        assert PlanGenerationErrorType.CLI_ERROR.retryable is True

    def test_non_retryable_types(self) -> None:
        """CLI unavailable and budget exceeded are not retryable."""
        assert PlanGenerationErrorType.CLI_UNAVAILABLE.retryable is False
        assert PlanGenerationErrorType.BUDGET_EXCEEDED.retryable is False

    def test_string_values(self) -> None:
        """Enum values are lowercase snake_case strings."""
        assert PlanGenerationErrorType.CLI_UNAVAILABLE == "cli_unavailable"
        assert PlanGenerationErrorType.TIMEOUT == "timeout"
        assert PlanGenerationErrorType.PARSE_FAILURE == "parse_failure"
        assert PlanGenerationErrorType.BUDGET_EXCEEDED == "budget_exceeded"
        assert PlanGenerationErrorType.CLI_ERROR == "cli_error"


class TestPlanGenerationError:
    """Tests for PlanGenerationError exception class."""

    def test_error_carries_type(self) -> None:
        """Exception carries error_type for classification."""
        err = PlanGenerationError(PlanGenerationErrorType.TIMEOUT, "timed out")
        assert err.error_type == PlanGenerationErrorType.TIMEOUT
        assert err.detail == "timed out"

    def test_retryable_property(self) -> None:
        """retryable delegates to error_type."""
        err = PlanGenerationError(PlanGenerationErrorType.TIMEOUT, "timed out")
        assert err.retryable is True
        err2 = PlanGenerationError(PlanGenerationErrorType.BUDGET_EXCEEDED, "over")
        assert err2.retryable is False

    def test_user_message_property(self) -> None:
        """user_message delegates to error_type."""
        err = PlanGenerationError(PlanGenerationErrorType.CLI_UNAVAILABLE, "x")
        assert "not installed" in err.user_message.lower()

    def test_str_representation(self) -> None:
        """str() includes error type and detail."""
        err = PlanGenerationError(PlanGenerationErrorType.TIMEOUT, "timed out")
        s = str(err)
        assert "timeout" in s
        assert "timed out" in s


class TestClassifyCliError:
    """Tests for _classify_cli_error helper."""

    def test_budget_exceeded(self) -> None:
        """Stderr mentioning 'budget' -> BUDGET_EXCEEDED."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(1, "Error: API budget exceeded")
        assert result == PlanGenerationErrorType.BUDGET_EXCEEDED

    def test_usage_limit(self) -> None:
        """Stderr mentioning 'usage limit' -> BUDGET_EXCEEDED."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(1, "Usage limit reached")
        assert result == PlanGenerationErrorType.BUDGET_EXCEEDED

    def test_not_found(self) -> None:
        """Stderr mentioning 'not found' -> CLI_UNAVAILABLE."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(127, "claude: command not found")
        assert result == PlanGenerationErrorType.CLI_UNAVAILABLE

    def test_no_such_file(self) -> None:
        """Stderr with 'no such file' -> CLI_UNAVAILABLE."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(1, "No such file or directory")
        assert result == PlanGenerationErrorType.CLI_UNAVAILABLE

    def test_generic_error(self) -> None:
        """Unrecognized errors -> CLI_ERROR."""
        from src.enrichment import _classify_cli_error

        result = _classify_cli_error(1, "Something unexpected happened")
        assert result == PlanGenerationErrorType.CLI_ERROR


# ------------------------------------------------------------------
# Unit tests: Pydantic validation models
# ------------------------------------------------------------------


class TestEnrichmentResultModel:
    """Tests for EnrichmentResult Pydantic validation."""

    def test_valid_enrichment(self) -> None:
        """Valid enrichment data passes validation."""
        result = EnrichmentResult.model_validate(
            {"description": "Add login", "priority": "P0"}
        )
        assert result.description == "Add login"
        assert result.priority == "P0"

    def test_invalid_priority_rejected(self) -> None:
        """Invalid priority enum value is rejected by Pydantic."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="priority"):
            EnrichmentResult.model_validate(
                {"description": "desc", "priority": "P5"}
            )

    def test_missing_required_field(self) -> None:
        """Missing required field is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="description"):
            EnrichmentResult.model_validate({"priority": "P0"})

    def test_all_valid_priorities(self) -> None:
        """All valid priority values are accepted."""
        for p in ("P0", "P1", "P2"):
            result = EnrichmentResult.model_validate(
                {"description": "d", "priority": p}
            )
            assert result.priority == p


class TestProposedTaskModel:
    """Tests for ProposedTask Pydantic validation."""

    def test_valid_proposed_task(self) -> None:
        """Valid proposed task passes validation."""
        task = ProposedTask.model_validate({
            "title": "Add auth",
            "description": "Implement JWT auth",
            "suggested_priority": "P0",
            "suggested_complexity": "S",
            "dependencies": ["Setup DB"],
            "acceptance_criteria": ["Auth works"],
        })
        assert task.title == "Add auth"
        assert task.suggested_priority == "P0"
        assert task.suggested_complexity == "S"
        assert task.dependencies == ["Setup DB"]

    def test_minimal_proposed_task(self) -> None:
        """Proposed task with only required fields uses defaults."""
        task = ProposedTask.model_validate({
            "title": "Fix bug",
            "description": "Fix the login bug",
        })
        assert task.suggested_priority == "P1"
        assert task.suggested_complexity == "M"
        assert task.dependencies == []
        assert task.acceptance_criteria == []

    def test_invalid_priority_rejected(self) -> None:
        """Invalid priority enum is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="suggested_priority"):
            ProposedTask.model_validate({
                "title": "x",
                "description": "y",
                "suggested_priority": "CRITICAL",
            })

    def test_invalid_complexity_rejected(self) -> None:
        """Invalid complexity enum is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="suggested_complexity"):
            ProposedTask.model_validate({
                "title": "x",
                "description": "y",
                "suggested_complexity": "XL",
            })


class TestPlanResultModel:
    """Tests for PlanResult and PlanStep Pydantic validation."""

    def test_valid_plan(self) -> None:
        """Valid plan data passes validation."""
        result = PlanResult.model_validate({
            "plan": "Add caching",
            "steps": [{"step": "Add Redis", "files": ["src/cache.py"]}],
            "acceptance_criteria": ["Tests pass"],
        })
        assert result.plan == "Add caching"
        assert len(result.steps) == 1
        assert result.steps[0].step == "Add Redis"
        assert result.steps[0].files == ["src/cache.py"]

    def test_step_without_files(self) -> None:
        """Step without files defaults to empty list."""
        result = PlanResult.model_validate({
            "plan": "p",
            "steps": [{"step": "Do thing"}],
            "acceptance_criteria": ["ac"],
        })
        assert result.steps[0].files == []

    def test_step_missing_step_key_rejected(self) -> None:
        """Step without required 'step' key is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="step"):
            PlanResult.model_validate({
                "plan": "p",
                "steps": [{"notastep": "invalid"}],
                "acceptance_criteria": [],
            })

    def test_missing_plan_field_rejected(self) -> None:
        """Missing plan field is rejected."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="plan"):
            PlanResult.model_validate({
                "steps": [],
                "acceptance_criteria": [],
            })

    def test_plan_with_proposed_tasks(self) -> None:
        """PlanResult with proposed_tasks validates correctly."""
        result = PlanResult.model_validate({
            "plan": "Auth plan",
            "steps": [{"step": "Do it"}],
            "acceptance_criteria": ["Done"],
            "proposed_tasks": [
                {"title": "Sub-task A", "description": "First piece"},
            ],
        })
        assert len(result.proposed_tasks) == 1
        assert result.proposed_tasks[0].title == "Sub-task A"

    def test_plan_without_proposed_tasks(self) -> None:
        """PlanResult without proposed_tasks defaults to empty list."""
        result = PlanResult.model_validate({
            "plan": "Simple",
            "steps": [{"step": "Do it"}],
            "acceptance_criteria": ["Done"],
        })
        assert result.proposed_tasks == []


class TestParseEnrichmentWithValidation:
    """Tests that _parse_enrichment uses Pydantic and logs raw content."""

    def test_invalid_priority_logs_raw_content(self, caplog: pytest.LogCaptureFixture) -> None:
        """Invalid priority triggers Pydantic rejection and logs raw content."""
        text = json.dumps({"description": "desc", "priority": "HIGH"})
        with caplog.at_level("WARNING"):
            result = _parse_enrichment(text)
        assert result["priority"] == "P1"  # falls back to default
        assert result["description"] == ""  # falls back to default
        assert "Raw" in caplog.text
        assert "HIGH" in caplog.text

    def test_malformed_json_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed JSON logs raw content."""
        with caplog.at_level("WARNING"):
            result = _parse_enrichment("not json {{{")
        assert result["description"] == ""
        assert "Raw" in caplog.text
        assert "not json" in caplog.text


class TestParsePlanWithValidation:
    """Tests that _parse_plan uses Pydantic and logs raw content."""

    def test_invalid_step_structure_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Steps with wrong structure trigger Pydantic rejection and log raw."""
        text = json.dumps({
            "plan": "p",
            "steps": [{"notastep": "bad"}],
            "acceptance_criteria": [],
        })
        with caplog.at_level("WARNING"):
            result = _parse_plan(text)
        assert result["steps"] == []  # falls back
        assert "Raw" in caplog.text

    def test_missing_acceptance_criteria_logs_raw(self, caplog: pytest.LogCaptureFixture) -> None:
        """Missing required field triggers Pydantic rejection."""
        text = json.dumps({"plan": "p", "steps": []})
        with caplog.at_level("WARNING"):
            result = _parse_plan(text)
        assert result["plan"] == text  # raw text fallback
        assert "Raw" in caplog.text
