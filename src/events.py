"""EventBus for real-time event streaming in HelixOS orchestrator.

Provides a pub/sub event bus where producers emit events and subscribers
consume them via async generators. Includes SSE formatting and a FastAPI
router for the ``GET /api/events`` endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

MAX_QUEUE_SIZE = 1000


@dataclass
class Event:
    """A single event emitted by the orchestrator.

    Attributes:
        type: Event category (e.g. "log", "status_change", "alert").
        task_id: The task this event relates to.
        data: Event payload (any JSON-serializable value).
        timestamp: When the event was created (defaults to now).
    """

    type: str
    task_id: str
    data: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventBus:
    """Pub/sub event bus with bounded per-subscriber queues.

    Producers call ``emit()`` to broadcast events. Consumers call
    ``subscribe()`` to get an async generator of events. Each subscriber
    gets its own bounded queue (max 1000 events); when full, the oldest
    event is dropped to make room.
    """

    def __init__(self) -> None:
        """Initialize the event bus with no subscribers."""
        self._subscribers: list[asyncio.Queue[Event]] = []

    def emit(self, event_type: str, task_id: str, data: Any) -> None:
        """Emit an event to all current subscribers.

        Args:
            event_type: Event type (e.g., "log", "status_change", "alert").
            task_id: The task this event relates to.
            data: Event payload (any JSON-serializable value).
        """
        event = Event(type=event_type, task_id=task_id, data=data)
        for queue in self._subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()  # Drop oldest
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def subscribe(self) -> AsyncGenerator[Event, None]:
        """Subscribe to events, yielding them as they arrive.

        Creates a bounded queue for this subscriber. The queue is
        automatically removed when the generator is closed (e.g.,
        when the SSE client disconnects).

        Yields:
            Event objects as they are emitted.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        """Return the number of active subscribers."""
        return len(self._subscribers)


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------

KEEPALIVE_INTERVAL_SECONDS = 15.0


def format_sse(event: Event) -> str:
    """Format an Event as a Server-Sent Events ``data:`` frame.

    Returns a string of the form ``data: {json}\\n\\n`` suitable for
    writing directly into an SSE stream.

    Args:
        event: The event to format.

    Returns:
        SSE-formatted string.
    """
    payload = {
        "type": event.type,
        "task_id": event.task_id,
        "data": event.data,
        "timestamp": event.timestamp.isoformat(),
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def sse_stream(
    event_bus: EventBus,
    keepalive_interval: float = KEEPALIVE_INTERVAL_SECONDS,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE frames from the EventBus.

    Subscribes to the bus, yields ``data:`` frames for real events and
    ``: keepalive`` comments when idle. The subscription is cleaned up
    automatically when the generator is closed (client disconnect).

    Args:
        event_bus: The EventBus to subscribe to.
        keepalive_interval: Seconds between keepalive comments.

    Yields:
        SSE-formatted strings (data frames and keepalive comments).
    """
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
    event_bus._subscribers.append(queue)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
                yield format_sse(event)
            except TimeoutError:
                yield ": keepalive\n\n"
    finally:
        with contextlib.suppress(ValueError):
            event_bus._subscribers.remove(queue)


# ---------------------------------------------------------------------------
# FastAPI SSE router
# ---------------------------------------------------------------------------

sse_router = APIRouter()


@sse_router.get("/api/events")
async def sse_events(request: Request) -> StreamingResponse:
    """SSE endpoint that streams orchestrator events to clients.

    Expects ``request.app.state.event_bus`` to be an EventBus instance
    (set up by the application lifespan handler in T-P0-10).

    The stream sends:
    - ``data: {json}`` frames for real events
    - ``: keepalive`` comments every 15 seconds to keep the connection alive

    On client disconnect the ASGI framework closes the generator,
    which triggers the ``finally`` block in ``sse_stream`` to
    unsubscribe from the EventBus.
    """
    event_bus: EventBus = request.app.state.event_bus
    return StreamingResponse(
        sse_stream(event_bus),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
