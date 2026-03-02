/**
 * useSSE -- React hook for connecting to the SSE event stream.
 * Connects to GET /api/events, auto-reconnects with exponential backoff
 * (1s, 2s, 4s, ... max 30s), and provides a `connected` boolean.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** Shape of an SSE event from the backend. */
export interface SSEEvent {
  type: string;
  task_id: string;
  data: Record<string, unknown>;
  timestamp: string;
}

/** Callback invoked for each SSE event received. */
export type SSEEventHandler = (event: SSEEvent) => void;

const BASE_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30000;

export default function useSSE(onEvent: SSEEventHandler): {
  connected: boolean;
} {
  const [connected, setConnected] = useState(false);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const reconnectAttempt = useRef(0);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    // Clean up previous connection if any
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const es = new EventSource("/api/events");
    eventSourceRef.current = es;

    es.onopen = () => {
      setConnected(true);
      reconnectAttempt.current = 0;
    };

    es.onmessage = (msg: MessageEvent) => {
      try {
        const event: SSEEvent = JSON.parse(msg.data as string);
        onEventRef.current(event);
      } catch {
        // Ignore non-JSON messages (e.g., keepalive comments)
      }
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
      eventSourceRef.current = null;

      // Exponential backoff: 1s, 2s, 4s, 8s, ... capped at 30s
      const delay = Math.min(
        BASE_BACKOFF_MS * Math.pow(2, reconnectAttempt.current),
        MAX_BACKOFF_MS,
      );
      reconnectAttempt.current += 1;

      reconnectTimerRef.current = setTimeout(() => {
        connect();
      }, delay);
    };
  }, []);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [connect]);

  return { connected };
}
