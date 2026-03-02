"""EventBus for real-time event streaming in HelixOS orchestrator.

Provides a pub/sub event bus where producers emit events and subscribers
consume them via async generators. Used by Scheduler, SSE endpoint, etc.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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
