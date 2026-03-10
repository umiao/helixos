"""Tests for src/sdk_adapter.py -- Claude Agent SDK adapter layer.

All tests mock ``claude_agent_sdk.query()`` so no real SDK calls are made.
"""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.sdk_adapter import (
    AssistantTurn,
    ClaudeEvent,
    ClaudeEventType,
    ClaudeResult,
    QueryOptions,
    ToolAction,
    _build_sdk_options,
    _translate_message,
    collect_turns,
    run_claude_query,
)


def _ensure_fake_sdk() -> types.ModuleType:
    """Inject a fake ``claude_agent_sdk`` into sys.modules if not installed."""
    if "claude_agent_sdk" not in sys.modules:
        mod = types.ModuleType("claude_agent_sdk")

        class _FakeClaudeAgentOptions:
            def __init__(self, **kwargs: Any) -> None:
                self._kwargs = kwargs

        mod.ClaudeAgentOptions = _FakeClaudeAgentOptions  # type: ignore[attr-defined]
        mod.query = None  # type: ignore[attr-defined]
        sys.modules["claude_agent_sdk"] = mod
    return sys.modules["claude_agent_sdk"]


# ---------------------------------------------------------------------------
# Fake SDK message types (mirror real SDK dataclasses)
# ---------------------------------------------------------------------------


@dataclass
class SystemMessage:
    """Mimics ``claude_agent_sdk.SystemMessage``."""

    subtype: str = "init"
    data: dict[str, Any] = field(default_factory=lambda: {"session_id": "sess-123"})


@dataclass
class TextBlock:
    """Mimics ``claude_agent_sdk.TextBlock``."""

    text: str = "Hello, world!"


@dataclass
class ThinkingBlock:
    """Mimics ``claude_agent_sdk.ThinkingBlock``."""

    thinking: str = "Let me think..."
    signature: str = "sig"


@dataclass
class ToolUseBlock:
    """Mimics ``claude_agent_sdk.ToolUseBlock``."""

    id: str = "tool-1"
    name: str = "Read"
    input: dict[str, Any] = field(default_factory=lambda: {"file_path": "/foo.py"})


@dataclass
class ToolResultBlock:
    """Mimics ``claude_agent_sdk.ToolResultBlock``."""

    tool_use_id: str = "tool-1"
    content: str | list[dict[str, Any]] | None = "file contents here"
    is_error: bool | None = False


@dataclass
class AssistantMessageError:
    """Mimics an error on AssistantMessage."""

    type: str = "overloaded"
    message: str = "Rate limited"


@dataclass
class AssistantMessage:
    """Mimics ``claude_agent_sdk.AssistantMessage``."""

    content: list[Any] = field(default_factory=list)
    model: str = "claude-sonnet-4-5"
    parent_tool_use_id: str | None = None
    error: AssistantMessageError | None = None


@dataclass
class UserMessage:
    """Mimics ``claude_agent_sdk.UserMessage``."""

    content: str | list[Any] = ""
    uuid: str | None = None
    parent_tool_use_id: str | None = None
    tool_use_result: dict[str, Any] | None = None


@dataclass
class ResultMessage:
    """Mimics ``claude_agent_sdk.ResultMessage``."""

    subtype: str = "result"
    duration_ms: int = 5000
    duration_api_ms: int = 4500
    is_error: bool = False
    num_turns: int = 3
    session_id: str = "sess-123"
    total_cost_usd: float | None = 0.05
    usage: dict[str, Any] | None = field(
        default_factory=lambda: {"input_tokens": 1000, "output_tokens": 500}
    )
    result: str | None = "Task completed successfully."
    structured_output: Any | None = None


# ---------------------------------------------------------------------------
# Helper: async iterator from list
# ---------------------------------------------------------------------------


async def _aiter_from(items: list[Any]) -> AsyncIterator[Any]:
    """Yield items as an async iterator."""
    for item in items:
        yield item


async def _event_aiter(events: list[ClaudeEvent]) -> AsyncIterator[ClaudeEvent]:
    """Yield ClaudeEvents as an async iterator."""
    for ev in events:
        yield ev


# ---------------------------------------------------------------------------
# Tests: _translate_message
# ---------------------------------------------------------------------------


class TestTranslateMessage:
    """Test _translate_message for each SDK message type."""

    def test_system_message(self) -> None:
        """SystemMessage -> INIT event with session_id."""
        msg = SystemMessage(data={"session_id": "abc-456"})
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.INIT
        assert events[0].session_id == "abc-456"

    def test_system_message_no_data(self) -> None:
        """SystemMessage with empty data still yields INIT."""
        msg = SystemMessage(data={})
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.INIT
        assert events[0].session_id is None

    def test_assistant_message_text(self) -> None:
        """AssistantMessage with TextBlock -> TEXT event."""
        msg = AssistantMessage(
            content=[TextBlock(text="Hello!")],
            model="claude-haiku-4-5-20251001",
        )
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.TEXT
        assert events[0].text == "Hello!"
        assert events[0].model == "claude-haiku-4-5-20251001"

    def test_assistant_message_tool_use(self) -> None:
        """AssistantMessage with ToolUseBlock -> TOOL_USE event."""
        msg = AssistantMessage(
            content=[
                ToolUseBlock(id="tu-99", name="Bash", input={"command": "ls"})
            ],
        )
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.TOOL_USE
        assert events[0].tool_name == "Bash"
        assert events[0].tool_input == {"command": "ls"}
        assert events[0].tool_use_id == "tu-99"

    def test_assistant_message_tool_result_in_content(self) -> None:
        """AssistantMessage with ToolResultBlock -> TOOL_RESULT event."""
        msg = AssistantMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tu-99", content="output text", is_error=False
                )
            ],
        )
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.TOOL_RESULT
        assert events[0].tool_result_content == "output text"
        assert events[0].tool_result_for_id == "tu-99"
        assert events[0].tool_is_error is False

    def test_assistant_message_tool_result_list_content(self) -> None:
        """ToolResultBlock with list content is joined."""
        msg = AssistantMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tu-1",
                    content=[{"text": "line1"}, {"text": "line2"}],
                )
            ],
        )
        events = _translate_message(msg)

        assert events[0].tool_result_content == "line1\nline2"

    def test_assistant_message_mixed_content(self) -> None:
        """AssistantMessage with text + tool_use yields multiple events."""
        msg = AssistantMessage(
            content=[
                TextBlock(text="I'll read the file."),
                ToolUseBlock(id="tu-5", name="Read", input={"file_path": "/a.py"}),
            ],
            model="claude-sonnet-4-5",
        )
        events = _translate_message(msg)

        assert len(events) == 2
        assert events[0].type == ClaudeEventType.TEXT
        assert events[1].type == ClaudeEventType.TOOL_USE

    def test_assistant_message_thinking_emitted(self) -> None:
        """ThinkingBlock is emitted as THINKING event."""
        msg = AssistantMessage(
            content=[ThinkingBlock(), TextBlock(text="result")],
        )
        events = _translate_message(msg)

        assert len(events) == 2
        assert events[0].type == ClaudeEventType.THINKING
        assert events[0].thinking == "Let me think..."
        assert events[1].type == ClaudeEventType.TEXT

    def test_assistant_message_empty_thinking_skipped(self) -> None:
        """ThinkingBlock with empty text is skipped."""
        msg = AssistantMessage(
            content=[ThinkingBlock(thinking=""), TextBlock(text="result")],
        )
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.TEXT

    def test_assistant_message_error(self) -> None:
        """AssistantMessage with error -> ERROR event."""
        msg = AssistantMessage(
            error=AssistantMessageError(type="overloaded", message="Rate limited"),
            model="claude-sonnet-4-5",
        )
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.ERROR
        assert "Rate limited" in (events[0].error_message or "")

    def test_user_message_with_tool_result(self) -> None:
        """UserMessage with ToolResultBlock -> TOOL_RESULT event."""
        msg = UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tu-7", content="file read output", is_error=False
                )
            ],
        )
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.TOOL_RESULT
        assert events[0].tool_result_for_id == "tu-7"

    def test_user_message_text_only(self) -> None:
        """UserMessage with plain string content yields no events."""
        msg = UserMessage(content="user says hello")
        events = _translate_message(msg)

        assert len(events) == 0

    def test_result_message_success(self) -> None:
        """ResultMessage (success) -> RESULT event."""
        msg = ResultMessage(
            result="All done.",
            structured_output={"key": "value"},
            total_cost_usd=0.12,
            usage={"input_tokens": 2000, "output_tokens": 800},
            duration_ms=10000,
            num_turns=5,
            session_id="sess-x",
        )
        events = _translate_message(msg)

        assert len(events) == 1
        ev = events[0]
        assert ev.type == ClaudeEventType.RESULT
        assert ev.result_text == "All done."
        assert ev.structured_output == {"key": "value"}
        assert ev.cost_usd == 0.12
        assert ev.usage == {"input_tokens": 2000, "output_tokens": 800}
        assert ev.duration_ms == 10000
        assert ev.num_turns == 5
        assert ev.session_id == "sess-x"

    def test_result_message_error(self) -> None:
        """ResultMessage with is_error -> ERROR event."""
        msg = ResultMessage(
            is_error=True,
            result="Something went wrong",
            total_cost_usd=0.01,
        )
        events = _translate_message(msg)

        assert len(events) == 1
        assert events[0].type == ClaudeEventType.ERROR
        assert events[0].error_message == "Something went wrong"
        assert events[0].cost_usd == 0.01

    def test_unknown_message_type(self) -> None:
        """Unknown message types yield no events (silent skip)."""

        @dataclass
        class FakeUnknown:
            pass

        events = _translate_message(FakeUnknown())
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Tests: collect_turns
# ---------------------------------------------------------------------------


class TestCollectTurns:
    """Test collect_turns() for conversation reconstruction."""

    @pytest.mark.asyncio
    async def test_simple_text_turn(self) -> None:
        """Single text event -> one turn with text, no tools."""
        events = [
            ClaudeEvent(type=ClaudeEventType.INIT, session_id="s1"),
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Hello!", model="haiku"),
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                result_text="Hello!",
                cost_usd=0.01,
            ),
        ]

        turns, result = await collect_turns(_event_aiter(events))

        assert len(turns) == 1
        assert turns[0].text == "Hello!"
        assert turns[0].model == "haiku"
        assert turns[0].tool_actions == []
        assert result.result_text == "Hello!"
        assert result.cost_usd == 0.01
        assert result.session_id == "s1"

    @pytest.mark.asyncio
    async def test_tool_use_paired_with_result(self) -> None:
        """TOOL_USE + TOOL_RESULT are paired into a ToolAction."""
        events = [
            ClaudeEvent(
                type=ClaudeEventType.TEXT,
                text="Let me read the file.",
                model="sonnet",
            ),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_USE,
                tool_name="Read",
                tool_input={"file_path": "/a.py"},
                tool_use_id="tu-1",
                model="sonnet",
            ),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_RESULT,
                tool_result_content="print('hi')",
                tool_is_error=False,
                tool_result_for_id="tu-1",
            ),
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                result_text="Done",
            ),
        ]

        turns, result = await collect_turns(_event_aiter(events))

        assert len(turns) == 1
        turn = turns[0]
        assert turn.text == "Let me read the file."
        assert len(turn.tool_actions) == 1
        assert turn.tool_actions[0].name == "Read"
        assert turn.tool_actions[0].tool_use_id == "tu-1"
        assert turn.tool_actions[0].result_content == "print('hi')"
        assert turn.tool_actions[0].is_error is False

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self) -> None:
        """Multiple turns with tool_result boundaries."""
        events = [
            # Turn 1: text + tool
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Turn 1 text", model="s"),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_USE,
                tool_name="Bash",
                tool_input={"command": "ls"},
                tool_use_id="tu-a",
            ),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_RESULT,
                tool_result_content="file1.py\nfile2.py",
                tool_result_for_id="tu-a",
            ),
            # Turn 2: text + tool
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Turn 2 text", model="s"),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_USE,
                tool_name="Read",
                tool_input={"file_path": "file1.py"},
                tool_use_id="tu-b",
            ),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_RESULT,
                tool_result_content="content",
                tool_result_for_id="tu-b",
            ),
            # Final result
            ClaudeEvent(
                type=ClaudeEventType.RESULT,
                result_text="All done",
                cost_usd=0.10,
                num_turns=2,
            ),
        ]

        turns, result = await collect_turns(_event_aiter(events))

        assert len(turns) == 2
        assert turns[0].text == "Turn 1 text"
        assert turns[0].tool_actions[0].name == "Bash"
        assert turns[1].text == "Turn 2 text"
        assert turns[1].tool_actions[0].name == "Read"
        assert result.num_turns == 2
        assert result.cost_usd == 0.10

    @pytest.mark.asyncio
    async def test_tool_result_error(self) -> None:
        """Tool result with is_error=True is reflected in ToolAction."""
        events = [
            ClaudeEvent(
                type=ClaudeEventType.TOOL_USE,
                tool_name="Bash",
                tool_input={"command": "bad-cmd"},
                tool_use_id="tu-err",
            ),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_RESULT,
                tool_result_content="command not found",
                tool_is_error=True,
                tool_result_for_id="tu-err",
            ),
            ClaudeEvent(type=ClaudeEventType.RESULT, result_text="Failed"),
        ]

        turns, result = await collect_turns(_event_aiter(events))

        assert len(turns) == 1
        assert turns[0].tool_actions[0].is_error is True
        assert turns[0].tool_actions[0].result_content == "command not found"

    @pytest.mark.asyncio
    async def test_error_result(self) -> None:
        """ERROR event sets result.is_error."""
        events = [
            ClaudeEvent(
                type=ClaudeEventType.ERROR,
                error_message="Budget exceeded",
                cost_usd=1.50,
            ),
        ]

        turns, result = await collect_turns(_event_aiter(events))

        assert len(turns) == 0
        assert result.is_error is True
        assert result.result_text == "Budget exceeded"
        assert result.cost_usd == 1.50

    @pytest.mark.asyncio
    async def test_multiple_text_events_concatenated(self) -> None:
        """Multiple TEXT events in same turn are concatenated."""
        events = [
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Part 1 "),
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Part 2"),
            ClaudeEvent(type=ClaudeEventType.RESULT, result_text="Part 1 Part 2"),
        ]

        turns, _ = await collect_turns(_event_aiter(events))

        assert len(turns) == 1
        assert turns[0].text == "Part 1 Part 2"

    @pytest.mark.asyncio
    async def test_empty_stream(self) -> None:
        """Empty event stream -> no turns, default result."""
        turns, result = await collect_turns(_event_aiter([]))

        assert len(turns) == 0
        assert result.is_error is False
        assert result.result_text is None

    @pytest.mark.asyncio
    async def test_unmatched_tool_result(self) -> None:
        """Tool result with no matching tool_use doesn't crash."""
        events = [
            ClaudeEvent(type=ClaudeEventType.TEXT, text="Hi"),
            ClaudeEvent(
                type=ClaudeEventType.TOOL_RESULT,
                tool_result_content="orphaned result",
                tool_result_for_id="nonexistent-id",
            ),
            ClaudeEvent(type=ClaudeEventType.RESULT, result_text="Done"),
        ]

        turns, result = await collect_turns(_event_aiter(events))

        # Should still produce a turn without crashing
        assert len(turns) == 1
        assert result.result_text == "Done"


# ---------------------------------------------------------------------------
# Tests: run_claude_query (integration with mocked SDK)
# ---------------------------------------------------------------------------


class TestRunClaudeQuery:
    """Test run_claude_query() with mocked SDK query()."""

    @pytest.mark.asyncio
    async def test_basic_query_translation(self) -> None:
        """Verify event translation from fake SDK messages."""
        fake_messages = [
            SystemMessage(data={"session_id": "s-test"}),
            AssistantMessage(
                content=[TextBlock(text="Answer")],
                model="claude-haiku-4-5-20251001",
            ),
            ResultMessage(
                result="Answer",
                total_cost_usd=0.001,
                duration_ms=500,
                num_turns=1,
                session_id="s-test",
            ),
        ]

        # Translate messages directly (no real SDK import needed)
        events: list[ClaudeEvent] = []
        for msg in fake_messages:
            events.extend(_translate_message(msg))

        assert len(events) == 3
        assert events[0].type == ClaudeEventType.INIT
        assert events[0].session_id == "s-test"
        assert events[1].type == ClaudeEventType.TEXT
        assert events[1].text == "Answer"
        assert events[2].type == ClaudeEventType.RESULT
        assert events[2].cost_usd == 0.001

    @pytest.mark.asyncio
    async def test_query_with_tool_cycle(self) -> None:
        """Full tool-use cycle: text -> tool_use -> tool_result -> result."""
        fake_messages = [
            SystemMessage(),
            AssistantMessage(
                content=[
                    TextBlock(text="Reading file..."),
                    ToolUseBlock(id="tu-x", name="Read", input={"file_path": "/x"}),
                ],
            ),
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="tu-x", content="file data", is_error=False
                    )
                ]
            ),
            AssistantMessage(
                content=[TextBlock(text="The file contains data.")],
            ),
            ResultMessage(result="The file contains data.", num_turns=2),
        ]

        events: list[ClaudeEvent] = []
        for msg in fake_messages:
            events.extend(_translate_message(msg))

        turns, result = await collect_turns(_event_aiter(events))

        assert len(turns) == 2
        assert turns[0].text == "Reading file..."
        assert turns[0].tool_actions[0].name == "Read"
        assert turns[0].tool_actions[0].result_content == "file data"
        assert turns[1].text == "The file contains data."
        assert result.result_text == "The file contains data."

    @pytest.mark.asyncio
    async def test_run_claude_query_with_mock(self) -> None:
        """Verify run_claude_query() delegates to SDK and translates."""
        fake_messages = [
            SystemMessage(data={"session_id": "s-mock"}),
            AssistantMessage(
                content=[TextBlock(text="Mocked answer")],
                model="claude-sonnet-4-5",
            ),
            ResultMessage(
                result="Mocked answer",
                total_cost_usd=0.02,
                session_id="s-mock",
            ),
        ]

        fake_mod = _ensure_fake_sdk()
        fake_mod.query = lambda **kw: _aiter_from(fake_messages)  # type: ignore[attr-defined]

        events: list[ClaudeEvent] = []
        async for ev in run_claude_query("test prompt"):
            events.append(ev)

        assert len(events) == 3
        assert events[0].type == ClaudeEventType.INIT
        assert events[1].type == ClaudeEventType.TEXT
        assert events[1].text == "Mocked answer"
        assert events[2].type == ClaudeEventType.RESULT


# ---------------------------------------------------------------------------
# Tests: QueryOptions -> SDK options mapping
# ---------------------------------------------------------------------------


class TestBuildSdkOptions:
    """Test _build_sdk_options mapping."""

    def test_none_options(self) -> None:
        """None options -> default ClaudeAgentOptions."""
        fake_mod = _ensure_fake_sdk()
        captured: list[dict[str, Any]] = []

        class CapturingOpts:
            def __init__(self, **kwargs: Any) -> None:
                captured.append(kwargs)

        original = fake_mod.ClaudeAgentOptions  # type: ignore[union-attr]
        fake_mod.ClaudeAgentOptions = CapturingOpts  # type: ignore[attr-defined]
        try:
            result = _build_sdk_options(None)
            assert isinstance(result, CapturingOpts)
            assert captured[0] == {}  # no kwargs for None options
        finally:
            fake_mod.ClaudeAgentOptions = original  # type: ignore[attr-defined]

    def test_full_options(self) -> None:
        """All fields are passed through to ClaudeAgentOptions."""
        opts = QueryOptions(
            model="claude-haiku-4-5-20251001",
            system_prompt="You are a reviewer.",
            allowed_tools=["Read", "Grep"],
            permission_mode="acceptEdits",
            add_dirs=["/extra"],
            max_budget_usd=0.50,
            max_turns=10,
            cwd="/project",
            env={"MY_VAR": "1"},
        )

        fake_mod = _ensure_fake_sdk()
        captured: list[dict[str, Any]] = []

        class CapturingOpts:
            def __init__(self, **kwargs: Any) -> None:
                captured.append(kwargs)

        original = fake_mod.ClaudeAgentOptions  # type: ignore[union-attr]
        fake_mod.ClaudeAgentOptions = CapturingOpts  # type: ignore[attr-defined]
        try:
            _build_sdk_options(opts)
        finally:
            fake_mod.ClaudeAgentOptions = original  # type: ignore[attr-defined]

        kw = captured[0]
        assert kw["model"] == "claude-haiku-4-5-20251001"
        assert kw["system_prompt"] == "You are a reviewer."
        assert kw["allowed_tools"] == ["Read", "Grep"]
        assert kw["permission_mode"] == "acceptEdits"
        assert kw["add_dirs"] == ["/extra"]
        assert kw["max_budget_usd"] == 0.50
        assert kw["max_turns"] == 10
        assert kw["cwd"] == "/project"
        assert kw["env"] == {"MY_VAR": "1"}
        assert kw["output_format"] is None

    def test_json_schema_string(self) -> None:
        """json_schema string is parsed into output_format."""
        opts = QueryOptions(
            json_schema='{"type": "object", "properties": {"x": {"type": "string"}}}',
        )

        fake_mod = _ensure_fake_sdk()
        captured: list[dict[str, Any]] = []

        class CapturingOpts:
            def __init__(self, **kwargs: Any) -> None:
                captured.append(kwargs)

        original = fake_mod.ClaudeAgentOptions  # type: ignore[union-attr]
        fake_mod.ClaudeAgentOptions = CapturingOpts  # type: ignore[attr-defined]
        try:
            _build_sdk_options(opts)
        finally:
            fake_mod.ClaudeAgentOptions = original  # type: ignore[attr-defined]

        assert captured[0]["output_format"] == {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
        }


# ---------------------------------------------------------------------------
# Tests: Pydantic model validation
# ---------------------------------------------------------------------------


class TestModels:
    """Verify Pydantic model contracts."""

    def test_claude_event_defaults(self) -> None:
        """ClaudeEvent with only type has all optional fields as None."""
        ev = ClaudeEvent(type=ClaudeEventType.INIT)
        assert ev.session_id is None
        assert ev.text is None
        assert ev.tool_name is None

    def test_tool_action_defaults(self) -> None:
        """ToolAction has sensible defaults."""
        ta = ToolAction(tool_use_id="t1", name="Bash")
        assert ta.input == {}
        assert ta.result_content is None
        assert ta.is_error is False

    def test_assistant_turn_defaults(self) -> None:
        """AssistantTurn starts empty."""
        turn = AssistantTurn()
        assert turn.text == ""
        assert turn.tool_actions == []
        assert turn.model is None

    def test_claude_result_defaults(self) -> None:
        """ClaudeResult defaults to non-error."""
        r = ClaudeResult()
        assert r.is_error is False
        assert r.result_text is None
        assert r.structured_output is None

    def test_query_options_defaults(self) -> None:
        """QueryOptions has empty defaults."""
        opts = QueryOptions()
        assert opts.model is None
        assert opts.allowed_tools == []
        assert opts.add_dirs == []
        assert opts.env == {}
        assert opts.setting_sources is None

    def test_query_options_setting_sources(self) -> None:
        """QueryOptions accepts setting_sources."""
        opts = QueryOptions(setting_sources=[])
        assert opts.setting_sources == []

        opts2 = QueryOptions(setting_sources=["project"])
        assert opts2.setting_sources == ["project"]

    def test_build_sdk_options_passes_setting_sources(self) -> None:
        """_build_sdk_options passes setting_sources to ClaudeAgentOptions."""
        _ensure_fake_sdk()
        opts = QueryOptions(setting_sources=[])
        sdk_opts = _build_sdk_options(opts)
        assert sdk_opts._kwargs["setting_sources"] == []

    def test_build_sdk_options_omits_setting_sources_when_none(self) -> None:
        """_build_sdk_options omits setting_sources when None (SDK default)."""
        _ensure_fake_sdk()
        opts = QueryOptions()
        sdk_opts = _build_sdk_options(opts)
        # When setting_sources is None, it should not be passed, so SDK uses
        # its own default (which loads all setting sources).
        assert "setting_sources" not in sdk_opts._kwargs
