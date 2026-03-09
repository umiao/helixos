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
  source?: string;
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
  /** Status of the selected task (used to show elapsed timer when "running"). */
  selectedTaskStatus?: string;
  /** ISO timestamp when the selected task's execution started (for elapsed calc). */
  executionStartedAt?: string | null;
}

const MAX_VISIBLE_LINES = 500;
const FETCH_LIMIT = 500;
const POLL_INTERVAL_MS = 5000;

export default function ExecutionLog({
  entries,
  taskIds,
  selectedTaskId,
  selectedTaskStatus,
  executionStartedAt,
}: ExecutionLogProps) {
  const [filterTaskId, setFilterTaskId] = useState("");
  const [filterLevels, setFilterLevels] = useState<Set<string>>(new Set());
  const [showMoreLevels, setShowMoreLevels] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const moreDropdownRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevScrollTop = useRef(0);

  // Live elapsed counter for RUNNING tasks
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (selectedTaskStatus !== "running" || !executionStartedAt) {
      setElapsedSeconds(0);
      return;
    }
    const startMs = new Date(executionStartedAt).getTime();
    const tick = () => {
      const now = Date.now();
      setElapsedSeconds(Math.max(0, Math.floor((now - startMs) / 1000)));
    };
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [selectedTaskStatus, executionStartedAt]);

  const formatElapsed = (totalSec: number) => {
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

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
        source: e.source,
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
      source: e.source,
    }));
  }

  // Apply level filter in task-focused mode (multi-select: empty set = show all)
  if (filterLevels.size > 0 && selectedTaskId) {
    displayEntries = displayEntries.filter(
      (e) => !e.level || filterLevels.has(e.level.toLowerCase()),
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

  /** Message text color based on level and source for visual hierarchy (AC3). */
  const messageColor = (level?: string, source?: string) => {
    // Level takes priority for error/warn
    if (level) {
      switch (level.toLowerCase()) {
        case "error":
          return "text-red-400";
        case "warn":
          return "text-yellow-400";
        case "debug":
          return "text-gray-500";
      }
    }
    // Source-based coloring for clear visual hierarchy
    if (source) {
      switch (source.toLowerCase()) {
        case "review":
          return "text-purple-300";
        case "plan":
          return "text-violet-300";
        case "executor":
          return "text-blue-300";
        case "scheduler":
          return "text-cyan-300";
      }
    }
    return "text-gray-200";
  };

  const toggleLevel = useCallback((level: string) => {
    setFilterLevels((prev) => {
      const next = new Set(prev);
      if (next.has(level)) {
        next.delete(level);
      } else {
        next.add(level);
      }
      return next;
    });
  }, []);

  // Close "More" dropdown on outside click
  useEffect(() => {
    if (!showMoreLevels) return;
    const handleClick = (e: MouseEvent) => {
      if (
        moreDropdownRef.current &&
        !moreDropdownRef.current.contains(e.target as Node)
      ) {
        setShowMoreLevels(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showMoreLevels]);

  const COMMON_LEVELS = ["error", "warn", "info"] as const;
  const MORE_LEVELS = ["debug"] as const;

  const chipClass = (level: string, active: boolean) => {
    const base = "px-2 py-0.5 rounded text-xs font-medium cursor-pointer select-none transition-colors";
    if (!active) return `${base} bg-gray-700 text-gray-400 hover:bg-gray-600`;
    switch (level) {
      case "error":
        return `${base} bg-red-900 text-red-300 ring-1 ring-red-700`;
      case "warn":
        return `${base} bg-yellow-900 text-yellow-300 ring-1 ring-yellow-700`;
      case "info":
        return `${base} bg-blue-900 text-blue-300 ring-1 ring-blue-700`;
      case "debug":
        return `${base} bg-gray-600 text-gray-300 ring-1 ring-gray-500`;
      default:
        return `${base} bg-gray-600 text-gray-300 ring-1 ring-gray-500`;
    }
  };

  const sourceBadgeClass = (source: string) => {
    switch (source.toLowerCase()) {
      case "review":
        return "bg-purple-900 text-purple-300";
      case "plan":
        return "bg-violet-900 text-violet-300";
      case "scheduler":
        return "bg-cyan-900 text-cyan-300";
      case "executor":
        return "bg-blue-900 text-blue-300";
      default:
        return "bg-gray-700 text-gray-300";
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
          {selectedTaskStatus === "running" && elapsedSeconds > 0 && (
            <span className="text-xs font-mono text-yellow-400 bg-yellow-900 px-1.5 py-0.5 rounded">
              {formatElapsed(elapsedSeconds)} elapsed
            </span>
          )}
          {dbLoading && (
            <span className="text-xs text-gray-500">Loading...</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {selectedTaskId ? (
            <div className="flex items-center gap-1">
              {COMMON_LEVELS.map((level) => (
                <button
                  key={level}
                  onClick={() => toggleLevel(level)}
                  className={chipClass(level, filterLevels.has(level))}
                >
                  {level.toUpperCase()}
                </button>
              ))}
              <div className="relative" ref={moreDropdownRef}>
                <button
                  onClick={() => setShowMoreLevels((v) => !v)}
                  className={`px-2 py-0.5 rounded text-xs font-medium cursor-pointer select-none transition-colors ${
                    MORE_LEVELS.some((l) => filterLevels.has(l))
                      ? "bg-gray-600 text-gray-200 ring-1 ring-gray-500"
                      : "bg-gray-700 text-gray-400 hover:bg-gray-600"
                  }`}
                >
                  More
                </button>
                {showMoreLevels && (
                  <div className="absolute right-0 top-full mt-1 bg-gray-700 border border-gray-600 rounded shadow-lg z-10 min-w-[100px]">
                    {MORE_LEVELS.map((level) => (
                      <button
                        key={level}
                        onClick={() => toggleLevel(level)}
                        className={`block w-full text-left px-3 py-1.5 text-xs hover:bg-gray-600 ${
                          filterLevels.has(level)
                            ? "text-gray-200 font-medium"
                            : "text-gray-400"
                        }`}
                      >
                        {filterLevels.has(level) ? "[x] " : "[ ] "}
                        {level.toUpperCase()}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {filterLevels.size > 0 && (
                <button
                  onClick={() => setFilterLevels(new Set())}
                  className="px-1.5 py-0.5 rounded text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-600"
                  title="Clear all filters"
                >
                  Clear
                </button>
              )}
            </div>
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
        className="flex-1 overflow-y-auto p-2 font-mono text-[13px] leading-relaxed"
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
              <span className="text-gray-400 whitespace-nowrap shrink-0">
                {formatTime(entry.timestamp)}
              </span>
              {entry.source && ["review", "plan", "scheduler", "executor"].includes(entry.source) && (
                <span
                  className={`px-1 rounded text-xs uppercase font-medium shrink-0 ${sourceBadgeClass(entry.source)}`}
                >
                  {entry.source}
                </span>
              )}
              {entry.level && (
                <span
                  className={`px-1 rounded text-xs uppercase font-medium shrink-0 ${levelBadgeClass(entry.level)}`}
                >
                  {entry.level}
                </span>
              )}
              {!selectedTaskId && (
                <span className="text-indigo-400 whitespace-nowrap shrink-0">
                  [{entry.task_id}]
                </span>
              )}
              {entry.source && !["review", "plan", "scheduler", "executor"].includes(entry.source) && (
                <span className="text-gray-500 whitespace-nowrap shrink-0">
                  [{entry.source}]
                </span>
              )}
              <span className={messageColor(entry.level, entry.source)}>
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
