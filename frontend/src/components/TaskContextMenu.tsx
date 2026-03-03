/**
 * TaskContextMenu -- right-click context menu for task cards.
 * Renders via portal at mouse position with status-aware actions.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Task, TaskStatus, KanbanColumn } from "../types";
import { STATUS_TO_COLUMN, KANBAN_COLUMNS, COLUMN_TO_STATUS } from "../types";
import { deleteTask, ApiError } from "../api";

interface TaskContextMenuProps {
  task: Task;
  position: { x: number; y: number };
  onClose: () => void;
  onMoveTask: (taskId: string, newStatus: TaskStatus, opts?: { reason?: string }) => void;
  onSelectTask?: (task: Task) => void;
  onTaskDeleted?: () => void;
  onError?: (msg: string) => void;
  /** Open the review submit modal for this task. */
  onSendToReview?: (task: Task) => void;
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
  onTaskDeleted,
  onError,
  onSendToReview,
}: TaskContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [dependents, setDependents] = useState<string[] | null>(null);

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
  }, [position, confirmingDelete]);

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

  const handleSendToReview = useCallback(() => {
    onSendToReview?.(task);
    onClose();
  }, [task, onSendToReview, onClose]);

  // Show "Send to Review" for BACKLOG and QUEUED tasks
  const canSendToReview =
    onSendToReview && (task.status === "backlog" || task.status === "queued");

  const handleDeleteClick = useCallback(() => {
    setConfirmingDelete(true);
  }, []);

  const handleDeleteConfirm = useCallback(
    async (force: boolean = false) => {
      setDeleting(true);
      try {
        await deleteTask(task.id, force);
        onTaskDeleted?.();
        onClose();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          const deps = (err as ApiError & { dependents?: string[] }).dependents;
          if (deps && deps.length > 0) {
            setDependents(deps);
            setDeleting(false);
            return;
          }
          onError?.(err.detail);
        } else if (err instanceof ApiError) {
          onError?.(err.detail);
        } else {
          onError?.("Failed to delete task");
        }
        setDeleting(false);
      }
    },
    [task.id, onTaskDeleted, onClose, onError],
  );

  const handleDeleteCancel = useCallback(() => {
    setConfirmingDelete(false);
    setDependents(null);
  }, []);

  // Build "Move to" options: all columns except current
  const moveTargets = KANBAN_COLUMNS.filter((col) => col !== currentColumn);

  // Cannot delete RUNNING tasks
  const canDelete = task.status !== "running";

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

      {!confirmingDelete ? (
        <>
          {/* View details */}
          {onSelectTask && (
            <button
              onClick={handleViewDetails}
              className="w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 transition-colors"
            >
              View details
            </button>
          )}

          {/* Send to Review */}
          {canSendToReview && (
            <>
              <div className="h-px bg-gray-100 my-0.5" />
              <button
                onClick={handleSendToReview}
                className="w-full text-left px-3 py-1.5 text-sm text-yellow-700 hover:bg-yellow-50 transition-colors"
              >
                Send to Review
              </button>
            </>
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

          {/* Delete action */}
          {canDelete && (
            <>
              <div className="h-px bg-gray-100 my-0.5" />
              <button
                onClick={handleDeleteClick}
                className="w-full text-left px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 transition-colors"
              >
                Delete
              </button>
            </>
          )}
        </>
      ) : (
        <div className="px-3 py-2">
          {dependents ? (
            <>
              <p className="text-xs text-red-600 mb-1">
                This task has dependents:
              </p>
              <ul className="text-xs text-gray-600 mb-2 list-disc pl-4">
                {dependents.map((dep) => (
                  <li key={dep} className="font-mono">{dep}</li>
                ))}
              </ul>
              <p className="text-xs text-gray-500 mb-2">
                Force delete anyway?
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => handleDeleteConfirm(true)}
                  disabled={deleting}
                  className="px-2 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
                >
                  {deleting ? "Deleting..." : "Force delete"}
                </button>
                <button
                  onClick={handleDeleteCancel}
                  className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200"
                >
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="text-xs text-gray-600 mb-2">
                Delete "{task.title}"?
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => handleDeleteConfirm(false)}
                  disabled={deleting}
                  className="px-2 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
                >
                  {deleting ? "Deleting..." : "Confirm"}
                </button>
                <button
                  onClick={handleDeleteCancel}
                  className="px-2 py-1 text-xs bg-gray-100 text-gray-600 rounded hover:bg-gray-200"
                >
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>,
    document.body,
  );
}
