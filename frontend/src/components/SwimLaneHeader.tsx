/**
 * SwimLaneHeader -- per-project action bar with Review Gate, Pause/Resume, Start All Planned, New Task, Sync.
 * Also shows warning badges for limited-mode projects (missing TASKS.md or CLAUDE.md).
 */

import { useState } from "react";
import { pauseExecution, resumeExecution, setReviewGate } from "../api";
import type { Project, Task } from "../types";
import StartAllPlanned from "./StartAllPlanned";

interface SwimLaneHeaderProps {
  project: Project;
  tasks: Task[];
  taskCount: number;
  syncing: boolean;
  onSync: () => void;
  onNewTask: () => void;
  onError: (msg: string) => void;
  onPauseToggle?: (paused: boolean) => void;
  onReviewGateToggle?: (enabled: boolean) => void;
}

export default function SwimLaneHeader({
  project,
  tasks,
  taskCount,
  syncing,
  onSync,
  onNewTask,
  onError,
  onPauseToggle,
  onReviewGateToggle,
}: SwimLaneHeaderProps) {
  const [toggling, setToggling] = useState(false);
  const [togglingGate, setTogglingGate] = useState(false);

  // Detect limited-mode warnings (missing repo path or CLAUDE.md)
  const warnings: { label: string; tooltip: string }[] = [];
  if (!project.repo_path) {
    warnings.push({
      label: "No repo path",
      tooltip: "No repository path configured -- file operations unavailable",
    });
  }
  if (!project.claude_md_path) {
    warnings.push({
      label: "No CLAUDE.md",
      tooltip:
        "No CLAUDE.md found in project root -- Claude agent lacks project-specific context and conventions",
    });
  }

  const handlePauseToggle = async () => {
    setToggling(true);
    try {
      if (project.execution_paused) {
        await resumeExecution(project.id);
        onPauseToggle?.(false);
      } else {
        await pauseExecution(project.id);
        onPauseToggle?.(true);
      }
    } catch (err) {
      onError(
        `Failed to ${project.execution_paused ? "resume" : "pause"} execution: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setToggling(false);
    }
  };

  const handleGateToggle = async () => {
    setTogglingGate(true);
    try {
      const newEnabled = !project.review_gate_enabled;
      await setReviewGate(project.id, newEnabled);
      onReviewGateToggle?.(newEnabled);
    } catch (err) {
      onError(
        `Failed to toggle review gate: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setTogglingGate(false);
    }
  };

  const paused = project.execution_paused;
  const gateOn = project.review_gate_enabled;

  return (
    <div className="flex items-center gap-3 px-4 py-1.5 bg-gray-200 border-b border-gray-300 rounded-t-md flex-shrink-0">
      {/* Project name + ID */}
      <h2 className="text-sm font-bold text-gray-800 tracking-tight">
        {project.name}
      </h2>
      <span className="text-xs text-gray-500 font-mono">{project.id}</span>

      {/* Paused badge */}
      {paused && (
        <span
          className="text-xs px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium"
          title="Execution is paused -- new tasks will not be dispatched"
        >
          PAUSED
        </span>
      )}

      {/* Warning badges */}
      {warnings.length > 0 && (
        <span className="flex items-center gap-1">
          {warnings.map((w) => (
            <span
              key={w.label}
              className="text-xs px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700"
              title={w.tooltip}
            >
              {w.label}
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

      {/* Review gate toggle */}
      <button
        onClick={handleGateToggle}
        disabled={togglingGate}
        className={`rounded px-2 py-0.5 text-xs font-medium transition-colors disabled:opacity-50 ${
          gateOn
            ? "text-blue-800 bg-blue-200 hover:bg-blue-300"
            : "text-gray-700 bg-gray-100 hover:bg-gray-300"
        }`}
        title={
          gateOn
            ? "Review gate ON -- tasks must be reviewed before execution. Click to disable."
            : "Review gate OFF -- tasks can skip review. Click to enable."
        }
      >
        {togglingGate ? "..." : gateOn ? "Gate ON" : "Gate OFF"}
      </button>

      {/* Pause/Resume toggle */}
      <button
        onClick={handlePauseToggle}
        disabled={toggling}
        className={`rounded px-2 py-0.5 text-xs font-medium transition-colors disabled:opacity-50 ${
          paused
            ? "text-amber-800 bg-amber-200 hover:bg-amber-300"
            : "text-gray-700 bg-gray-100 hover:bg-gray-300"
        }`}
        title={
          paused
            ? "Resume execution -- new tasks will be dispatched"
            : "Pause execution -- in-flight tasks continue, new tasks held"
        }
      >
        {toggling ? "..." : paused ? "Resume" : "Pause"}
      </button>

      {/* Action buttons */}
      <StartAllPlanned projectId={project.id} tasks={tasks} onError={onError} />

      <button
        onClick={onNewTask}
        className="rounded px-2 py-0.5 text-xs font-medium text-indigo-700 bg-indigo-100 hover:bg-indigo-200 transition-colors"
        title="Create a new task (opens form)"
      >
        + Task
      </button>

      <button
        onClick={onSync}
        disabled={syncing}
        className="rounded px-2 py-0.5 text-xs font-medium text-gray-700 bg-gray-100 hover:bg-gray-300 disabled:opacity-50 transition-colors"
        title="Sync tasks from TASKS.md on disk"
      >
        {syncing ? "..." : "Sync"}
      </button>
    </div>
  );
}
