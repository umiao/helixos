/**
 * TaskCard -- displays a single task inside a Kanban column.
 * Shows: project ID, task ID, title, status badge, dependency indicator.
 * Supports drag via @dnd-kit/core useDraggable.
 */

import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import type { Task, TaskStatus } from "../types";

interface TaskCardProps {
  task: Task;
}

const STATUS_COLORS: Record<TaskStatus, string> = {
  backlog: "bg-gray-200 text-gray-700",
  review: "bg-yellow-100 text-yellow-800",
  review_auto_approved: "bg-green-100 text-green-800",
  review_needs_human: "bg-orange-100 text-orange-800",
  queued: "bg-blue-100 text-blue-800",
  running: "bg-indigo-100 text-indigo-800",
  done: "bg-green-200 text-green-900",
  failed: "bg-red-200 text-red-900",
  blocked: "bg-red-100 text-red-800",
};

const STATUS_LABELS: Record<TaskStatus, string> = {
  backlog: "BACKLOG",
  review: "REVIEW",
  review_auto_approved: "AUTO-APPROVED",
  review_needs_human: "NEEDS HUMAN",
  queued: "QUEUED",
  running: "RUNNING",
  done: "DONE",
  failed: "FAILED",
  blocked: "BLOCKED",
};

export default function TaskCard({ task }: TaskCardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({
      id: task.id,
      data: { task },
    });

  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.5 : 1,
  };

  const badgeClass = STATUS_COLORS[task.status];
  const label = STATUS_LABELS[task.status];
  const hasDeps = task.depends_on.length > 0;

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm hover:shadow-md transition-shadow cursor-grab active:cursor-grabbing"
    >
      {/* Header: project + task ID */}
      <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
        <span className="font-mono">{task.project_id}</span>
        <span className="font-mono font-semibold text-gray-700">
          {task.local_task_id}
        </span>
      </div>

      {/* Title */}
      <p className="text-sm font-medium text-gray-900 leading-snug mb-2">
        {task.title}
      </p>

      {/* Footer: status badge + dependency indicator */}
      <div className="flex items-center justify-between">
        <span
          className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${badgeClass}`}
        >
          {label}
        </span>

        {hasDeps && (
          <span
            className="text-xs text-gray-400 flex items-center gap-0.5"
            title={`Depends on: ${task.depends_on.join(", ")}`}
          >
            <svg
              className="w-3.5 h-3.5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101"
              />
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M10.172 13.828a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.102 1.101"
              />
            </svg>
            {task.depends_on.length}
          </span>
        )}
      </div>
    </div>
  );
}
