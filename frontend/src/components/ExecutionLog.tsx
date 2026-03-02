/**
 * ExecutionLog -- scrollable log panel with task filtering,
 * auto-scroll with scroll-lock, timestamps, and a max of 500 lines.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface LogEntry {
  id: number;
  task_id: string;
  message: string;
  timestamp: string;
}

interface ExecutionLogProps {
  entries: LogEntry[];
  taskIds: string[];
}

const MAX_VISIBLE_LINES = 500;

export default function ExecutionLog({ entries, taskIds }: ExecutionLogProps) {
  const [filterTaskId, setFilterTaskId] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevScrollTop = useRef(0);

  const filtered = entries
    .filter((e) => !filterTaskId || e.task_id === filterTaskId)
    .slice(-MAX_VISIBLE_LINES);

  // Auto-scroll to bottom when new entries arrive (unless user scrolled up)
  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [filtered.length, autoScroll]);

  // Detect manual scroll-up to pause auto-scroll
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;

    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    // User scrolled up -> disable auto-scroll; scrolled to bottom -> re-enable
    if (el.scrollTop < prevScrollTop.current && !atBottom) {
      setAutoScroll(false);
    } else if (atBottom) {
      setAutoScroll(true);
    }
    prevScrollTop.current = el.scrollTop;
  }, []);

  const formatTime = (ts: string) => {
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return ts;
    }
  };

  return (
    <div className="flex flex-col h-full bg-gray-900 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700">
        <h3 className="text-xs font-bold uppercase tracking-wide text-gray-300">
          Execution Log
        </h3>
        <div className="flex items-center gap-2">
          <select
            value={filterTaskId}
            onChange={(e) => setFilterTaskId(e.target.value)}
            className="rounded border border-gray-600 bg-gray-700 px-2 py-0.5 text-xs text-gray-200"
          >
            <option value="">All tasks</option>
            {taskIds.map((tid) => (
              <option key={tid} value={tid}>
                {tid}
              </option>
            ))}
          </select>
          {!autoScroll && (
            <button
              onClick={() => setAutoScroll(true)}
              className="rounded bg-indigo-600 px-2 py-0.5 text-xs text-white hover:bg-indigo-700"
            >
              Resume scroll
            </button>
          )}
        </div>
      </div>

      {/* Log content */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-2 font-mono text-xs leading-relaxed"
      >
        {filtered.length === 0 ? (
          <p className="text-gray-500 text-center py-4">No log entries</p>
        ) : (
          filtered.map((entry) => (
            <div key={entry.id} className="flex gap-2 py-0.5 hover:bg-gray-800">
              <span className="text-gray-500 whitespace-nowrap shrink-0">
                {formatTime(entry.timestamp)}
              </span>
              <span className="text-indigo-400 whitespace-nowrap shrink-0">
                [{entry.task_id}]
              </span>
              <span className="text-gray-200 break-all">{entry.message}</span>
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-1 bg-gray-800 border-t border-gray-700 text-xs text-gray-500 flex justify-between">
        <span>{filtered.length} lines</span>
        <span>{autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}</span>
      </div>
    </div>
  );
}
