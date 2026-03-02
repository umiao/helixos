import { useCallback, useEffect, useRef, useState } from "react";
import SwimLane from "./components/SwimLane";
import ExecutionLog, { type LogEntry } from "./components/ExecutionLog";
import ReviewPanel from "./components/ReviewPanel";
import Toast, { type ToastMessage } from "./components/Toast";
import ProjectSelector, {
  loadSelectedProjects,
  saveSelectedProjects,
} from "./components/ProjectSelector";
import ImportProjectModal from "./components/ImportProjectModal";
import NewTaskModal from "./components/NewTaskModal";
import useSSE, { type SSEEvent } from "./hooks/useSSE";
import {
  fetchProjects,
  fetchTasks,
  fetchTask,
  syncAll,
  syncProject,
  updateTaskStatus,
  ApiError,
} from "./api";
import type { Project, Task, TaskStatus } from "./types";

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
  const [bottomPanel, setBottomPanel] = useState<"log" | "review">("log");
  const [showImportModal, setShowImportModal] = useState(false);
  const [newTaskProject, setNewTaskProject] = useState<Project | null>(null);

  // Keep a ref to tasks for SSE handler (avoid stale closure)
  const tasksRef = useRef(tasks);
  tasksRef.current = tasks;

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
    (task_id: string, message: string, timestamp: string) => {
      setLogEntries((prev) => {
        const entry: LogEntry = {
          id: ++logIdCounter,
          task_id,
          message,
          timestamp,
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
          addLogEntry(event.task_id, msg, event.timestamp);
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
          addLogEntry(
            event.task_id,
            `Review progress: ${completed}/${total}`,
            event.timestamp,
          );
          break;
        }
      }
    },
    [addToast, addLogEntry],
  );

  const { connected } = useSSE(handleSSEEvent);

  const loadData = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([fetchProjects(), fetchTasks()]);
      setProjects(p);
      setTasks(t);

      // Initialize selected projects from localStorage, or select all
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
        // First time: select all
        setSelectedProjects(p.map((proj) => proj.id));
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

  const runningCount = tasks.filter((t) => t.status === "running").length;

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

  const handleMoveTask = async (taskId: string, newStatus: TaskStatus) => {
    // Optimistic update: move card immediately
    setTasks((prev) =>
      prev.map((t) => (t.id === taskId ? { ...t, status: newStatus } : t)),
    );

    try {
      const updated = await updateTaskStatus(taskId, newStatus);
      // Replace with server response to ensure consistency
      setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)));
    } catch (err) {
      // Revert optimistic update on error
      const original = tasksRef.current.find((t) => t.id === taskId);
      if (original) {
        setTasks((prev) =>
          prev.map((t) => (t.id === taskId ? original : t)),
        );
      }
      const msg =
        err instanceof ApiError
          ? `Invalid transition: ${err.detail}`
          : "Failed to update task";
      addToast(msg, "error");
    }
  };

  const handleSelectTask = useCallback((task: Task) => {
    setSelectedTask(task);
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

  const handleTaskCreated = useCallback(async () => {
    // Refresh tasks after creation
    try {
      const updatedTasks = await fetchTasks();
      setTasks(updatedTasks);
      addToast("Task created", "success");
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

          <span className="text-sm text-gray-500">
            Running:{" "}
            <span className="font-semibold text-indigo-600">
              {runningCount}
            </span>
          </span>
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
                    onNewTask={() => setNewTaskProject(project)}
                    onTaskCreated={handleTaskCreated}
                    onError={(msg) => addToast(msg, "error")}
                  />
                </div>
              );
            })}
          </div>
        )}
      </main>

      {/* Bottom panel: ExecutionLog / ReviewPanel */}
      <div className="h-56 border-t border-gray-300 bg-white flex flex-col min-h-0">
        {/* Panel tabs */}
        <div className="flex items-center border-b border-gray-200 px-4">
          <button
            onClick={() => setBottomPanel("log")}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
              bottomPanel === "log"
                ? "border-indigo-500 text-indigo-700"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
            title="View real-time execution logs from task runners"
          >
            Execution Log
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
        </div>

        {/* Panel content */}
        <div className="flex-1 min-h-0">
          {bottomPanel === "log" ? (
            <ExecutionLog entries={logEntries} taskIds={taskIds} />
          ) : (
            <ReviewPanel
              task={selectedTask}
              onDecisionSubmitted={handleReviewDecision}
              onError={(msg) => addToast(msg, "error")}
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
          onClose={() => setNewTaskProject(null)}
          onCreated={handleTaskCreated}
          onError={(msg) => addToast(msg, "error")}
        />
      )}
    </div>
  );
}

export default App;
