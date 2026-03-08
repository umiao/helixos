import { useCallback, useMemo, useRef, useState } from "react";
import {
  fetchTask,
  fetchTasks,
  updateTaskStatus,
  ApiError,
} from "../api";
import type { Task, TaskStatus, StreamDisplayItem, StreamSummary } from "../types";
import type { LogEntry } from "../components/ExecutionLog";

let logIdCounter = 0;

export function useTaskState(addToast: (text: string, type: "success" | "error") => void) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);
  const [streamEvents, setStreamEvents] = useState<Record<string, StreamDisplayItem[]>>({});
  const [viewMode, setViewMode] = useState<"conversation" | "log">("conversation");
  const [reviewPhase, setReviewPhase] = useState("");
  const [bottomPanel, setBottomPanel] = useState<"log" | "review" | "running">("log");
  const [filterStatus, setFilterStatus] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [filterPriorities, setFilterPriorities] = useState<Set<string>>(new Set());
  const [filterComplexities, setFilterComplexities] = useState<Set<string>>(new Set());
  const [reviewSubmitTask, setReviewSubmitTask] = useState<Task | null>(null);
  const [editTask, setEditTask] = useState<Task | null>(null);

  // Keep refs for SSE handler (avoid stale closures)
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;
  const selectedTaskRef = useRef(selectedTask);
  selectedTaskRef.current = selectedTask;

  // Derive per-task stream summaries for popover display
  const streamSummaries = useMemo(() => {
    const result: Record<string, StreamSummary> = {};
    for (const [taskId, items] of Object.entries(streamEvents)) {
      let toolCallCount = 0;
      let lastActivity = "";
      for (const item of items) {
        if (item.type === "tool_use") {
          toolCallCount++;
          lastActivity = item.toolName ?? "tool";
        } else if (item.type === "text" && item.text) {
          const trimmed = item.text.trim();
          lastActivity = trimmed.length > 80
            ? "..." + trimmed.slice(-77)
            : trimmed;
        }
      }
      result[taskId] = { lastActivity, toolCallCount };
    }
    return result;
  }, [streamEvents]);

  // Apply global filters (status + search + priority + complexity)
  const globallyFiltered = useMemo(() => {
    return tasks.filter((t) => {
      if (filterStatus && t.status !== filterStatus) return false;
      if (
        searchQuery &&
        !t.title.toLowerCase().includes(searchQuery.toLowerCase()) &&
        !t.local_task_id.toLowerCase().includes(searchQuery.toLowerCase())
      ) {
        return false;
      }
      if (filterPriorities.size > 0) {
        const m = t.local_task_id.match(/T-P(\d+)-/);
        const prio = m ? `P${m[1]}` : null;
        if (!prio || !filterPriorities.has(prio)) return false;
      }
      if (filterComplexities.size > 0) {
        const cm = t.description.match(/\*\*Complexity\*\*:\s*(S|M|L)\b/);
        const cplx = cm ? cm[1] : null;
        if (!cplx || !filterComplexities.has(cplx)) return false;
      }
      return true;
    });
  }, [tasks, filterStatus, searchQuery, filterPriorities, filterComplexities]);

  const runningCount = useMemo(() => {
    return tasks.filter(
      (t) => t.status === "running" || t.plan_status === "generating"
    ).length;
  }, [tasks]);

  const taskIds = useMemo(() => [...new Set(tasks.map((t) => t.id))], [tasks]);

  const addLogEntry = useCallback(
    (task_id: string, message: string, timestamp: string, source?: string) => {
      setLogEntries((prev) => {
        const entry: LogEntry = {
          id: ++logIdCounter,
          task_id,
          message,
          timestamp,
          source,
        };
        const next = [...prev, entry];
        return next.length > 500 ? next.slice(-500) : next;
      });
    },
    [],
  );

  const handleMoveTask = useCallback(
    async (
      taskId: string,
      newStatus: TaskStatus,
      opts?: { reason?: string },
    ) => {
      const task = tasksRef.current.find((t) => t.id === taskId);
      const expectedUpdatedAt = task?.updated_at;

      // Optimistic update
      setTasks((prev) =>
        prev.map((t) => (t.id === taskId ? { ...t, status: newStatus } : t)),
      );

      try {
        const updated = await updateTaskStatus(taskId, newStatus, {
          reason: opts?.reason,
          expected_updated_at: expectedUpdatedAt,
        });
        setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)));

        if (newStatus === "review") {
          setSelectedTask(updated);
          setBottomPanel("review");
        }
      } catch (err) {
        // Revert optimistic update
        const original = tasksRef.current.find((t) => t.id === taskId);
        if (original) {
          setTasks((prev) =>
            prev.map((t) => (t.id === taskId ? original : t)),
          );
        }

        if (err instanceof ApiError) {
          const gateAction = (err as ApiError & { gate_action?: string }).gate_action;
          const conflict = (err as ApiError & { conflict?: boolean }).conflict;
          if (gateAction === "review_required" || gateAction === "plan_invalid") {
            const blockedTask = tasksRef.current.find((t) => t.id === taskId);
            if (blockedTask) {
              setReviewSubmitTask(blockedTask);
            } else {
              addToast(err.detail, "error");
            }
          } else if (conflict) {
            addToast("Task was just updated. Refreshing...", "error");
            try {
              const refreshed = await fetchTask(taskId);
              setTasks((prev) =>
                prev.map((t) => (t.id === taskId ? refreshed : t)),
              );
            } catch {
              const updatedTasks = await fetchTasks();
              setTasks(updatedTasks);
            }
          } else {
            addToast(err.detail, "error");
          }
        } else {
          addToast("Failed to update task", "error");
        }
      }
    },
    [addToast],
  );

  const handleSelectTask = useCallback((task: Task) => {
    setSelectedTask(task);
    setReviewPhase("");
    if (
      task.status === "review" ||
      task.status === "review_auto_approved" ||
      task.status === "review_needs_human"
    ) {
      setBottomPanel("review");
    }
  }, []);

  const handleReviewDecision = useCallback(
    (taskId: string, decision: string) => {
      addToast(
        `Review decision "${decision}" submitted for ${taskId}`,
        "success",
      );
      setSelectedTask(null);
    },
    [addToast],
  );

  const handleTaskCreated = useCallback(async (synced?: boolean) => {
    try {
      const updatedTasks = await fetchTasks();
      setTasks(updatedTasks);
      if (synced === false) {
        addToast("Task saved to TASKS.md but sync to board failed. Try manual sync.", "error");
      } else {
        addToast("Task created", "success");
      }
    } catch {
      // Data will be stale but not broken
    }
  }, [addToast]);

  const handleEditSaved = useCallback(
    (updated: Task) => {
      setEditTask(null);
      setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      setSelectedTask((sel) =>
        sel && sel.id === updated.id ? updated : sel,
      );
      addToast("Task updated", "success");
    },
    [addToast],
  );

  const handleTaskDeleted = useCallback(async () => {
    try {
      const updatedTasks = await fetchTasks();
      setTasks(updatedTasks);
      addToast("Task deleted", "success");
    } catch {
      // Data will be stale but not broken
    }
  }, [addToast]);

  const handleSendToReview = useCallback((task: Task) => {
    setReviewSubmitTask(task);
  }, []);

  const handleEditTask = useCallback((task: Task) => {
    setEditTask(task);
  }, []);

  const handleReviewSubmitted = useCallback(
    async (taskId: string) => {
      setReviewSubmitTask(null);
      addToast(`Task ${taskId} submitted for review`, "success");
      try {
        const refreshed = await fetchTask(taskId);
        setTasks((prev) => prev.map((t) => (t.id === taskId ? refreshed : t)));
        setSelectedTask(refreshed);
        setBottomPanel("review");
      } catch {
        const updatedTasks = await fetchTasks();
        setTasks(updatedTasks);
      }
    },
    [addToast],
  );

  const clearFilters = useCallback(() => {
    setFilterPriorities(new Set());
    setFilterComplexities(new Set());
  }, []);

  return {
    // State
    tasks,
    setTasks,
    loading,
    setLoading,
    selectedTask,
    setSelectedTask,
    logEntries,
    streamEvents,
    setStreamEvents,
    streamSummaries,
    viewMode,
    setViewMode,
    reviewPhase,
    setReviewPhase,
    bottomPanel,
    setBottomPanel,
    filterStatus,
    setFilterStatus,
    searchQuery,
    setSearchQuery,
    filterPriorities,
    setFilterPriorities,
    filterComplexities,
    setFilterComplexities,
    reviewSubmitTask,
    setReviewSubmitTask,
    editTask,
    setEditTask,
    globallyFiltered,
    runningCount,
    taskIds,
    // Refs
    tasksRef,
    selectedTaskRef,
    // Handlers
    addLogEntry,
    handleMoveTask,
    handleSelectTask,
    handleReviewDecision,
    handleTaskCreated,
    handleEditSaved,
    handleTaskDeleted,
    handleSendToReview,
    handleEditTask,
    handleReviewSubmitted,
    clearFilters,
  };
}
