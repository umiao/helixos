import ConversationView from "./ConversationView";
import ExecutionLog from "./ExecutionLog";
import ReviewPanel from "./ReviewPanel";
import RunningJobsPanel from "./RunningJobsPanel";
import type { LogEntry } from "./ExecutionLog";
import type { Task, Project, StreamDisplayItem } from "../types";

interface BottomPanelContainerProps {
  bottomPanel: "log" | "review" | "running";
  setBottomPanel: (panel: "log" | "review" | "running") => void;
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
}: BottomPanelContainerProps) {
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
            onDecisionSubmitted={onReviewDecision}
            onError={onError}
            onTaskUpdated={onTaskUpdated}
          />
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
