"""Tests for conversation extraction using real fixture data.

Feeds persisted fixture files through ``collect_turns()`` and
``_extract_conversation_summary()`` to verify meaningful output.
All tests are deterministic -- no live CLI calls.

Covers T-P2-91 acceptance criteria:
  AC1: fixtures in tests/fixtures/ (sdk_review_session.json, etc.)
  AC2: collect_turns() produces non-empty text + tool actions
  AC3: summary extraction produces non-empty findings/actions
  AC4: ClaudeResult.structured_output correctly extracted
  AC5: deterministic (no live CLI calls)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from src.review_pipeline import _extract_conversation_summary
from src.sdk_adapter import (
    ClaudeEvent,
    ClaudeEventType,
    ClaudeResult,
    collect_turns,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> list[dict[str, Any]]:
    """Load a fixture JSON file and return its event list."""
    path = FIXTURES_DIR / name
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["events"]


def _events_from_dicts(raw_events: list[dict[str, Any]]) -> list[ClaudeEvent]:
    """Convert raw JSON dicts to ClaudeEvent objects."""
    events: list[ClaudeEvent] = []
    for raw in raw_events:
        event_type = ClaudeEventType(raw["type"])
        kwargs: dict[str, Any] = {"type": event_type}

        # Map fields based on event type
        if event_type == ClaudeEventType.INIT:
            kwargs["session_id"] = raw.get("session_id")
        elif event_type == ClaudeEventType.TEXT:
            kwargs["text"] = raw.get("text")
            kwargs["model"] = raw.get("model")
        elif event_type == ClaudeEventType.TOOL_USE:
            kwargs["tool_name"] = raw.get("tool_name")
            kwargs["tool_input"] = raw.get("tool_input")
            kwargs["tool_use_id"] = raw.get("tool_use_id")
            kwargs["model"] = raw.get("model")
        elif event_type == ClaudeEventType.TOOL_RESULT:
            kwargs["tool_result_content"] = raw.get("tool_result_content")
            kwargs["tool_is_error"] = raw.get("tool_is_error")
            kwargs["tool_result_for_id"] = raw.get("tool_result_for_id")
        elif event_type == ClaudeEventType.RESULT:
            kwargs["result_text"] = raw.get("result_text")
            kwargs["structured_output"] = raw.get("structured_output")
            kwargs["cost_usd"] = raw.get("cost_usd")
            kwargs["usage"] = raw.get("usage")
            kwargs["duration_ms"] = raw.get("duration_ms")
            kwargs["model"] = raw.get("model")
            kwargs["num_turns"] = raw.get("num_turns")
            kwargs["session_id"] = raw.get("session_id")
        elif event_type == ClaudeEventType.ERROR:
            kwargs["error_message"] = raw.get("error_message")
            kwargs["cost_usd"] = raw.get("cost_usd")
            kwargs["usage"] = raw.get("usage")
            kwargs["duration_ms"] = raw.get("duration_ms")
            kwargs["model"] = raw.get("model")

        events.append(ClaudeEvent(**kwargs))
    return events


async def _event_aiter(events: list[ClaudeEvent]) -> AsyncIterator[ClaudeEvent]:
    """Wrap a list of ClaudeEvent into an async iterator."""
    for ev in events:
        yield ev


# ---------------------------------------------------------------------------
# Review session fixture tests
# ---------------------------------------------------------------------------


class TestReviewFixture:
    """Tests using sdk_review_session.json fixture."""

    @pytest.fixture()
    def raw_events(self) -> list[dict[str, Any]]:
        return _load_fixture("sdk_review_session.json")

    @pytest.fixture()
    def events(self, raw_events: list[dict[str, Any]]) -> list[ClaudeEvent]:
        return _events_from_dicts(raw_events)

    @pytest.mark.asyncio()
    async def test_collect_turns_produces_nonempty_turns(
        self, events: list[ClaudeEvent],
    ) -> None:
        """AC2: collect_turns produces non-empty text + tool actions."""
        turns, _result = await collect_turns(_event_aiter(events))

        assert len(turns) >= 2, "Review session should have multiple turns"

        # At least one turn has text
        texts = [t.text for t in turns if t.text.strip()]
        assert len(texts) >= 1, "At least one turn should have text"

        # At least one turn has tool actions
        all_actions = [a for t in turns for a in t.tool_actions]
        assert len(all_actions) >= 1, "At least one tool action expected"

    @pytest.mark.asyncio()
    async def test_collect_turns_has_read_and_bash_tools(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Review fixture uses Read and Bash tools."""
        turns, _result = await collect_turns(_event_aiter(events))

        tool_names = {a.name for t in turns for a in t.tool_actions}
        assert "Read" in tool_names
        assert "Bash" in tool_names

    @pytest.mark.asyncio()
    async def test_tool_results_paired_correctly(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Tool results are paired with their corresponding tool uses."""
        turns, _result = await collect_turns(_event_aiter(events))

        for turn in turns:
            for action in turn.tool_actions:
                if action.result_content is not None:
                    assert action.result_content != "", (
                        f"Tool {action.name} should have non-empty result"
                    )

    @pytest.mark.asyncio()
    async def test_structured_output_extracted(
        self, events: list[ClaudeEvent],
    ) -> None:
        """AC4: ClaudeResult.structured_output correctly extracted."""
        _turns, result = await collect_turns(_event_aiter(events))

        assert result.structured_output is not None
        assert isinstance(result.structured_output, dict)
        assert result.structured_output["verdict"] == "approve"
        assert "summary" in result.structured_output
        assert isinstance(result.structured_output["suggestions"], list)
        assert len(result.structured_output["suggestions"]) == 2

    @pytest.mark.asyncio()
    async def test_result_metadata(self, events: list[ClaudeEvent]) -> None:
        """Result contains cost, usage, session metadata."""
        _turns, result = await collect_turns(_event_aiter(events))

        assert result.cost_usd is not None and result.cost_usd > 0
        assert result.usage is not None
        assert result.usage["input_tokens"] > 0
        assert result.usage["output_tokens"] > 0
        assert result.session_id == "sess-review-fixture-001"
        assert result.num_turns == 3
        assert result.is_error is False

    @pytest.mark.asyncio()
    async def test_summary_extraction_findings(
        self, events: list[ClaudeEvent],
    ) -> None:
        """AC3: summary extraction produces non-empty findings."""
        turns, _result = await collect_turns(_event_aiter(events))
        summary = _extract_conversation_summary(turns)

        assert len(summary["findings"]) >= 2, "Should have multiple findings"
        # All findings should be non-empty strings
        for finding in summary["findings"]:
            assert isinstance(finding, str)
            assert len(finding) > 0

    @pytest.mark.asyncio()
    async def test_summary_extraction_actions(
        self, events: list[ClaudeEvent],
    ) -> None:
        """AC3: summary extraction produces non-empty actions_taken."""
        turns, _result = await collect_turns(_event_aiter(events))
        summary = _extract_conversation_summary(turns)

        assert len(summary["actions_taken"]) >= 2
        assert "Read" in summary["actions_taken"]
        assert "Bash" in summary["actions_taken"]

    @pytest.mark.asyncio()
    async def test_summary_extraction_conclusion(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Conclusion is the last non-empty text from turns."""
        turns, _result = await collect_turns(_event_aiter(events))
        summary = _extract_conversation_summary(turns)

        assert summary["conclusion"] != ""
        # Conclusion should be the last finding
        assert summary["conclusion"] == summary["findings"][-1]


# ---------------------------------------------------------------------------
# Execution session fixture tests
# ---------------------------------------------------------------------------


class TestExecutionFixture:
    """Tests using sdk_execution_session.json fixture."""

    @pytest.fixture()
    def raw_events(self) -> list[dict[str, Any]]:
        return _load_fixture("sdk_execution_session.json")

    @pytest.fixture()
    def events(self, raw_events: list[dict[str, Any]]) -> list[ClaudeEvent]:
        return _events_from_dicts(raw_events)

    @pytest.mark.asyncio()
    async def test_multi_turn_reconstruction(
        self, events: list[ClaudeEvent],
    ) -> None:
        """AC2: Execution session with 5 tool calls produces multiple turns."""
        turns, _result = await collect_turns(_event_aiter(events))

        assert len(turns) >= 3, "Execution fixture should have 3+ turns"

    @pytest.mark.asyncio()
    async def test_edit_and_bash_tools_present(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Execution fixture uses Read, Edit, and Bash tools."""
        turns, _result = await collect_turns(_event_aiter(events))

        tool_names = {a.name for t in turns for a in t.tool_actions}
        assert "Read" in tool_names
        assert "Edit" in tool_names
        assert "Bash" in tool_names

    @pytest.mark.asyncio()
    async def test_text_result_not_structured(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Execution session returns text result, not structured output."""
        _turns, result = await collect_turns(_event_aiter(events))

        assert result.result_text is not None
        assert len(result.result_text) > 0
        assert result.structured_output is None

    @pytest.mark.asyncio()
    async def test_summary_has_meaningful_content(
        self, events: list[ClaudeEvent],
    ) -> None:
        """AC3: Summary from execution session has findings and actions."""
        turns, _result = await collect_turns(_event_aiter(events))
        summary = _extract_conversation_summary(turns)

        assert len(summary["findings"]) >= 3
        assert len(summary["actions_taken"]) >= 3
        assert summary["conclusion"] != ""

    @pytest.mark.asyncio()
    async def test_failed_test_context_preserved(
        self, events: list[ClaudeEvent],
    ) -> None:
        """The Bash tool result containing test failure is preserved."""
        turns, _result = await collect_turns(_event_aiter(events))

        # Find the Bash action with test failure
        bash_results = [
            a.result_content
            for t in turns
            for a in t.tool_actions
            if a.name == "Bash" and a.result_content
        ]
        has_failure = any("FAILED" in r for r in bash_results)
        has_pass = any("passed" in r for r in bash_results)
        assert has_failure, "Should capture test failure output"
        assert has_pass, "Should capture test pass output"

    @pytest.mark.asyncio()
    async def test_result_cost_and_turns(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Result captures cost and turn count."""
        _turns, result = await collect_turns(_event_aiter(events))

        assert result.cost_usd is not None and result.cost_usd > 0
        assert result.num_turns == 5
        assert result.session_id == "sess-exec-fixture-002"


# ---------------------------------------------------------------------------
# Enrichment session fixture tests
# ---------------------------------------------------------------------------


class TestEnrichmentFixture:
    """Tests using sdk_enrichment_session.json fixture."""

    @pytest.fixture()
    def raw_events(self) -> list[dict[str, Any]]:
        return _load_fixture("sdk_enrichment_session.json")

    @pytest.fixture()
    def events(self, raw_events: list[dict[str, Any]]) -> list[ClaudeEvent]:
        return _events_from_dicts(raw_events)

    @pytest.mark.asyncio()
    async def test_single_tool_turn(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Enrichment fixture has a single tool call (Read) then result."""
        turns, _result = await collect_turns(_event_aiter(events))

        assert len(turns) >= 1
        all_actions = [a for t in turns for a in t.tool_actions]
        assert len(all_actions) >= 1
        assert all_actions[0].name == "Read"

    @pytest.mark.asyncio()
    async def test_structured_output_enrichment_fields(
        self, events: list[ClaudeEvent],
    ) -> None:
        """AC4: Enrichment structured_output has enriched_title, plan_steps, etc."""
        _turns, result = await collect_turns(_event_aiter(events))

        assert result.structured_output is not None
        so = result.structured_output
        assert "enriched_title" in so
        assert len(so["enriched_title"]) > 0
        assert "complexity" in so
        assert so["complexity"] in ("S", "M", "L")
        assert "plan_steps" in so
        assert isinstance(so["plan_steps"], list)
        assert len(so["plan_steps"]) >= 2
        assert "estimated_files" in so
        assert isinstance(so["estimated_files"], list)

    @pytest.mark.asyncio()
    async def test_enrichment_uses_haiku_model(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Enrichment sessions typically use the haiku model."""
        _turns, result = await collect_turns(_event_aiter(events))

        assert result.model is not None
        assert "haiku" in result.model

    @pytest.mark.asyncio()
    async def test_summary_from_enrichment(
        self, events: list[ClaudeEvent],
    ) -> None:
        """AC3: Summary extraction works for short enrichment conversations."""
        turns, _result = await collect_turns(_event_aiter(events))
        summary = _extract_conversation_summary(turns)

        assert len(summary["findings"]) >= 1
        assert len(summary["actions_taken"]) >= 1
        assert summary["conclusion"] != ""

    @pytest.mark.asyncio()
    async def test_enrichment_low_cost(
        self, events: list[ClaudeEvent],
    ) -> None:
        """Enrichment sessions are cheap (haiku model, short conversation)."""
        _turns, result = await collect_turns(_event_aiter(events))

        assert result.cost_usd is not None
        assert result.cost_usd < 0.05


# ---------------------------------------------------------------------------
# Cross-fixture tests
# ---------------------------------------------------------------------------


class TestCrossFixture:
    """Tests that verify properties across all fixtures."""

    @pytest.fixture(
        params=[
            "sdk_review_session.json",
            "sdk_execution_session.json",
            "sdk_enrichment_session.json",
        ],
    )
    def fixture_events(self, request: pytest.FixtureRequest) -> list[ClaudeEvent]:
        raw = _load_fixture(request.param)
        return _events_from_dicts(raw)

    @pytest.mark.asyncio()
    async def test_all_fixtures_produce_nonempty_turns(
        self, fixture_events: list[ClaudeEvent],
    ) -> None:
        """AC2: Every fixture produces at least one turn with content."""
        turns, _result = await collect_turns(_event_aiter(fixture_events))

        assert len(turns) >= 1
        has_content = any(
            t.text.strip() or t.tool_actions for t in turns
        )
        assert has_content, "At least one turn should have text or tool actions"

    @pytest.mark.asyncio()
    async def test_all_fixtures_produce_valid_result(
        self, fixture_events: list[ClaudeEvent],
    ) -> None:
        """Every fixture has a valid ClaudeResult with cost and session info."""
        _turns, result = await collect_turns(_event_aiter(fixture_events))

        assert result.is_error is False
        assert result.cost_usd is not None
        assert result.session_id is not None

    @pytest.mark.asyncio()
    async def test_all_fixtures_summary_has_findings(
        self, fixture_events: list[ClaudeEvent],
    ) -> None:
        """AC3: Every fixture produces a summary with non-empty findings."""
        turns, _result = await collect_turns(_event_aiter(fixture_events))
        summary = _extract_conversation_summary(turns)

        assert len(summary["findings"]) >= 1
        assert all(isinstance(f, str) and len(f) > 0 for f in summary["findings"])

    @pytest.mark.asyncio()
    async def test_all_fixtures_summary_has_actions(
        self, fixture_events: list[ClaudeEvent],
    ) -> None:
        """AC3: Every fixture produces a summary with non-empty actions."""
        turns, _result = await collect_turns(_event_aiter(fixture_events))
        summary = _extract_conversation_summary(turns)

        assert len(summary["actions_taken"]) >= 1

    @pytest.mark.asyncio()
    async def test_all_fixtures_summary_has_conclusion(
        self, fixture_events: list[ClaudeEvent],
    ) -> None:
        """Every fixture summary has a non-empty conclusion."""
        turns, _result = await collect_turns(_event_aiter(fixture_events))
        summary = _extract_conversation_summary(turns)

        assert summary["conclusion"] != ""

    @pytest.mark.asyncio()
    async def test_no_live_cli_calls(
        self, fixture_events: list[ClaudeEvent],
    ) -> None:
        """AC5: Tests are deterministic -- events come from fixture files only."""
        # This test documents the intent: all events are pre-built from JSON.
        # If collect_turns() tried to make external calls it would fail here
        # because no SDK is mocked.
        turns, result = await collect_turns(_event_aiter(fixture_events))
        assert isinstance(turns, list)
        assert isinstance(result, ClaudeResult)
