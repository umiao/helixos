import { useCallback, useEffect, useMemo, useState } from "react";
import SwimLane from "./components/SwimLane";
import ResizableDivider, {
  loadPanelHeight,
} from "./components/ResizableDivider";
import Toast from "./components/Toast";
import ProjectSelector from "./components/ProjectSelector";
import ImportProjectModal from "./components/ImportProjectModal";
import NewTaskModal from "./components/NewTaskModal";
import ReviewSubmitModal from "./components/ReviewSubmitModal";
import EditTaskModal from "./components/EditTaskModal";
import { ErrorBoundary } from "./components/ErrorBoundary";
import BottomPanelContainer from "./components/BottomPanelContainer";
import { fetchProjects, fetchTasks, ApiError, syncProject } from "./api";
import { useToasts } from "./hooks/useToasts";
import { useTaskState } from "./hooks/useTaskState";
import { useProjectState } from "./hooks/useProjectState";
import { useSSEHandler } from "./hooks/useSSEHandler";
import type { Project, Task } from "./types";

function App() {
  const { toasts, addToast, dismissToast } = useToasts();

  const taskState = useTaskState(addToast);
  const {
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
    selectedTaskRef,
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
  } = taskState;

  const projectState = useProjectState(addToast, setTasks);
  const {
    projects,
    setProjects,
    selectedProjects,
    syncing,
    syncingProjects,
    handleSelectedProjectsChange,
    handleSyncAll,
    handleSyncProject,
    handleImported,
    initializeProjects,
  } = projectState;

  const { connected } = useSSEHandler({
    addToast,
    addLogEntry,
    setTasks,
    setProjects,
    setSelectedTask,
    setBottomPanel,
    setReviewPhase,
    setStreamEvents,
    selectedTaskRef,
  });

  // Initial data load
  const loadData = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([fetchProjects(), fetchTasks()]);
      setTasks(t);
      initializeProjects(p);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to load data";
      addToast(msg, "error");
    } finally {
      setLoading(false);
    }
  }, [addToast, setTasks, setLoading, initializeProjects]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Modal state
  const [showImportModal, setShowImportModal] = useState(false);
  const [newTaskProject, setNewTaskProject] = useState<Project | null>(null);
  const [enrichTitle, setEnrichTitle] = useState("");
  const [autoEnrich, setAutoEnrich] = useState(false);
  const [bottomPanelHeight, setBottomPanelHeight] = useState(loadPanelHeight);

  // Derived: active project IDs and tasks grouped by project
  const activeProjectIds = useMemo(() =>
    selectedProjects.length > 0
      ? selectedProjects
      : projects.map((p) => p.id),
    [selectedProjects, projects],
  );

  const tasksByProject = useMemo(() => {
    const map = new Map<string, Task[]>();
    for (const pid of activeProjectIds) {
      map.set(pid, globallyFiltered.filter((t) => t.project_id === pid));
    }
    return map;
  }, [activeProjectIds, globallyFiltered]);

  const soloLane = activeProjectIds.length === 1;

  // Handler for plan confirmation: sync to pick up new tasks
  const handlePlanConfirmed = useCallback(
    async (taskId: string, writtenIds: string[]) => {
      addToast(
        `Created ${writtenIds.length} task(s) from plan: ${writtenIds.join(", ")}`,
        "success",
      );
      // Sync the project to bring new tasks into the board
      const task = tasks.find((t) => t.id === taskId);
      if (task) {
        try {
          await syncProject(task.project_id);
          const updatedTasks = await fetchTasks();
          setTasks(updatedTasks);
        } catch {
          // board_sync SSE will eventually refresh
        }
      }
    },
    [addToast, tasks, setTasks],
  );

  return (
    <div className="flex flex-col h-screen bg-gray-100">
      <Toast messages={toasts} onDismiss={dismissToast} />

      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-gray-200 shadow-sm">
        <h1 className="text-lg font-bold text-gray-900 tracking-tight">
          HelixOS Dashboard
        </h1>
        <div className="flex items-center gap-4">
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

        {/* Priority filter chips */}
        <div className="flex items-center gap-1 ml-2">
          <span className="text-xs text-gray-400 mr-0.5">Priority:</span>
          {["P0", "P1", "P2", "P3"].map((p) => {
            const active = filterPriorities.has(p);
            return (
              <button
                key={p}
                onClick={() =>
                  setFilterPriorities((prev) => {
                    const next = new Set(prev);
                    if (next.has(p)) next.delete(p);
                    else next.add(p);
                    return next;
                  })
                }
                className={`px-2 py-0.5 rounded text-xs font-medium cursor-pointer select-none transition-colors ${
                  active
                    ? "bg-indigo-600 text-white ring-1 ring-indigo-400"
                    : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                }`}
              >
                {p}
              </button>
            );
          })}
        </div>

        {/* Complexity filter chips */}
        <div className="flex items-center gap-1 ml-2">
          <span className="text-xs text-gray-400 mr-0.5">Size:</span>
          {["S", "M", "L"].map((c) => {
            const active = filterComplexities.has(c);
            return (
              <button
                key={c}
                onClick={() =>
                  setFilterComplexities((prev) => {
                    const next = new Set(prev);
                    if (next.has(c)) next.delete(c);
                    else next.add(c);
                    return next;
                  })
                }
                className={`px-2 py-0.5 rounded text-xs font-medium cursor-pointer select-none transition-colors ${
                  active
                    ? "bg-emerald-600 text-white ring-1 ring-emerald-400"
                    : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                }`}
              >
                {c}
              </button>
            );
          })}
        </div>

        {(filterPriorities.size > 0 || filterComplexities.size > 0) && (
          <button
            onClick={clearFilters}
            className="px-2 py-0.5 rounded text-xs text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          >
            Clear
          </button>
        )}
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

      <ResizableDivider
        panelHeight={bottomPanelHeight}
        onHeightChange={setBottomPanelHeight}
      />

      <ErrorBoundary name="Bottom Panel">
        <div className="border-t border-gray-300 bg-white flex flex-col min-h-0" style={{ height: bottomPanelHeight }}>
          <BottomPanelContainer
            bottomPanel={bottomPanel}
            setBottomPanel={setBottomPanel}
            viewMode={viewMode}
            setViewMode={setViewMode}
            selectedTask={selectedTask}
            setSelectedTask={setSelectedTask}
            setReviewPhase={setReviewPhase}
            reviewPhase={reviewPhase}
            logEntries={logEntries}
            taskIds={taskIds}
            streamEvents={streamEvents}
            tasks={tasks}
            projects={projects}
            runningCount={runningCount}
            onReviewDecision={handleReviewDecision}
            onSelectTask={handleSelectTask}
            onError={(msg) => addToast(msg, "error")}
            onTaskUpdated={(updated) =>
              setTasks((prev) =>
                prev.map((t) => (t.id === updated.id ? updated : t)),
              )
            }
            onPlanConfirmed={handlePlanConfirmed}
          />
        </div>
      </ErrorBoundary>

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
