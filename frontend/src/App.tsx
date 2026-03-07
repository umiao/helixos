import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import SwimLane from "./components/SwimLane";
import ExecutionLog, { type LogEntry } from "./components/ExecutionLog";
import ConversationView, { normalizeStreamEvents } from "./components/ConversationView";
import ReviewPanel from "./components/ReviewPanel";
import RunningJobsPanel from "./components/RunningJobsPanel";
import ResizableDivider, {
  loadPanelHeight,
} from "./components/ResizableDivider";
import Toast, { type ToastMessage } from "./components/Toast";
import ProjectSelector, {
  loadSelectedProjects,
  saveSelectedProjects,
} from "./components/ProjectSelector";
import ImportProjectModal from "./components/ImportProjectModal";
import NewTaskModal from "./components/NewTaskModal";
import useSSE, { type SSEEvent } from "./hooks/useSSE";
import ReviewSubmitModal from "./components/ReviewSubmitModal";
import EditTaskModal from "./components/EditTaskModal";
import {
  fetchProjects,
  fetchTasks,
  fetchTask,
  syncAll,
  syncProject,
  updateTaskStatus,
  ApiError,
} from "./api";
import type { Project, Task, TaskStatus, StreamDisplayItem, StreamSummary } from "./types";

let toastIdCounter = 0;
let logIdCounter = 0;

function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [syncingProjects, setSyncingProjects] = useState<Set<string>>(
    new Set(),
  );
  const [selectedProjects, setSelectedProjects] = useState<string[]>([]);
  const [filterStatus, setFilterStatus] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [bottomPanel, setBottomPanel] = useState<"log" | "review" | "running">("log");
  const [showImportModal, setShowImportModal] = useState(false);
  const [newTaskProject, setNewTaskProject] = useState<Project | null>(null);
  const [enrichTitle, setEnrichTitle] = useState("");
  const [autoEnrich, setAutoEnrich] = useState(false);
  const [bottomPanelHeight, setBottomPanelHeight] = useState(loadPanelHeight);
  const [reviewSubmitTask, setReviewSubmitTask] = useState<Task | null>(null);
  const [editTask, setEditTask] = useState<Task | null>(null);
  const [reviewPhase, setReviewPhase] = useState("");
  const [streamEvents, setStreamEvents] = useState<Record<string, StreamDisplayItem[]>>({});
  const [viewMode, setViewMode] = useState<"conversation" | "log">("conversation");

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
          // Take last ~80 chars of the latest text block
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

  // Keep a ref to tasks for SSE handler (avoid stale closure)
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;

  // Keep a ref to selectedTask for SSE handler (task_id guard)
  const selectedTaskRef = useRef(selectedTask);
  selectedTaskRef.current = selectedTask;

  // Debounced board sync: coalesces rapid board_sync SSE events into a
  // single fetchTasks() call after 500ms of quiet.
  const boardSyncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debouncedBoardSync = useCallback(() => {
    if (boardSyncTimerRef.current !== null) {
      clearTimeout(boardSyncTimerRef.current);
    }
    boardSyncTimerRef.current = setTimeout(async () => {
      boardSyncTimerRef.current = null;
      try {
        const updated = await fetchTasks();
        setTasks(updated);
      } catch {
        // Silently ignore -- next sync event will retry
      }
    }, 500);
  }, []);

  // Clean up timer on unmount
  useEffect(() => {
    return () => {
      if (boardSyncTimerRef.current !== null) {
        clearTimeout(boardSyncTimerRef.current);
      }
    };
  }, []);

  const addToast = useCallback(
    (text: string, type: "success" | "error") => {
      const id = ++toastIdCounter;
      setToasts((prev) => [...prev, { id, text, type }]);
    },
    [],
  );

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

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
        // Keep max 500 entries
        return next.length > 500 ? next.slice(-500) : next;
      });
    },
    [],
  );

  // SSE event handler
  const handleSSEEvent = useCallback(
    (event: SSEEvent) => {
      switch (event.type) {
        case "status_change": {
          const newStatus = event.data.status as TaskStatus;
          // Update card position in real-time
          setTasks((prev) =>
            prev.map((t) =>
              t.id === event.task_id ? { ...t, status: newStatus } : t,
            ),
          );
          addLogEntry(
            event.task_id,
            `Status changed to ${newStatus}`,
            event.timestamp,
          );
          // REVIEW_NEEDS_HUMAN: toast + auto-switch to Review tab + auto-select task
          if (newStatus === "review_needs_human") {
            addToast(
              `[${event.task_id}] Review needs human decision`,
              "error",
            );
            setBottomPanel("review");
            // Auto-select the task that needs attention (fetch full data first)
            fetchTask(event.task_id)
              .then((updated) => {
                setTasks((prev) =>
                  prev.map((t) => (t.id === updated.id ? updated : t)),
                );
                setSelectedTask(updated);
              })
              .catch(() => {
                // Fallback: select from existing tasks
                setTasks((prev) => {
                  const t = prev.find((x) => x.id === event.task_id);
                  if (t) setSelectedTask({ ...t, status: newStatus });
                  return prev;
                });
              });
            break;
          }
          // Fetch full task data for the updated task to get execution/review state
          fetchTask(event.task_id)
            .then((updated) => {
              setTasks((prev) =>
                prev.map((t) => (t.id === updated.id ? updated : t)),
              );
              // Update selected task if it matches
              setSelectedTask((sel) =>
                sel && sel.id === updated.id ? updated : sel,
              );
            })
            .catch(() => {
              // Ignore fetch errors -- the status is already updated optimistically
            });
          break;
        }
        case "log": {
          const msg =
            typeof event.data.message === "string"
              ? event.data.message
              : JSON.stringify(event.data);
          const logSource = typeof event.data.source === "string"
            ? event.data.source
            : undefined;
          addLogEntry(event.task_id, msg, event.timestamp, logSource);
          break;
        }
        case "plan_status_change": {
          const newPlanStatus = event.data.plan_status as string;
          // Capture structured error info on failure
          const errorPatch: Partial<Task> =
            newPlanStatus === "failed"
              ? {
                  plan_error_type: (event.data.error_type as string) || undefined,
                  plan_error_message: (event.data.error_message as string) || undefined,
                }
              : { plan_error_type: undefined, plan_error_message: undefined };
          setTasks((prev) =>
            prev.map((t) =>
              t.id === event.task_id
                ? { ...t, plan_status: newPlanStatus as Task["plan_status"], ...errorPatch }
                : t,
            ),
          );
          // Update selected task inline
          setSelectedTask((sel) =>
            sel && sel.id === event.task_id
              ? { ...sel, plan_status: newPlanStatus as Task["plan_status"], ...errorPatch }
              : sel,
          );
          // On terminal states, fetch full task to get updated description
          if (newPlanStatus === "ready" || newPlanStatus === "failed") {
            fetchTask(event.task_id)
              .then((updated) => {
                setTasks((prev) =>
                  prev.map((t) => (t.id === updated.id ? updated : t)),
                );
                setSelectedTask((sel) =>
                  sel && sel.id === updated.id ? updated : sel,
                );
              })
              .catch(() => { /* ignore */ });
          }
          break;
        }
        case "alert": {
          const alertMsg =
            typeof event.data.error === "string"
              ? event.data.error
              : JSON.stringify(event.data);
          addToast(`[${event.task_id}] ${alertMsg}`, "error");
          addLogEntry(event.task_id, `ALERT: ${alertMsg}`, event.timestamp);
          break;
        }
        case "review_progress": {
          const completed = event.data.completed as number;
          const total = event.data.total as number;
          const phase = (event.data.phase as string) ?? "";
          addLogEntry(
            event.task_id,
            `Review progress: ${completed}/${total} -- ${phase}`,
            event.timestamp,
            "review",
          );
          // SSE task_id guard: only update phase if this event is for the selected task
          if (event.task_id === selectedTaskRef.current?.id) {
            setReviewPhase(phase);
          }
          break;
        }
        case "execution_paused": {
          const paused = event.data.paused as boolean;
          const projectId = event.task_id; // task_id carries project_id for this event
          setProjects((prev) =>
            prev.map((p) =>
              p.id === projectId ? { ...p, execution_paused: paused } : p,
            ),
          );
          addToast(
            `[${projectId}] Execution ${paused ? "paused" : "resumed"}`,
            paused ? "error" : "success",
          );
          break;
        }
        case "review_gate_changed": {
          const gateEnabled = event.data.review_gate_enabled as boolean;
          const gateProjectId = event.task_id;
          setProjects((prev) =>
            prev.map((p) =>
              p.id === gateProjectId
                ? { ...p, review_gate_enabled: gateEnabled }
                : p,
            ),
          );
          addToast(
            `[${gateProjectId}] Review gate ${gateEnabled ? "enabled" : "disabled"}`,
            gateEnabled ? "success" : "error",
          );
          break;
        }
        case "review_started": {
          // Update review_status on the task in local state
          setTasks((prev) =>
            prev.map((t) =>
              t.id === event.task_id
                ? { ...t, review_status: "running" as const }
                : t,
            ),
          );
          // Clear stale phase when a new review starts for the selected task
          if (event.task_id === selectedTaskRef.current?.id) {
            setReviewPhase("");
          }
          addLogEntry(
            event.task_id,
            "Review pipeline started",
            event.timestamp,
            "review",
          );
          break;
        }
        case "review_failed": {
          // Update review_status on the task in local state
          setTasks((prev) =>
            prev.map((t) =>
              t.id === event.task_id
                ? { ...t, review_status: "failed" as const }
                : t,
            ),
          );
          // Also refresh task data from server
          fetchTask(event.task_id)
            .then((updated) => {
              setTasks((prev) =>
                prev.map((t) => (t.id === updated.id ? updated : t)),
              );
              setSelectedTask((sel) =>
                sel && sel.id === updated.id ? updated : sel,
              );
            })
            .catch(() => { /* ignore */ });
          break;
        }
        case "execution_stream": {
          // Normalize the raw event_dict and append to streamEvents for this task
          const normalized = normalizeStreamEvents(
            [event.data as Record<string, unknown>],
            `sse-${Date.now()}`,
            event.timestamp,
          );
          if (normalized.length > 0) {
            setStreamEvents((prev) => {
              const existing = prev[event.task_id] ?? [];
              const updated = [...existing, ...normalized];
              // Cap at 2000 events per task
              return {
                ...prev,
                [event.task_id]: updated.length > 2000 ? updated.slice(-2000) : updated,
              };
            });
          }
          break;
        }
        case "board_sync": {
          // Debounced full board refresh -- coalesces rapid events
          debouncedBoardSync();
          break;
        }
        case "process_failed": {
          const failError =
            typeof event.data.error === "string"
              ? event.data.error
              : "Process crashed";
          const failType = event.data.subprocess_type as string;
          const failPid = event.data.pid as number;
          addToast(
            `[${event.task_id}] ${failError}`,
            "error",
          );
          addLogEntry(
            event.task_id,
            `PROCESS FAILED: ${failType} pid=${failPid} -- ${failError}`,
            event.timestamp,
          );
          break;
        }
      }
    },
    [addToast, addLogEntry, debouncedBoardSync],
  );

  const { connected } = useSSE(handleSSEEvent);

  const loadData = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([fetchProjects(), fetchTasks()]);
      setProjects(p);
      setTasks(t);

      // Initialize selected projects from localStorage, or default to primary
      const saved = loadSelectedProjects();
      if (saved !== null) {
        // Keep only IDs that still exist
        const validIds = saved.filter((id) => p.some((proj) => proj.id === id));
        // Add any new projects not in saved
        const newIds = p
          .filter((proj) => !saved.includes(proj.id))
          .map((proj) => proj.id);
        setSelectedProjects([...validIds, ...newIds]);
      } else {
        // First time: default to primary project(s), or first project if none marked
        const primaryIds = p.filter((proj) => proj.is_primary).map((proj) => proj.id);
        if (primaryIds.length > 0) {
          setSelectedProjects(primaryIds);
        } else if (p.length > 0) {
          setSelectedProjects([p[0].id]);
        } else {
          setSelectedProjects([]);
        }
      }
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to load data";
      addToast(msg, "error");
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Persist selected projects to localStorage
  const handleSelectedProjectsChange = useCallback((ids: string[]) => {
    setSelectedProjects(ids);
    saveSelectedProjects(ids);
  }, []);

  const runningCount = tasks.filter(
    (t) => t.status === "running" || t.plan_status === "generating"
  ).length;

  // Apply global filters (status + search) to all tasks
  const globallyFiltered = tasks.filter((t) => {
    if (filterStatus && t.status !== filterStatus) return false;
    if (
      searchQuery &&
      !t.title.toLowerCase().includes(searchQuery.toLowerCase()) &&
      !t.local_task_id.toLowerCase().includes(searchQuery.toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  // Determine which projects to show swim lanes for
  const activeProjectIds =
    selectedProjects.length > 0
      ? selectedProjects
      : projects.map((p) => p.id);

  // Group filtered tasks by project
  const tasksByProject = new Map<string, Task[]>();
  for (const pid of activeProjectIds) {
    tasksByProject.set(
      pid,
      globallyFiltered.filter((t) => t.project_id === pid),
    );
  }

  // Unique task IDs for log filtering
  const taskIds = [...new Set(tasks.map((t) => t.id))];

  const handleSyncAll = async () => {
    setSyncing(true);
    try {
      const result = await syncAll();
      const totalAdded = result.results.reduce((s, r) => s + r.added, 0);
      const totalUpdated = result.results.reduce((s, r) => s + r.updated, 0);
      addToast(
        `Sync complete: ${totalAdded} added, ${totalUpdated} updated`,
        "success",
      );
      // Refresh tasks after sync
      const updatedTasks = await fetchTasks();
      setTasks(updatedTasks);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Sync failed";
      addToast(msg, "error");
    } finally {
      setSyncing(false);
    }
  };

  const handleSyncProject = useCallback(
    async (projectId: string) => {
      setSyncingProjects((prev) => new Set(prev).add(projectId));
      try {
        const result = await syncProject(projectId);
        addToast(
          `[${projectId}] Sync: ${result.added} added, ${result.updated} updated`,
          "success",
        );
        // Refresh tasks after sync
        const updatedTasks = await fetchTasks();
        setTasks(updatedTasks);
      } catch (err) {
        const msg =
          err instanceof ApiError ? err.detail : "Sync failed";
        addToast(msg, "error");
      } finally {
        setSyncingProjects((prev) => {
          const next = new Set(prev);
          next.delete(projectId);
          return next;
        });
      }
    },
    [addToast],
  );

  const handleMoveTask = async (
    taskId: string,
    newStatus: TaskStatus,
    opts?: { reason?: string },
  ) => {
    // Find the task to get its updated_at for optimistic locking
    const task = tasksRef.current.find((t) => t.id === taskId);
    const expectedUpdatedAt = task?.updated_at;

    // Optimistic update: move card immediately
    setTasks((prev) =>
      prev.map((t) => (t.id === taskId ? { ...t, status: newStatus } : t)),
    );

    try {
      const updated = await updateTaskStatus(taskId, newStatus, {
        reason: opts?.reason,
        expected_updated_at: expectedUpdatedAt,
      });
      // Replace with server response to ensure consistency
      setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)));

      // Auto-focus when dragging to REVIEW: open ReviewPanel with task selected
      if (newStatus === "review") {
        setSelectedTask(updated);
        setBottomPanel("review");
      }
    } catch (err) {
      // Revert optimistic update on error
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
          // Review gate or plan validity blocked: open the review submit modal
          const blockedTask = tasksRef.current.find((t) => t.id === taskId);
          if (blockedTask) {
            setReviewSubmitTask(blockedTask);
          } else {
            addToast(err.detail, "error");
          }
        } else if (conflict) {
          // Optimistic lock conflict: auto-refresh the task
          addToast("Task was just updated. Refreshing...", "error");
          try {
            const refreshed = await fetchTask(taskId);
            setTasks((prev) =>
              prev.map((t) => (t.id === taskId ? refreshed : t)),
            );
          } catch {
            // Fallback: full refresh
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
  };

  const handleSelectTask = useCallback((task: Task) => {
    setSelectedTask(task);
    // Clear stale review phase from previously selected task
    setReviewPhase("");
    // Switch to review panel if the task is in a review state
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
      // The SSE status_change event will update the card position
      setSelectedTask(null);
    },
    [addToast],
  );

  const handleImported = useCallback(async () => {
    // Reload projects and tasks after import
    try {
      const [p, t] = await Promise.all([fetchProjects(), fetchTasks()]);
      setProjects(p);
      setTasks(t);
      // Auto-select the new project
      setSelectedProjects((prev) => {
        const newIds = p
          .filter((proj) => !prev.includes(proj.id))
          .map((proj) => proj.id);
        const updated = [...prev, ...newIds];
        saveSelectedProjects(updated);
        return updated;
      });
      addToast("Project imported successfully", "success");
    } catch {
      // Data will be stale but not broken
    }
  }, [addToast]);

  const handleTaskCreated = useCallback(async (synced?: boolean) => {
    // Refresh tasks after creation
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

  const handleReviewSubmitted = useCallback(
    async (taskId: string) => {
      setReviewSubmitTask(null);
      addToast(`Task ${taskId} submitted for review`, "success");
      // Refresh the task to get updated status
      try {
        const refreshed = await fetchTask(taskId);
        setTasks((prev) => prev.map((t) => (t.id === taskId ? refreshed : t)));
        // Auto-focus in ReviewPanel
        setSelectedTask(refreshed);
        setBottomPanel("review");
      } catch {
        // Fallback: full refresh
        const updatedTasks = await fetchTasks();
        setTasks(updatedTasks);
      }
    },
    [addToast],
  );

  const handleSendToReview = useCallback(
    (task: Task) => {
      setReviewSubmitTask(task);
    },
    [],
  );

  const handleEditTask = useCallback(
    (task: Task) => {
      setEditTask(task);
    },
    [],
  );

  const handleEditSaved = useCallback(
    (updated: Task) => {
      setEditTask(null);
      setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
      // Update selected task if it matches
      setSelectedTask((sel) =>
        sel && sel.id === updated.id ? updated : sel,
      );
      addToast("Task updated", "success");
    },
    [addToast],
  );

  const handleTaskDeleted = useCallback(async () => {
    // Refresh tasks after deletion
    try {
      const updatedTasks = await fetchTasks();
      setTasks(updatedTasks);
      addToast("Task deleted", "success");
    } catch {
      // Data will be stale but not broken
    }
  }, [addToast]);

  const soloLane = activeProjectIds.length === 1;

  return (
    <div className="flex flex-col h-screen bg-gray-100">
      {/* Toasts */}
      <Toast messages={toasts} onDismiss={dismissToast} />

      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-gray-200 shadow-sm">
        <h1 className="text-lg font-bold text-gray-900 tracking-tight">
          HelixOS Dashboard
        </h1>
        <div className="flex items-center gap-4">
          {/* Connection status indicator */}
          <span
            className="flex items-center gap-1.5 text-xs"
            title={connected ? "SSE connected" : "SSE disconnected"}
          >
            <span
              className={`inline-block w-2 h-2 rounded-full ${
                connected ? "bg-green-500" : "bg-red-500"
              }`}
            />
            <span className={connected ? "text-green-700" : "text-red-600"}>
              {connected ? "Connected" : "Disconnected"}
            </span>
          </span>

          <button
            onClick={() =>
              setBottomPanel((prev) => (prev === "running" ? "log" : "running"))
            }
            className={`text-sm cursor-pointer rounded-md px-2 py-1 transition-colors ${
              bottomPanel === "running"
                ? "bg-indigo-100 text-indigo-700"
                : "text-gray-500 hover:bg-gray-100"
            }`}
            title="Toggle running jobs panel"
          >
            Running:{" "}
            <span className="font-semibold text-indigo-600">
              {runningCount}
            </span>
          </button>
          <button
            onClick={() => setShowImportModal(true)}
            className="rounded-md bg-gray-100 border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-200 transition-colors"
            title="Import a project directory into the orchestrator"
          >
            Import Project
          </button>
          <button
            onClick={handleSyncAll}
            disabled={syncing}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            title="Sync TASKS.md for all projects"
          >
            {syncing ? "Syncing..." : "Sync All"}
          </button>
        </div>
      </header>

      {/* Filter bar */}
      <div className="flex items-center gap-3 px-6 py-2 bg-white border-b border-gray-100">
        <ProjectSelector
          projects={projects}
          selectedIds={selectedProjects}
          onChange={handleSelectedProjectsChange}
          onImportClick={() => setShowImportModal(true)}
        />

        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 bg-white"
        >
          <option value="">All statuses</option>
          <option value="backlog">Backlog</option>
          <option value="review">Review</option>
          <option value="review_auto_approved">Auto-Approved</option>
          <option value="review_needs_human">Needs Human</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
          <option value="blocked">Blocked</option>
        </select>

        <input
          type="text"
          placeholder="Search tasks..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 bg-white w-48"
        />
      </div>

      {/* Swim lane area */}
      <main className="flex-1 overflow-y-auto p-4 min-h-0">
        {activeProjectIds.length === 0 ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            Select at least one project to view tasks
          </div>
        ) : (
          <div
            className={`flex flex-col gap-0 ${soloLane ? "h-full" : ""}`}
          >
            {activeProjectIds.map((pid, idx) => {
              const project = projects.find((p) => p.id === pid);
              if (!project) return null;
              const laneTasks = tasksByProject.get(pid) ?? [];

              return (
                <div key={pid} className={`flex flex-col ${soloLane ? "flex-1" : ""} min-h-0`}>
                  {/* Divider between swim lanes */}
                  {idx > 0 && (
                    <div className="h-px bg-gray-300 my-2 flex-shrink-0" />
                  )}
                  <SwimLane
                    project={project}
                    tasks={laneTasks}
                    loading={loading}
                    onMoveTask={handleMoveTask}
                    onSelectTask={handleSelectTask}
                    solo={soloLane}
                    syncing={syncingProjects.has(pid)}
                    onSync={() => handleSyncProject(pid)}
                    onNewTask={() => {
                      setEnrichTitle("");
                      setAutoEnrich(false);
                      setNewTaskProject(project);
                    }}
                    onTaskCreated={handleTaskCreated}
                    onError={(msg) => addToast(msg, "error")}
                    onPauseToggle={(paused) =>
                      setProjects((prev) =>
                        prev.map((p) =>
                          p.id === pid
                            ? { ...p, execution_paused: paused }
                            : p,
                        ),
                      )
                    }
                    onEnrichExpand={(title) => {
                      setEnrichTitle(title);
                      setAutoEnrich(true);
                      setNewTaskProject(project);
                    }}
                    onTaskDeleted={handleTaskDeleted}
                    onSendToReview={handleSendToReview}
                    onEditTask={handleEditTask}
                    onTaskUpdated={(updated) =>
                      setTasks((prev) =>
                        prev.map((t) => (t.id === updated.id ? updated : t)),
                      )
                    }
                    streamSummaries={streamSummaries}
                    onStarted={(count) =>
                      addToast(`Started ${count} planned task(s)`, "success")
                    }
                  />
                </div>
              );
            })}
          </div>
        )}
      </main>

      {/* Resizable divider */}
      <ResizableDivider
        panelHeight={bottomPanelHeight}
        onHeightChange={setBottomPanelHeight}
      />

      {/* Bottom panel: ExecutionLog / ReviewPanel */}
      <div className="border-t border-gray-300 bg-white flex flex-col min-h-0" style={{ height: bottomPanelHeight }}>
        {/* Panel tabs */}
        <div className="flex items-center border-b border-gray-200 px-4">
          <button
            onClick={() => { setBottomPanel("log"); setViewMode("conversation"); }}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
              bottomPanel === "log" && viewMode === "conversation"
                ? "border-indigo-500 text-indigo-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
            title="View structured conversation from task execution"
          >
            Conversation
          </button>
          <button
            onClick={() => { setBottomPanel("log"); setViewMode("log"); }}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
              bottomPanel === "log" && viewMode === "log"
                ? "border-indigo-500 text-indigo-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
            title="View plain execution log entries"
          >
            Plain Log
          </button>
          <button
            onClick={() => setBottomPanel("review")}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
              bottomPanel === "review"
                ? "border-indigo-500 text-indigo-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
            title="View review progress and make approval decisions"
          >
            Review
          </button>
          <button
            onClick={() => setBottomPanel("running")}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
              bottomPanel === "running"
                ? "border-indigo-500 text-indigo-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
            title="View currently running jobs across all projects"
          >
            Running{runningCount > 0 ? ` (${runningCount})` : ""}
          </button>

          {/* Selected task indicator with clear button */}
          {selectedTask && (
            <div className="ml-auto flex items-center gap-1.5">
              <span className="text-xs text-gray-500">Focused:</span>
              <span className="text-xs font-mono font-medium text-indigo-600">
                {selectedTask.local_task_id}
              </span>
              <button
                onClick={() => { setSelectedTask(null); setReviewPhase(""); }}
                className="ml-0.5 px-1 text-gray-400 hover:text-gray-600 text-xs rounded hover:bg-gray-100"
                title="Clear task focus"
              >
                x
              </button>
            </div>
          )}
        </div>

        {/* Panel content */}
        <div className="flex-1 min-h-0">
          {bottomPanel === "log" && viewMode === "conversation" && selectedTask ? (
            <ConversationView
              taskId={selectedTask.id}
              taskStatus={selectedTask.status}
              executionStartedAt={selectedTask.execution?.started_at}
              liveItems={streamEvents[selectedTask.id] ?? []}
              onToggleView={() => setViewMode("log")}
            />
          ) : bottomPanel === "log" ? (
            <ExecutionLog
              entries={logEntries}
              taskIds={taskIds}
              selectedTaskId={selectedTask?.id}
              selectedTaskStatus={selectedTask?.status}
              executionStartedAt={selectedTask?.execution?.started_at}
            />
          ) : bottomPanel === "review" ? (
            <ReviewPanel
              task={selectedTask}
              reviewPhase={reviewPhase}
              onDecisionSubmitted={handleReviewDecision}
              onError={(msg) => addToast(msg, "error")}
              onTaskUpdated={(updated) => {
                setTasks((prev) =>
                  prev.map((t) => (t.id === updated.id ? updated : t)),
                );
              }}
            />
          ) : (
            <RunningJobsPanel
              tasks={tasks}
              projects={projects}
              onSelectTask={handleSelectTask}
            />
          )}
        </div>
      </div>

      {/* Modals */}
      {showImportModal && (
        <ImportProjectModal
          onClose={() => setShowImportModal(false)}
          onImported={handleImported}
          onError={(msg) => addToast(msg, "error")}
        />
      )}
      {newTaskProject && (
        <NewTaskModal
          projectId={newTaskProject.id}
          projectName={newTaskProject.name}
          onClose={() => {
            setNewTaskProject(null);
            setEnrichTitle("");
            setAutoEnrich(false);
          }}
          onCreated={handleTaskCreated}
          onError={(msg) => addToast(msg, "error")}
          initialTitle={enrichTitle}
          autoEnrich={autoEnrich}
        />
      )}
      {reviewSubmitTask && (
        <ReviewSubmitModal
          task={reviewSubmitTask}
          onClose={() => setReviewSubmitTask(null)}
          onSubmitted={handleReviewSubmitted}
          onError={(msg) => addToast(msg, "error")}
        />
      )}
      {editTask && (
        <EditTaskModal
          task={editTask}
          onClose={() => setEditTask(null)}
          onSaved={handleEditSaved}
          onError={(msg) => addToast(msg, "error")}
        />
      )}
    </div>
  );
}

export default App;
