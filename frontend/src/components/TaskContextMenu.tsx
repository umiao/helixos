/**
 * TaskContextMenu -- right-click context menu for task cards.
 * Renders via portal at mouse position with status-aware actions.
 */

import { useCallback, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { Task, TaskStatus, KanbanColumn } from "../types";
import { STATUS_TO_COLUMN, KANBAN_COLUMNS, COLUMN_TO_STATUS } from "../types";

interface TaskContextMenuProps {
  task: Task;
  position: { x: number; y: number };
  onClose: () => void;
  onMoveTask: (taskId: string, newStatus: TaskStatus) => void;
  onSelectTask?: (task: Task) => void;
}

const COLUMN_LABELS: Record<KanbanColumn, string> = {
  BACKLOG: "Backlog",
  REVIEW: "Review",
  QUEUED: "Queued",
  RUNNING: "Running",
  DONE: "Done",
};

export default function TaskContextMenu({
  task,
  position,
  onClose,
  onMoveTask,
  onSelectTask,
}: TaskContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  // Close on click outside or Escape
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    // Use capture to catch before other handlers
    document.addEventListener("mousedown", handleClick, true);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClick, true);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  // Position the menu within viewport bounds
  useEffect(() => {
    if (!menuRef.current) return;
    const rect = menuRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let x = position.x;
    let y = position.y;
    if (x + rect.width > vw - 4) x = vw - rect.width - 4;
    if (y + rect.height > vh - 4) y = vh - rect.height - 4;
    if (x < 4) x = 4;
    if (y < 4) y = 4;
    menuRef.current.style.left = `${x}px`;
    menuRef.current.style.top = `${y}px`;
  }, [position]);

  const currentColumn = STATUS_TO_COLUMN[task.status];

  const handleMove = useCallback(
    (column: KanbanColumn) => {
      onMoveTask(task.id, COLUMN_TO_STATUS[column]);
      onClose();
    },
    [task.id, onMoveTask, onClose],
  );

  const handleViewDetails = useCallback(() => {
    onSelectTask?.(task);
    onClose();
  }, [task, onSelectTask, onClose]);

  // Build "Move to" options: all columns except current
  const moveTargets = KANBAN_COLUMNS.filter((col) => col !== currentColumn);

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-[9999] min-w-[160px] rounded-lg border border-gray-200 bg-white shadow-lg py-1"
      style={{ left: position.x, top: position.y }}
    >
      {/* Task ID header */}
      <div className="px-3 py-1.5 text-xs font-mono text-gray-400 border-b border-gray-100">
        {task.local_task_id}
      </div>

      {/* View details */}
      {onSelectTask && (
        <button
          onClick={handleViewDetails}
          className="w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 transition-colors"
        >
          View details
        </button>
      )}

      {/* Separator */}
      <div className="h-px bg-gray-100 my-0.5" />

      {/* Move to options */}
      <div className="px-3 py-1 text-xs text-gray-400 uppercase tracking-wide">
        Move to
      </div>
      {moveTargets.map((col) => (
        <button
          key={col}
          onClick={() => handleMove(col)}
          className="w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 transition-colors"
        >
          {COLUMN_LABELS[col]}
        </button>
      ))}

      {/* Status-specific actions */}
      {(task.status === "failed" || task.status === "blocked") && (
        <>
          <div className="h-px bg-gray-100 my-0.5" />
          <button
            onClick={() => handleMove("QUEUED")}
            className="w-full text-left px-3 py-1.5 text-sm text-indigo-600 hover:bg-indigo-50 transition-colors"
          >
            Retry (queue)
          </button>
        </>
      )}
    </div>,
    document.body,
  );
}
