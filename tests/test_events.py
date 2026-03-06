"""Tests for the EventBus pub/sub system."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.events import MAX_QUEUE_SIZE, Event, EventBus, TaskEvent


class TestEvent:
    """Tests for the Event dataclass."""

    def test_create_event(self) -> None:
        """Event should have type, task_id, data, and auto-generated timestamp."""
        event = Event(type="log", task_id="t1", data="hello")
        assert event.type == "log"
        assert event.task_id == "t1"
        assert event.data == "hello"
        assert event.timestamp is not None

    def test_event_custom_timestamp(self) -> None:
        """Event should accept a custom timestamp."""
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        event = Event(type="alert", task_id="t2", data={}, timestamp=ts)
        assert event.timestamp == ts

    def test_event_fields(self) -> None:
        """Event should store arbitrary data payloads."""
        event = Event(type="status_change", task_id="t3", data={"status": "done"})
        assert event.data == {"status": "done"}


class TestEventBus:
    """Tests for the EventBus pub/sub system."""

    def test_emit_no_subscribers(self) -> None:
        """Emitting with no subscribers should not raise."""
        bus = EventBus()
        bus.emit("log", "t1", "test")  # Should not crash

    async def test_subscribe_receives_events(self) -> None:
        """A subscriber should receive emitted events."""
        bus = EventBus()
        received: list[Event] = []

        async def consumer() -> None:
            async for event in bus.subscribe():
                received.append(event)
                if len(received) >= 2:
                    break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        bus.emit("log", "t1", "line1")
        bus.emit("alert", "t2", "boom")
        await task

        assert len(received) == 2
        assert received[0].type == "log"
        assert received[0].task_id == "t1"
        assert received[0].data == "line1"
        assert received[1].type == "alert"
        assert received[1].task_id == "t2"

    async def test_multi_subscriber(self) -> None:
        """Multiple subscribers should each receive all events."""
        bus = EventBus()
        received1: list[Event] = []
        received2: list[Event] = []

        async def consumer(target: list[Event]) -> None:
            async for event in bus.subscribe():
                target.append(event)
                if len(target) >= 1:
                    break

        t1 = asyncio.create_task(consumer(received1))
        t2 = asyncio.create_task(consumer(received2))
        await asyncio.sleep(0.01)

        bus.emit("log", "t1", "data")
        await t1
        await t2

        assert len(received1) == 1
        assert len(received2) == 1
        assert received1[0].data == "data"
        assert received2[0].data == "data"

    async def test_bounded_queue_drops_oldest(self) -> None:
        """When queue is full, oldest events should be dropped."""
        bus = EventBus()
        received: list[Event] = []

        async def consumer() -> None:
            async for event in bus.subscribe():
                received.append(event)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        # Emit more than MAX_QUEUE_SIZE events without yielding control,
        # so the consumer cannot process any until all emits are done.
        overflow = 50
        total = MAX_QUEUE_SIZE + overflow
        for i in range(total):
            bus.emit("log", "t1", f"event-{i}")

        await task

        # The consumer gets the oldest non-dropped event.
        # After the queue fills (events 0..999), each new event drops the oldest.
        # After 50 extra events, events 0..49 have been dropped.
        assert len(received) == 1
        assert received[0].data == f"event-{overflow}"

    async def test_subscriber_cleanup_on_close(self) -> None:
        """Closing the generator should remove the subscriber."""
        bus = EventBus()
        assert bus.subscriber_count == 0

        async def consumer() -> None:
            async for _event in bus.subscribe():
                break  # Exit after first event

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        assert bus.subscriber_count == 1

        bus.emit("log", "t1", "trigger")
        await task
        await asyncio.sleep(0.01)  # Let finally block run

        assert bus.subscriber_count == 0

    def test_subscriber_count_initial(self) -> None:
        """subscriber_count should be 0 initially."""
        bus = EventBus()
        assert bus.subscriber_count == 0

    async def test_multiple_event_types(self) -> None:
        """Events of different types should all be delivered."""
        bus = EventBus()
        received: list[Event] = []

        async def consumer() -> None:
            async for event in bus.subscribe():
                received.append(event)
                if len(received) >= 3:
                    break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        bus.emit("log", "t1", "line")
        bus.emit("status_change", "t1", {"status": "done"})
        bus.emit("alert", "t2", {"error": "failed"})
        await task

        types = [e.type for e in received]
        assert types == ["log", "status_change", "alert"]

    async def test_subscriber_independence(self) -> None:
        """One slow subscriber should not block others."""
        bus = EventBus()
        fast_received: list[Event] = []
        slow_received: list[Event] = []

        async def fast_consumer() -> None:
            async for event in bus.subscribe():
                fast_received.append(event)
                if len(fast_received) >= 2:
                    break

        async def slow_consumer() -> None:
            async for event in bus.subscribe():
                slow_received.append(event)
                await asyncio.sleep(0.05)  # Slow processing
                if len(slow_received) >= 1:
                    break

        fast_task = asyncio.create_task(fast_consumer())
        slow_task = asyncio.create_task(slow_consumer())
        await asyncio.sleep(0.01)

        bus.emit("log", "t1", "first")
        bus.emit("log", "t1", "second")

        await fast_task
        await slow_task

        assert len(fast_received) == 2
        assert len(slow_received) == 1

    async def test_emit_after_unsubscribe(self) -> None:
        """Emitting after all subscribers have left should not raise."""
        bus = EventBus()

        async def consumer() -> None:
            async for _event in bus.subscribe():
                break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)
        assert bus.subscriber_count == 1

        bus.emit("log", "t1", "trigger")
        await task
        await asyncio.sleep(0.01)

        assert bus.subscriber_count == 0
        bus.emit("log", "t1", "nobody listening")  # Should not crash


class TestTaskEventSchema:
    """Tests for TaskEvent Pydantic model schema enforcement."""

    def test_task_event_is_pydantic_model(self) -> None:
        """TaskEvent should be a Pydantic BaseModel."""
        from pydantic import BaseModel

        assert issubclass(TaskEvent, BaseModel)

    def test_event_alias_is_task_event(self) -> None:
        """Event should be an alias for TaskEvent."""
        assert Event is TaskEvent

    def test_missing_type_raises_validation_error(self) -> None:
        """Omitting 'type' should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TaskEvent(task_id="t1", data="x")  # type: ignore[call-arg]

    def test_missing_task_id_raises_validation_error(self) -> None:
        """Omitting 'task_id' should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TaskEvent(type="log", data="x")  # type: ignore[call-arg]

    def test_missing_data_raises_validation_error(self) -> None:
        """Omitting 'data' should raise a ValidationError."""
        with pytest.raises(ValidationError):
            TaskEvent(type="log", task_id="t1")  # type: ignore[call-arg]

    def test_timestamp_auto_generated(self) -> None:
        """Timestamp should be auto-generated when not provided."""
        event = TaskEvent(type="log", task_id="t1", data="hello")
        assert event.timestamp is not None
        assert event.timestamp.tzinfo is not None

    def test_timestamp_string_coerced(self) -> None:
        """Pydantic should coerce an ISO string into a datetime."""
        event = TaskEvent(
            type="log",
            task_id="t1",
            data="x",
            timestamp="2026-01-01T00:00:00+00:00",  # type: ignore[arg-type]
        )
        assert isinstance(event.timestamp, datetime)

    def test_model_dump_produces_expected_keys(self) -> None:
        """model_dump() should produce the SSE contract keys."""
        event = TaskEvent(type="log", task_id="t1", data={"msg": "hi"})
        dumped = event.model_dump()
        assert set(dumped.keys()) == {"type", "task_id", "data", "origin", "timestamp"}

    def test_emit_rejects_invalid_type(self) -> None:
        """EventBus.emit() should reject non-string event_type via Pydantic."""
        bus = EventBus()
        with pytest.raises(ValidationError):
            bus.emit(123, "t1", "data")  # type: ignore[arg-type]

    def test_emit_rejects_invalid_task_id(self) -> None:
        """EventBus.emit() should reject non-string task_id via Pydantic."""
        bus = EventBus()
        with pytest.raises(ValidationError):
            bus.emit("log", 456, "data")  # type: ignore[arg-type]

    def test_origin_defaults_to_system(self) -> None:
        """Origin should default to 'system' when not specified."""
        event = TaskEvent(type="log", task_id="t1", data="x")
        assert event.origin == "system"

    def test_origin_accepts_valid_values(self) -> None:
        """Origin should accept all valid Literal values."""
        for origin in ("execution", "review", "scheduler", "plan", "api", "system"):
            event = TaskEvent(type="log", task_id="t1", data="x", origin=origin)
            assert event.origin == origin

    def test_origin_rejects_invalid_value(self) -> None:
        """Origin should reject values outside the Literal type."""
        with pytest.raises(ValidationError):
            TaskEvent(type="log", task_id="t1", data="x", origin="bogus")  # type: ignore[arg-type]

    def test_emit_passes_origin_to_event(self) -> None:
        """EventBus.emit() should forward origin kwarg to TaskEvent."""
        bus = EventBus()
        received: list[TaskEvent] = []

        import asyncio

        async def _capture() -> None:
            async for evt in bus.subscribe():
                received.append(evt)
                break

        async def _run() -> None:
            task = asyncio.create_task(_capture())
            await asyncio.sleep(0.01)
            bus.emit("log", "t1", "x", origin="review")
            await task

        asyncio.get_event_loop().run_until_complete(_run())
        assert received[0].origin == "review"
