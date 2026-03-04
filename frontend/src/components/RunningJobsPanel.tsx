/**
 * RunningJobsPanel -- displays all currently executing tasks across projects.
 * Shows task ID, title, project, elapsed time, and execution phase.
 * Auto-updates in real-time via the parent's SSE-driven task state.
 */

import { useEffect, useState } from "react";
import type { Project, Task } from "../types";

interface RunningJobsPanelProps {
  tasks: Task[];
  projects: Project[];
  onSelectTask: (task: Task) => void;
}

function formatElapsed(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function ElapsedTimer({ startedAt }: { startedAt: string }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = new Date(startedAt).getTime();
    const update = () => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [startedAt]);

  return (
    <span className="font-mono tabular-nums text-indigo-600 font-semibold">
      {formatElapsed(elapsed)}
    </span>
  );
}

export default function RunningJobsPanel({
  tasks,
  projects,
  onSelectTask,
}: RunningJobsPanelProps) {
  const runningTasks = tasks.filter(
    (t) => t.status === "running" || t.plan_status === "generating"
  );

  const projectMap = new Map(projects.map((p) => [p.id, p]));

  if (runningTasks.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        <div className="text-center">
          <svg
            className="w-8 h-8 mx-auto mb-2 text-gray-300"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"
            />
          </svg>
          No jobs currently running
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto p-3 space-y-2">
      {runningTasks.map((task) => {
        const isPlanGenerating = task.plan_status === "generating" && task.status !== "running";
        const project = projectMap.get(task.project_id);
        const startedAt = isPlanGenerating ? null : task.execution?.started_at;
        const phase = isPlanGenerating ? "Planning" : (task.execution?.result || "executing");
        const retryCount = isPlanGenerating ? 0 : (task.execution?.retry_count ?? 0);

        const borderClass = isPlanGenerating
          ? "border-blue-100 bg-blue-50/50 hover:bg-blue-50"
          : "border-indigo-100 bg-indigo-50/50 hover:bg-indigo-50";
        const pingClass = isPlanGenerating ? "bg-blue-400" : "bg-indigo-400";
        const dotClass = isPlanGenerating ? "bg-blue-500" : "bg-indigo-500";
        const idClass = isPlanGenerating ? "text-blue-700" : "text-indigo-700";

        return (
          <button
            key={task.id}
            onClick={() => onSelectTask(task)}
            className={`flex items-center gap-3 rounded-lg border ${borderClass} px-4 py-3 text-left transition-colors w-full`}
          >
            {/* Pulsing indicator */}
            <div className="flex-shrink-0">
              <span className="relative flex h-3 w-3">
                <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${pingClass} opacity-75`} />
                <span className={`relative inline-flex rounded-full h-3 w-3 ${dotClass}`} />
              </span>
            </div>

            {/* Task info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <span className={`text-xs font-mono font-semibold ${idClass}`}>
                  {task.local_task_id}
                </span>
                {project && (
                  <span className="text-xs text-gray-400 truncate">
                    {project.name}
                  </span>
                )}
              </div>
              <p className="text-sm font-medium text-gray-900 truncate">
                {task.title}
              </p>
              <div className="flex items-center gap-3 mt-1">
                <span className="text-xs text-gray-500 truncate">
                  {phase}
                </span>
                {retryCount > 0 && (
                  <span className="text-xs text-amber-600">
                    retry {retryCount}
                  </span>
                )}
              </div>
            </div>

            {/* Elapsed time */}
            <div className="flex-shrink-0 text-right">
              {startedAt ? (
                <ElapsedTimer startedAt={startedAt} />
              ) : (
                <span className="text-xs text-gray-400">--:--</span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}
