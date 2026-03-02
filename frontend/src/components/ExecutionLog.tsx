/**
 * ExecutionLog -- task-focused log panel with persistent DB history + live SSE merge.
 *
 * Two modes:
 * - Task-focused (selectedTaskId set): fetches persistent logs from DB,
 *   merges with live SSE entries for the task, shows level/source coloring.
 * - All-tasks (no selectedTaskId): shows all live SSE entries with task filter.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ExecutionLogEntry } from "../types";
import { fetchExecutionLogs } from "../api";

export interface LogEntry {
  id: number;
  task_id: string;
  message: string;
  timestamp: string;
}

/** Unified display item for both DB and SSE entries. */
interface DisplayLogEntry {
  key: string;
  task_id: string;
  message: string;
  timestamp: string;
  level?: string;
  source?: string;
}

interface ExecutionLogProps {
  entries: LogEntry[];
  taskIds: string[];
  selectedTaskId?: string;
}

const MAX_VISIBLE_LINES = 500;
const FETCH_LIMIT = 500;
const POLL_INTERVAL_MS = 5000;

export default function ExecutionLog({
  entries,
  taskIds,
  selectedTaskId,
}: ExecutionLogProps) {
  const [filterTaskId, setFilterTaskId] = useState("");
  const [filterLevel, setFilterLevel] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevScrollTop = useRef(0);

  // DB log state for task-focused mode
  const [dbEntries, setDbEntries] = useState<ExecutionLogEntry[]>([]);
  const [dbTotal, setDbTotal] = useState(0);
  const [dbLoading, setDbLoading] = useState(false);
  const [dbFetchedTaskId, setDbFetchedTaskId] = useState<string | null>(null);

  // Fetch DB logs when selectedTaskId changes
  useEffect(() => {
    if (!selectedTaskId) {
      setDbEntries([]);
      setDbTotal(0);
      setDbFetchedTaskId(null);
      return;
    }

    let cancelled = false;
    const load = async () => {
      setDbLoading(true);
      try {
        const resp = await fetchExecutionLogs(selectedTaskId, {
          limit: FETCH_LIMIT,
        });
        if (!cancelled) {
          setDbEntries(resp.entries);
          setDbTotal(resp.total);
          setDbFetchedTaskId(selectedTaskId);
        }
      } catch {
        // Log entries are non-critical
      } finally {
        if (!cancelled) setDbLoading(false);
      }
    };
    load();

    return () => {
      cancelled = true;
    };
  }, [selectedTaskId]);

  // Poll DB logs while a task is selected
  useEffect(() => {
    if (!selectedTaskId) return;

    const interval = setInterval(async () => {
      try {
        const resp = await fetchExecutionLogs(selectedTaskId, {
          limit: FETCH_LIMIT,
        });
        setDbEntries(resp.entries);
        setDbTotal(resp.total);
      } catch {
        // Ignore poll errors
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [selectedTaskId]);

  // Build display entries based on mode
  let displayEntries: DisplayLogEntry[];

  if (selectedTaskId && dbFetchedTaskId === selectedTaskId) {
    // Task-focused: DB entries + SSE entries newer than latest DB entry
    const dbDisplay: DisplayLogEntry[] = dbEntries.map((e) => ({
      key: `db-${e.id}`,
      task_id: e.task_id,
      message: e.message,
      timestamp: e.timestamp,
      level: e.level,
      source: e.source,
    }));

    const latestDbTs =
      dbEntries.length > 0 ? dbEntries[dbEntries.length - 1].timestamp : "";
    const sseForTask = entries
      .filter((e) => e.task_id === selectedTaskId && e.timestamp > latestDbTs)
      .map((e) => ({
        key: `sse-${e.id}`,
        task_id: e.task_id,
        message: e.message,
        timestamp: e.timestamp,
      }));

    displayEntries = [...dbDisplay, ...sseForTask].slice(-MAX_VISIBLE_LINES);
  } else {
    // All-tasks mode: SSE entries with optional task filter
    const filtered = entries
      .filter((e) => !filterTaskId || e.task_id === filterTaskId)
      .slice(-MAX_VISIBLE_LINES);
    displayEntries = filtered.map((e) => ({
      key: `sse-${e.id}`,
      task_id: e.task_id,
      message: e.message,
      timestamp: e.timestamp,
    }));
  }

  // Apply level filter in task-focused mode
  if (filterLevel && selectedTaskId) {
    displayEntries = displayEntries.filter(
      (e) => !e.level || e.level.toLowerCase() === filterLevel.toLowerCase(),
    );
  }

  // Auto-scroll on new entries
  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [displayEntries.length, autoScroll]);

  // Detect manual scroll-up to pause auto-scroll
  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    if (el.scrollTop < prevScrollTop.current && !atBottom) {
      setAutoScroll(false);
    } else if (atBottom) {
      setAutoScroll(true);
    }
    prevScrollTop.current = el.scrollTop;
  }, []);

  const formatTime = (ts: string) => {
    try {
      return new Date(ts).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return ts;
    }
  };

  const levelColor = (level?: string) => {
    if (!level) return "text-gray-200";
    switch (level.toLowerCase()) {
      case "error":
        return "text-red-400";
      case "warn":
        return "text-yellow-400";
      case "debug":
        return "text-gray-500";
      default:
        return "text-gray-200";
    }
  };

  const levelBadgeClass = (level: string) => {
    switch (level.toLowerCase()) {
      case "error":
        return "bg-red-900 text-red-300";
      case "warn":
        return "bg-yellow-900 text-yellow-300";
      case "info":
        return "bg-blue-900 text-blue-300";
      case "debug":
        return "bg-gray-700 text-gray-400";
      default:
        return "bg-gray-700 text-gray-300";
    }
  };

  return (
    <div className="flex flex-col h-full bg-gray-900 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-bold uppercase tracking-wide text-gray-300">
            Execution Log
          </h3>
          {selectedTaskId && (
            <span className="text-xs text-indigo-400 font-mono">
              {selectedTaskId}
            </span>
          )}
          {dbLoading && (
            <span className="text-xs text-gray-500">Loading...</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {selectedTaskId ? (
            <select
              value={filterLevel}
              onChange={(e) => setFilterLevel(e.target.value)}
              className="rounded border border-gray-600 bg-gray-700 px-2 py-0.5 text-xs text-gray-200"
            >
              <option value="">All levels</option>
              <option value="error">Error</option>
              <option value="warn">Warn</option>
              <option value="info">Info</option>
              <option value="debug">Debug</option>
            </select>
          ) : (
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
          )}
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

      {/* Truncation notice */}
      {selectedTaskId && dbTotal > FETCH_LIMIT && (
        <div className="px-3 py-1 bg-gray-800 border-b border-gray-700 text-xs text-yellow-400">
          Showing latest {FETCH_LIMIT} of {dbTotal} entries
        </div>
      )}

      {/* Log content */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-2 font-mono text-xs leading-relaxed"
      >
        {displayEntries.length === 0 ? (
          <p className="text-gray-500 text-center py-4">
            {selectedTaskId
              ? "No log entries for this task"
              : "No log entries"}
          </p>
        ) : (
          displayEntries.map((entry) => (
            <div
              key={entry.key}
              className="flex gap-2 py-0.5 hover:bg-gray-800"
            >
              <span className="text-gray-500 whitespace-nowrap shrink-0">
                {formatTime(entry.timestamp)}
              </span>
              {entry.level && (
                <span
                  className={`px-1 rounded text-[10px] uppercase font-medium shrink-0 ${levelBadgeClass(entry.level)}`}
                >
                  {entry.level}
                </span>
              )}
              {!selectedTaskId && (
                <span className="text-indigo-400 whitespace-nowrap shrink-0">
                  [{entry.task_id}]
                </span>
              )}
              {entry.source && (
                <span className="text-gray-500 whitespace-nowrap shrink-0">
                  [{entry.source}]
                </span>
              )}
              <span className={levelColor(entry.level)}>
                {entry.message}
              </span>
            </div>
          ))
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-1 bg-gray-800 border-t border-gray-700 text-xs text-gray-500 flex justify-between">
        <span>
          {displayEntries.length} lines
          {selectedTaskId && dbTotal > 0 && ` (${dbTotal} total)`}
        </span>
        <span>{autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}</span>
      </div>
    </div>
  );
}
