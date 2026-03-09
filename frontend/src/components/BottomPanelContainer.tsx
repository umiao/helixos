import ConversationView from "./ConversationView";
import CostDashboard from "./CostDashboard";
import ExecutionLog from "./ExecutionLog";
import PlanReviewPanel from "./PlanReviewPanel";
import ReviewPanel from "./ReviewPanel";
import RunningJobsPanel from "./RunningJobsPanel";
import type { LogEntry } from "./ExecutionLog";
import type { Task, Project, StreamDisplayItem } from "../types";

export type BottomPanelTab = "log" | "review" | "plan" | "running" | "costs";

interface BottomPanelContainerProps {
  bottomPanel: BottomPanelTab;
  setBottomPanel: (panel: BottomPanelTab) => void;
  viewMode: "conversation" | "log";
  setViewMode: (mode: "conversation" | "log") => void;
  selectedTask: Task | null;
  setSelectedTask: (task: Task | null) => void;
  setReviewPhase: (phase: string) => void;
  reviewPhase: string;
  logEntries: LogEntry[];
  taskIds: string[];
  streamEvents: Record<string, StreamDisplayItem[]>;
  tasks: Task[];
  projects: Project[];
  runningCount: number;
  onReviewDecision: (taskId: string, decision: string) => void;
  onSelectTask: (task: Task) => void;
  onError: (msg: string) => void;
  onTaskUpdated: (updated: Task) => void;
  onPlanConfirmed: (taskId: string, writtenIds: string[]) => void;
}

export default function BottomPanelContainer({
  bottomPanel,
  setBottomPanel,
  viewMode,
  setViewMode,
  selectedTask,
  setSelectedTask,
  setReviewPhase,
  reviewPhase,
  logEntries,
  taskIds,
  streamEvents,
  tasks,
  projects,
  runningCount,
  onReviewDecision,
  onSelectTask,
  onError,
  onTaskUpdated,
  onPlanConfirmed,
}: BottomPanelContainerProps) {
  // Determine if the plan tab should be highlighted (task has actionable plan state)
  const showPlanBadge = selectedTask != null && (
    selectedTask.plan_status === "ready" ||
    selectedTask.plan_status === "generating" ||
    selectedTask.plan_status === "failed"
  );
  // Show animated dot on Conversation/Log tabs when task is running (AC4)
  const isRunning = selectedTask?.status === "running";
  return (
    <>
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
          Conversation{isRunning ? (
            <span className="ml-1 inline-block w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
          ) : null}
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
          Plain Log{isRunning ? (
            <span className="ml-1 inline-block w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
          ) : null}
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
          onClick={() => setBottomPanel("plan")}
          className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
            bottomPanel === "plan"
              ? "border-indigo-500 text-indigo-700"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
          title="Review generated plan and proposed tasks"
        >
          Plan{showPlanBadge ? (
            <span className={`ml-1 inline-block w-1.5 h-1.5 rounded-full ${
              selectedTask?.plan_status === "generating" ? "bg-blue-500 animate-pulse" :
              selectedTask?.plan_status === "ready" ? "bg-green-500" : "bg-red-500"
            }`} />
          ) : null}
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
        <button
          onClick={() => setBottomPanel("costs")}
          className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
            bottomPanel === "costs"
              ? "border-indigo-500 text-indigo-700"
              : "border-transparent text-gray-500 hover:text-gray-700"
          }`}
          title="View cost/usage breakdown by project"
        >
          Costs
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
            onDecisionSubmitted={onReviewDecision}
            onError={onError}
            onTaskUpdated={onTaskUpdated}
          />
        ) : bottomPanel === "plan" ? (
          selectedTask ? (
            <PlanReviewPanel
              task={selectedTask}
              onTaskUpdated={onTaskUpdated}
              onError={onError}
              onConfirmed={onPlanConfirmed}
            />
          ) : (
            <div className="flex items-center justify-center h-full text-gray-400 text-sm">
              Select a task to view its plan
            </div>
          )
        ) : bottomPanel === "costs" ? (
          <CostDashboard />
        ) : (
          <RunningJobsPanel
            tasks={tasks}
            projects={projects}
            onSelectTask={onSelectTask}
          />
        )}
      </div>
    </>
  );
}
