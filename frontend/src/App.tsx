import { useCallback, useEffect, useState } from "react";
import KanbanBoard from "./components/KanbanBoard";
import Toast, { type ToastMessage } from "./components/Toast";
import {
  fetchProjects,
  fetchTasks,
  syncAll,
  updateTaskStatus,
  ApiError,
} from "./api";
import type { Project, Task, TaskStatus } from "./types";

let toastIdCounter = 0;

function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [filterProject, setFilterProject] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

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

  const loadData = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([fetchProjects(), fetchTasks()]);
      setProjects(p);
      setTasks(t);
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

  const runningCount = tasks.filter((t) => t.status === "running").length;

  const filteredTasks = tasks.filter((t) => {
    if (filterProject && t.project_id !== filterProject) return false;
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
      const original = tasks.find((t) => t.id === taskId);
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
          <span className="text-sm text-gray-500">
            Running:{" "}
            <span className="font-semibold text-indigo-600">
              {runningCount}
            </span>
          </span>
          <button
            onClick={handleSyncAll}
            disabled={syncing}
            className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {syncing ? "Syncing..." : "Sync All"}
          </button>
        </div>
      </header>

      {/* Filter bar */}
      <div className="flex items-center gap-3 px-6 py-2 bg-white border-b border-gray-100">
        <select
          value={filterProject}
          onChange={(e) => setFilterProject(e.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 bg-white"
        >
          <option value="">All projects</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>

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

      {/* Kanban board */}
      <main className="flex-1 overflow-hidden p-4">
        <KanbanBoard
          tasks={filteredTasks}
          loading={loading}
          onMoveTask={handleMoveTask}
        />
      </main>
    </div>
  );
}

export default App;
