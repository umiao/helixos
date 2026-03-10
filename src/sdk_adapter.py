"""Claude Agent SDK adapter layer.

Wraps ``claude_agent_sdk.query()`` into typed Pydantic events and provides
utilities for conversation-turn reconstruction.  The adapter returns an async
iterator -- **no callbacks**.  JSONL logging is the consumer's responsibility.

SDK flag compatibility spike results (documented inline):
  - ``permission_mode``: Maps to ``PermissionMode`` literal
    ("default" | "acceptEdits" | "plan" | "bypassPermissions").
    Set via ``ClaudeAgentOptions.permission_mode``.
  - ``add_dirs``: List of ``str | Path`` in ``ClaudeAgentOptions.add_dirs``.
    Provides additional project directories to the agent.
  - ``max_budget_usd``: ``float | None`` in ``ClaudeAgentOptions.max_budget_usd``.
    Caps total cost; the SDK enforces it per-session.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed event models
# ---------------------------------------------------------------------------


class ClaudeEventType(StrEnum):
    """Discriminator for ``ClaudeEvent.type``."""

    INIT = "init"
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    RESULT = "result"
    ERROR = "error"


class ClaudeEvent(BaseModel):
    """A single event emitted by ``run_claude_query()``.

    The ``type`` field determines which optional payload fields are populated.
    """

    type: ClaudeEventType

    # INIT
    session_id: str | None = None

    # TEXT -- assistant text fragment
    text: str | None = None

    # THINKING -- agent reasoning block
    thinking: str | None = None

    # TOOL_USE
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_use_id: str | None = None

    # TOOL_RESULT
    tool_result_content: str | None = None
    tool_is_error: bool | None = None
    tool_result_for_id: str | None = None  # matches tool_use_id

    # RESULT
    result_text: str | None = None
    structured_output: Any | None = None
    cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    duration_ms: int | None = None
    model: str | None = None
    num_turns: int | None = None

    # ERROR
    error_message: str | None = None


class ToolAction(BaseModel):
    """A tool invocation paired with its result."""

    tool_use_id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    result_content: str | None = None
    is_error: bool = False


class AssistantTurn(BaseModel):
    """A complete assistant turn: text + tool actions."""

    text: str = ""
    tool_actions: list[ToolAction] = Field(default_factory=list)
    model: str | None = None


class ClaudeResult(BaseModel):
    """Final aggregated result from a ``run_claude_query()`` session."""

    result_text: str | None = None
    structured_output: Any | None = None
    cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    duration_ms: int | None = None
    model: str | None = None
    session_id: str | None = None
    num_turns: int | None = None
    is_error: bool = False


# ---------------------------------------------------------------------------
# Query options
# ---------------------------------------------------------------------------

PermissionMode = Literal["default", "acceptEdits", "plan", "bypassPermissions"]


SettingSource = Literal["user", "project", "local"]


class QueryOptions(BaseModel):
    """Options for ``run_claude_query()``."""

    model: str | None = None
    system_prompt: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    permission_mode: PermissionMode | None = None
    add_dirs: list[str] = Field(default_factory=list)
    max_budget_usd: float | None = None
    output_format: dict[str, Any] | None = None
    json_schema: str | None = None
    max_turns: int | None = None
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    setting_sources: list[SettingSource] | None = None


# ---------------------------------------------------------------------------
# Adapter core
# ---------------------------------------------------------------------------


def _build_sdk_options(options: QueryOptions | None) -> Any:
    """Convert ``QueryOptions`` to ``claude_agent_sdk.ClaudeAgentOptions``."""
    from claude_agent_sdk import ClaudeAgentOptions  # type: ignore[import-untyped]

    if options is None:
        return ClaudeAgentOptions()

    output_fmt = options.output_format
    if output_fmt is None and options.json_schema is not None:
        import json

        output_fmt = {
            "type": "json_schema",
            "schema": json.loads(options.json_schema)
            if isinstance(options.json_schema, str)
            else options.json_schema,
        }

    kwargs: dict[str, Any] = dict(
        model=options.model,
        system_prompt=options.system_prompt,
        allowed_tools=options.allowed_tools,
        permission_mode=options.permission_mode,
        add_dirs=options.add_dirs,
        max_budget_usd=options.max_budget_usd,
        output_format=output_fmt,
        max_turns=options.max_turns,
        cwd=options.cwd,
        env=options.env,
    )
    if options.setting_sources is not None:
        kwargs["setting_sources"] = options.setting_sources

    return ClaudeAgentOptions(**kwargs)


async def run_claude_query(
    prompt: str,
    options: QueryOptions | None = None,
) -> AsyncIterator[ClaudeEvent]:
    """Run a Claude query via the Agent SDK and yield typed events.

    This is the single integration point for all Claude interactions.
    Consumers iterate over ``ClaudeEvent`` objects and handle logging,
    persistence, and UI updates themselves.

    Args:
        prompt: The user prompt to send.
        options: Optional query configuration.

    Yields:
        ``ClaudeEvent`` instances in order: INIT, then TEXT/TOOL_USE/TOOL_RESULT
        interleaved, then RESULT (or ERROR).
    """
    from claude_agent_sdk import query as sdk_query  # type: ignore[import-untyped]

    sdk_options = _build_sdk_options(options)

    async for message in sdk_query(prompt=prompt, options=sdk_options):
        for event in _translate_message(message):
            yield event


def _translate_message(message: Any) -> list[ClaudeEvent]:
    """Translate a single SDK message into zero or more ``ClaudeEvent``s."""
    cls_name = type(message).__name__
    events: list[ClaudeEvent] = []

    if cls_name == "SystemMessage":
        data = getattr(message, "data", {}) or {}
        events.append(
            ClaudeEvent(
                type=ClaudeEventType.INIT,
                session_id=data.get("session_id"),
            )
        )

    elif cls_name == "AssistantMessage":
        error = getattr(message, "error", None)
        if error is not None:
            events.append(
                ClaudeEvent(
                    type=ClaudeEventType.ERROR,
                    error_message=str(error),
                    model=getattr(message, "model", None),
                )
            )
            return events

        content_blocks = getattr(message, "content", []) or []
        model = getattr(message, "model", None)
        for block in content_blocks:
            block_cls = type(block).__name__

            if block_cls == "TextBlock":
                events.append(
                    ClaudeEvent(
                        type=ClaudeEventType.TEXT,
                        text=getattr(block, "text", ""),
                        model=model,
                    )
                )

            elif block_cls == "ToolUseBlock":
                events.append(
                    ClaudeEvent(
                        type=ClaudeEventType.TOOL_USE,
                        tool_name=getattr(block, "name", ""),
                        tool_input=getattr(block, "input", {}),
                        tool_use_id=getattr(block, "id", ""),
                        model=model,
                    )
                )

            elif block_cls == "ToolResultBlock":
                raw_content = getattr(block, "content", None)
                content_str: str | None = None
                if isinstance(raw_content, str):
                    content_str = raw_content
                elif isinstance(raw_content, list):
                    # List of content parts -- concatenate text parts
                    parts = []
                    for part in raw_content:
                        if isinstance(part, dict):
                            parts.append(part.get("text", ""))
                        else:
                            parts.append(str(part))
                    content_str = "\n".join(parts)
                elif raw_content is not None:
                    content_str = str(raw_content)

                events.append(
                    ClaudeEvent(
                        type=ClaudeEventType.TOOL_RESULT,
                        tool_result_content=content_str,
                        tool_is_error=getattr(block, "is_error", None),
                        tool_result_for_id=getattr(block, "tool_use_id", None),
                    )
                )

            elif block_cls == "ThinkingBlock":
                thinking_text = getattr(block, "thinking", None)
                if thinking_text:
                    events.append(
                        ClaudeEvent(
                            type=ClaudeEventType.THINKING,
                            thinking=thinking_text,
                            model=model,
                        )
                    )

    elif cls_name == "UserMessage":
        # User messages contain tool results injected by the SDK.
        content = getattr(message, "content", None)
        if isinstance(content, list):
            for block in content:
                block_cls = type(block).__name__
                if block_cls == "ToolResultBlock":
                    raw_content = getattr(block, "content", None)
                    content_str = None
                    if isinstance(raw_content, str):
                        content_str = raw_content
                    elif isinstance(raw_content, list):
                        parts = []
                        for part in raw_content:
                            if isinstance(part, dict):
                                parts.append(part.get("text", ""))
                            else:
                                parts.append(str(part))
                        content_str = "\n".join(parts)
                    elif raw_content is not None:
                        content_str = str(raw_content)

                    events.append(
                        ClaudeEvent(
                            type=ClaudeEventType.TOOL_RESULT,
                            tool_result_content=content_str,
                            tool_is_error=getattr(block, "is_error", None),
                            tool_result_for_id=getattr(
                                block, "tool_use_id", None
                            ),
                        )
                    )

    elif cls_name == "ResultMessage":
        is_error = getattr(message, "is_error", False)
        if is_error:
            events.append(
                ClaudeEvent(
                    type=ClaudeEventType.ERROR,
                    error_message=getattr(message, "result", None),
                    cost_usd=getattr(message, "total_cost_usd", None),
                    usage=getattr(message, "usage", None),
                    duration_ms=getattr(message, "duration_ms", None),
                )
            )
        else:
            events.append(
                ClaudeEvent(
                    type=ClaudeEventType.RESULT,
                    result_text=getattr(message, "result", None),
                    structured_output=getattr(
                        message, "structured_output", None
                    ),
                    cost_usd=getattr(message, "total_cost_usd", None),
                    usage=getattr(message, "usage", None),
                    duration_ms=getattr(message, "duration_ms", None),
                    num_turns=getattr(message, "num_turns", None),
                    session_id=getattr(message, "session_id", None),
                )
            )

    # StreamEvent -- intentionally ignored (we use complete messages only)
    # Unknown message types -- silently skipped

    return events


# ---------------------------------------------------------------------------
# Turn reconstruction
# ---------------------------------------------------------------------------


async def collect_turns(
    events: AsyncIterator[ClaudeEvent],
) -> tuple[list[AssistantTurn], ClaudeResult]:
    """Consume an event stream and reconstruct assistant turns.

    Pairs ``TOOL_USE`` events with their corresponding ``TOOL_RESULT`` events
    across turn boundaries, building a list of ``AssistantTurn`` objects.

    Returns:
        A tuple of ``(turns, result)`` where *result* contains the final
        cost/usage/output information.
    """
    turns: list[AssistantTurn] = []
    current_turn: AssistantTurn | None = None
    # Map tool_use_id -> ToolAction for pairing with results
    pending_tools: dict[str, ToolAction] = {}
    result = ClaudeResult()

    async for event in events:
        if event.type == ClaudeEventType.INIT:
            result.session_id = event.session_id

        elif event.type == ClaudeEventType.TEXT:
            if current_turn is None:
                current_turn = AssistantTurn(model=event.model)
            current_turn.text += event.text or ""
            if current_turn.model is None and event.model is not None:
                current_turn.model = event.model

        elif event.type == ClaudeEventType.TOOL_USE:
            if current_turn is None:
                current_turn = AssistantTurn(model=event.model)
            action = ToolAction(
                tool_use_id=event.tool_use_id or "",
                name=event.tool_name or "",
                input=event.tool_input or {},
            )
            current_turn.tool_actions.append(action)
            if event.tool_use_id:
                pending_tools[event.tool_use_id] = action

        elif event.type == ClaudeEventType.TOOL_RESULT:
            # Pair with pending tool action
            tool_id = event.tool_result_for_id
            if tool_id and tool_id in pending_tools:
                action = pending_tools.pop(tool_id)
                action.result_content = event.tool_result_content
                action.is_error = event.tool_is_error or False
            # Tool results mark the end of one assistant turn -- the next
            # text/tool_use will start a new turn.
            if current_turn is not None:
                turns.append(current_turn)
                current_turn = None

        elif event.type == ClaudeEventType.RESULT:
            # Flush any in-progress turn
            if current_turn is not None:
                turns.append(current_turn)
                current_turn = None
            result.result_text = event.result_text
            result.structured_output = event.structured_output
            result.cost_usd = event.cost_usd
            result.usage = event.usage
            result.duration_ms = event.duration_ms
            result.model = event.model
            result.num_turns = event.num_turns
            if event.session_id:
                result.session_id = event.session_id

        elif event.type == ClaudeEventType.ERROR:
            if current_turn is not None:
                turns.append(current_turn)
                current_turn = None
            result.is_error = True
            result.result_text = event.error_message
            result.cost_usd = event.cost_usd
            result.usage = event.usage
            result.duration_ms = event.duration_ms

    # Flush trailing turn (no RESULT received -- shouldn't happen normally)
    if current_turn is not None:
        turns.append(current_turn)

    return turns, result
