"""Tests for the SSE event stream endpoint and formatting."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from starlette.responses import StreamingResponse

from src.events import Event, EventBus, format_sse, sse_events, sse_stream

# ---------------------------------------------------------------------------
# format_sse tests
# ---------------------------------------------------------------------------


class TestFormatSSE:
    """Tests for the format_sse helper function."""

    def test_basic_format(self) -> None:
        """format_sse should return 'data: {json}\\n\\n'."""
        ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        event = Event(type="log", task_id="t1", data="hello", timestamp=ts)
        result = format_sse(event)

        assert result.startswith("data: ")
        assert result.endswith("\n\n")

        payload = json.loads(result[len("data: ") : -2])
        assert payload["type"] == "log"
        assert payload["task_id"] == "t1"
        assert payload["data"] == "hello"
        assert payload["timestamp"] == "2026-01-15T12:00:00+00:00"

    def test_dict_data(self) -> None:
        """format_sse should handle dict data payloads."""
        event = Event(type="status_change", task_id="t2", data={"status": "done"})
        result = format_sse(event)
        payload = json.loads(result[len("data: ") : -2])
        assert payload["data"] == {"status": "done"}

    def test_all_event_types(self) -> None:
        """format_sse should work for all expected event types."""
        for etype in ("log", "status_change", "review_progress", "alert"):
            event = Event(type=etype, task_id="t1", data="x")
            result = format_sse(event)
            payload = json.loads(result[len("data: ") : -2])
            assert payload["type"] == etype

    def test_json_structure(self) -> None:
        """Payload should contain exactly type, task_id, data, timestamp."""
        event = Event(type="alert", task_id="t3", data={"err": "fail"})
        result = format_sse(event)
        payload = json.loads(result[len("data: ") : -2])
        assert set(payload.keys()) == {"type", "task_id", "data", "timestamp"}

    def test_nested_data(self) -> None:
        """format_sse should handle nested data structures."""
        event = Event(
            type="review_progress",
            task_id="t4",
            data={"round": 2, "reviews": [{"verdict": "approve"}]},
        )
        result = format_sse(event)
        payload = json.loads(result[len("data: ") : -2])
        assert payload["data"]["reviews"][0]["verdict"] == "approve"

    def test_null_data(self) -> None:
        """format_sse should handle None data."""
        event = Event(type="log", task_id="t1", data=None)
        result = format_sse(event)
        payload = json.loads(result[len("data: ") : -2])
        assert payload["data"] is None


# ---------------------------------------------------------------------------
# sse_stream tests
# ---------------------------------------------------------------------------


class TestSSEStream:
    """Tests for the sse_stream async generator."""

    async def test_yields_event_frames(self) -> None:
        """sse_stream should yield formatted SSE frames for events."""
        bus = EventBus()
        frames: list[str] = []

        async def collect() -> None:
            async for frame in sse_stream(bus, keepalive_interval=5.0):
                frames.append(frame)
                if len(frames) >= 2:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.02)

        bus.emit("log", "t1", "line1")
        bus.emit("alert", "t2", "boom")
        await task

        assert len(frames) == 2
        assert all(f.startswith("data: ") for f in frames)
        p1 = json.loads(frames[0][len("data: ") : -2])
        assert p1["type"] == "log"
        assert p1["task_id"] == "t1"
        p2 = json.loads(frames[1][len("data: ") : -2])
        assert p2["type"] == "alert"
        assert p2["task_id"] == "t2"

    async def test_keepalive_on_idle(self) -> None:
        """sse_stream should yield keepalive comments when no events arrive."""
        bus = EventBus()
        frames: list[str] = []

        async def collect() -> None:
            async for frame in sse_stream(bus, keepalive_interval=0.05):
                frames.append(frame)
                if len(frames) >= 1:
                    break

        task = asyncio.create_task(collect())
        await task

        assert len(frames) == 1
        assert frames[0] == ": keepalive\n\n"

    async def test_keepalive_format(self) -> None:
        """Keepalive should be a valid SSE comment (colon prefix)."""
        bus = EventBus()
        frames: list[str] = []

        async def collect() -> None:
            async for frame in sse_stream(bus, keepalive_interval=0.03):
                frames.append(frame)
                if len(frames) >= 2:
                    break

        task = asyncio.create_task(collect())
        await task

        for frame in frames:
            assert frame.startswith(": ")
            assert frame.endswith("\n\n")

    async def test_mixed_events_and_keepalive(self) -> None:
        """sse_stream should interleave events and keepalives correctly."""
        bus = EventBus()
        frames: list[str] = []

        async def collect() -> None:
            async for frame in sse_stream(bus, keepalive_interval=0.05):
                frames.append(frame)
                if len(frames) >= 3:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.02)

        # Emit one event immediately, then wait for keepalive, then another event
        bus.emit("log", "t1", "first")
        await asyncio.sleep(0.08)  # Enough for one keepalive
        bus.emit("log", "t1", "second")
        await task

        assert frames[0].startswith("data: ")
        assert frames[1] == ": keepalive\n\n"
        assert frames[2].startswith("data: ")

    async def test_cleanup_on_close(self) -> None:
        """sse_stream should unsubscribe when the generator is closed."""
        bus = EventBus()
        assert bus.subscriber_count == 0

        async def collect() -> None:
            async for _frame in sse_stream(bus, keepalive_interval=5.0):
                break  # Exit after first frame

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.02)
        assert bus.subscriber_count == 1

        bus.emit("log", "t1", "trigger")
        await task
        await asyncio.sleep(0.02)

        assert bus.subscriber_count == 0

    async def test_multiple_subscribers(self) -> None:
        """Multiple sse_stream consumers should each get all events."""
        bus = EventBus()
        frames1: list[str] = []
        frames2: list[str] = []

        async def collect(target: list[str]) -> None:
            async for frame in sse_stream(bus, keepalive_interval=5.0):
                target.append(frame)
                if len(target) >= 1:
                    break

        t1 = asyncio.create_task(collect(frames1))
        t2 = asyncio.create_task(collect(frames2))
        await asyncio.sleep(0.02)

        bus.emit("log", "t1", "broadcast")
        await t1
        await t2

        assert len(frames1) == 1
        assert len(frames2) == 1
        p1 = json.loads(frames1[0][len("data: ") : -2])
        p2 = json.loads(frames2[0][len("data: ") : -2])
        assert p1["data"] == "broadcast"
        assert p2["data"] == "broadcast"

    async def test_subscriber_cleanup_after_multiple_events(self) -> None:
        """Subscriber count should return to 0 after stream ends."""
        bus = EventBus()

        async def collect() -> None:
            count = 0
            async for _frame in sse_stream(bus, keepalive_interval=5.0):
                count += 1
                if count >= 3:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.02)
        assert bus.subscriber_count == 1

        bus.emit("log", "t1", "one")
        bus.emit("log", "t1", "two")
        bus.emit("log", "t1", "three")
        await task
        await asyncio.sleep(0.02)

        assert bus.subscriber_count == 0


# ---------------------------------------------------------------------------
# SSE endpoint handler tests
# ---------------------------------------------------------------------------


def _make_request_with_event_bus(event_bus: EventBus) -> MagicMock:
    """Create a mock Request with app.state.event_bus set."""
    request = MagicMock()
    request.app.state.event_bus = event_bus
    return request


class TestSSEEndpoint:
    """Tests for the sse_events endpoint handler."""

    async def test_returns_streaming_response(self) -> None:
        """sse_events should return a StreamingResponse."""
        bus = EventBus()
        request = _make_request_with_event_bus(bus)

        response = await sse_events(request)

        assert isinstance(response, StreamingResponse)

    async def test_content_type(self) -> None:
        """Response should have text/event-stream content type."""
        bus = EventBus()
        request = _make_request_with_event_bus(bus)

        response = await sse_events(request)

        assert response.media_type == "text/event-stream"

    async def test_response_headers(self) -> None:
        """Response should have proper SSE headers."""
        bus = EventBus()
        request = _make_request_with_event_bus(bus)

        response = await sse_events(request)

        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"

    async def test_body_streams_events(self) -> None:
        """Response body should yield SSE event frames."""
        bus = EventBus()
        request = _make_request_with_event_bus(bus)

        response = await sse_events(request)
        body_iter = response.body_iterator
        frames: list[str] = []

        async def collect() -> None:
            async for chunk in body_iter:
                frames.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
                if len(frames) >= 2:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.02)

        bus.emit("status_change", "t1", {"status": "done"})
        bus.emit("alert", "t2", {"error": "fail"})
        await task

        assert len(frames) == 2
        p1 = json.loads(frames[0][len("data: ") : -2])
        assert p1["type"] == "status_change"
        assert p1["task_id"] == "t1"
        p2 = json.loads(frames[1][len("data: ") : -2])
        assert p2["type"] == "alert"
        assert p2["task_id"] == "t2"

    async def test_body_streams_keepalive(self) -> None:
        """Response body should yield keepalive when idle (via sse_stream)."""
        bus = EventBus()

        # We test keepalive through sse_stream directly since the endpoint
        # uses the default 15s interval (too slow for tests).
        frames: list[str] = []

        async def collect() -> None:
            async for frame in sse_stream(bus, keepalive_interval=0.04):
                frames.append(frame)
                if len(frames) >= 1:
                    break

        task = asyncio.create_task(collect())
        await task

        assert frames[0] == ": keepalive\n\n"

    async def test_disconnect_unsubscribes(self) -> None:
        """Closing the response body should unsubscribe from EventBus."""
        bus = EventBus()
        request = _make_request_with_event_bus(bus)

        response = await sse_events(request)
        body_iter = response.body_iterator

        # Start consuming and read one frame
        async def collect() -> None:
            async for _chunk in body_iter:
                break  # Stop after first frame

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.02)
        assert bus.subscriber_count == 1

        bus.emit("log", "t1", "trigger")
        await task

        # Explicitly close the async generator to trigger cleanup
        await body_iter.aclose()
        await asyncio.sleep(0.02)

        assert bus.subscriber_count == 0

    async def test_event_json_schema(self) -> None:
        """Each event frame should have type, task_id, data, timestamp keys."""
        bus = EventBus()
        request = _make_request_with_event_bus(bus)

        response = await sse_events(request)
        body_iter = response.body_iterator
        frames: list[str] = []

        async def collect() -> None:
            async for chunk in body_iter:
                frames.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
                if len(frames) >= 1:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.02)

        bus.emit("review_progress", "t5", {"round": 2, "total": 3})
        await task

        payload = json.loads(frames[0][len("data: ") : -2])
        assert set(payload.keys()) == {"type", "task_id", "data", "timestamp"}
        assert payload["type"] == "review_progress"
        assert payload["task_id"] == "t5"
        assert payload["data"] == {"round": 2, "total": 3}

    async def test_multiple_clients_via_handler(self) -> None:
        """Multiple handler calls should each subscribe independently."""
        bus = EventBus()
        request = _make_request_with_event_bus(bus)

        resp1 = await sse_events(request)
        resp2 = await sse_events(request)
        frames1: list[str] = []
        frames2: list[str] = []

        async def collect(body_iter: object, target: list[str]) -> None:
            async for chunk in body_iter:  # type: ignore[union-attr]
                target.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
                if len(target) >= 1:
                    break

        t1 = asyncio.create_task(collect(resp1.body_iterator, frames1))
        t2 = asyncio.create_task(collect(resp2.body_iterator, frames2))
        await asyncio.sleep(0.02)

        bus.emit("log", "t1", "broadcast")
        await t1
        await t2

        assert len(frames1) == 1
        assert len(frames2) == 1
        p1 = json.loads(frames1[0][len("data: ") : -2])
        p2 = json.loads(frames2[0][len("data: ") : -2])
        assert p1["data"] == "broadcast"
        assert p2["data"] == "broadcast"
