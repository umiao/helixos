/**
 * SwimLaneHeader -- per-project action bar with Launch, New Task, Sync buttons.
 * Also shows warning badges for limited-mode projects (missing TASKS.md or CLAUDE.md).
 */

import type { Project } from "../types";
import LaunchControl from "./LaunchControl";

interface SwimLaneHeaderProps {
  project: Project;
  taskCount: number;
  syncing: boolean;
  onSync: () => void;
  onNewTask: () => void;
  onError: (msg: string) => void;
}

export default function SwimLaneHeader({
  project,
  taskCount,
  syncing,
  onSync,
  onNewTask,
  onError,
}: SwimLaneHeaderProps) {
  // Detect limited-mode warnings (missing TASKS.md path or CLAUDE.md)
  const warnings: string[] = [];
  if (!project.repo_path) {
    warnings.push("No repo path");
  }
  if (!project.claude_md_path) {
    warnings.push("No CLAUDE.md");
  }

  return (
    <div className="flex items-center gap-3 px-4 py-1.5 bg-gray-200 border-b border-gray-300 rounded-t-md flex-shrink-0">
      {/* Project name + ID */}
      <h2 className="text-sm font-bold text-gray-800 tracking-tight">
        {project.name}
      </h2>
      <span className="text-xs text-gray-500 font-mono">{project.id}</span>

      {/* Warning badges */}
      {warnings.length > 0 && (
        <span className="flex items-center gap-1">
          {warnings.map((w) => (
            <span
              key={w}
              className="text-xs px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700"
              title={w}
            >
              {w}
            </span>
          ))}
        </span>
      )}

      {/* Spacer */}
      <span className="ml-auto" />

      {/* Task count */}
      <span className="text-xs text-gray-500">
        {taskCount} {taskCount === 1 ? "task" : "tasks"}
      </span>

      {/* Action buttons */}
      <LaunchControl projectId={project.id} onError={onError} />

      <button
        onClick={onNewTask}
        className="rounded px-2 py-0.5 text-xs font-medium text-indigo-700 bg-indigo-100 hover:bg-indigo-200 transition-colors"
      >
        + Task
      </button>

      <button
        onClick={onSync}
        disabled={syncing}
        className="rounded px-2 py-0.5 text-xs font-medium text-gray-700 bg-gray-100 hover:bg-gray-300 disabled:opacity-50 transition-colors"
      >
        {syncing ? "..." : "Sync"}
      </button>
    </div>
  );
}
