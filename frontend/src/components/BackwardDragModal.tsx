/**
 * BackwardDragModal -- styled confirmation dialog for backward drag operations.
 * Replaces browser confirm()/prompt() with a modal matching the app design language.
 * Shows task title, source/target columns, consequences, and optional reason input.
 */

import { useCallback, useState } from "react";
import type { KanbanColumn } from "../types";

/** Human-readable column labels. */
const COLUMN_LABELS: Record<KanbanColumn, string> = {
  BACKLOG: "Backlog",
  REVIEW: "Review",
  QUEUED: "Queued",
  RUNNING: "Running",
  DONE: "Done",
};

/** Consequence descriptions for moving backward to each target column. */
const COLUMN_CONSEQUENCES: Record<KanbanColumn, string> = {
  BACKLOG:
    "The task will return to the backlog. Any review progress or queue position will be reset.",
  REVIEW:
    "The task will be sent back for review. Execution results will be preserved.",
  QUEUED:
    "The task will be re-queued for execution.",
  RUNNING: "",
  DONE: "",
};

interface BackwardDragModalProps {
  taskTitle: string;
  taskId: string;
  sourceColumn: KanbanColumn;
  targetColumn: KanbanColumn;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}

export default function BackwardDragModal({
  taskTitle,
  taskId,
  sourceColumn,
  targetColumn,
  onConfirm,
  onCancel,
}: BackwardDragModalProps) {
  const [reason, setReason] = useState("");

  const handleConfirm = useCallback(() => {
    onConfirm(reason);
  }, [reason, onConfirm]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleConfirm();
      } else if (e.key === "Escape") {
        onCancel();
      }
    },
    [handleConfirm, onCancel],
  );

  const consequence =
    COLUMN_CONSEQUENCES[targetColumn] ||
    "The task will be moved to an earlier stage.";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onKeyDown={handleKeyDown}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 bg-amber-50">
          <h2 className="text-base font-semibold text-gray-900">
            Move Task Backward
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            You are moving a task to an earlier stage.
          </p>
        </div>

        {/* Body */}
        <div className="px-6 py-4 space-y-4">
          {/* Task info */}
          <div>
            <span className="text-xs font-mono bg-gray-100 px-2 py-0.5 rounded text-gray-600">
              {taskId}
            </span>
            <p className="mt-1.5 text-sm font-medium text-gray-900">
              {taskTitle}
            </p>
          </div>

          {/* Transition visualization */}
          <div className="flex items-center gap-3 py-2">
            <span className="rounded-md bg-gray-100 border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700">
              {COLUMN_LABELS[sourceColumn]}
            </span>
            <svg
              className="w-5 h-5 text-amber-500 shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M10 19l-7-7m0 0l7-7m-7 7h18"
              />
            </svg>
            <span className="rounded-md bg-amber-100 border border-amber-300 px-3 py-1.5 text-sm font-medium text-amber-800">
              {COLUMN_LABELS[targetColumn]}
            </span>
          </div>

          {/* Consequence description */}
          <div className="rounded-md bg-gray-50 border border-gray-200 px-3 py-2">
            <p className="text-xs text-gray-600">{consequence}</p>
          </div>

          {/* Optional reason */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Reason (optional)
            </label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why are you moving this task back?"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400 focus:border-transparent"
              autoFocus
            />
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-200 bg-gray-50 flex items-center justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-md px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            className="rounded-md px-4 py-2 text-sm font-medium text-white bg-amber-600 hover:bg-amber-700 transition-colors"
          >
            Confirm Move
          </button>
        </div>
      </div>
    </div>
  );
}
