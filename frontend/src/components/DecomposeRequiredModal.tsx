/**
 * DecomposeRequiredModal -- shown when a user drags an undecomposed task to RUNNING.
 * Offers: "Go to Plan Review" (primary), "Cancel", "Execute Anyway" (danger).
 */

import { useCallback } from "react";

interface DecomposeRequiredModalProps {
  taskTitle: string;
  taskId: string;
  proposedTaskCount: number;
  /** Navigate to the plan review tab for this task. */
  onGoToPlanReview: () => void;
  /** Execute anyway, bypassing decomposition. */
  onExecuteAnyway: () => void;
  /** Cancel -- close modal, no action. */
  onCancel: () => void;
}

export default function DecomposeRequiredModal({
  taskTitle,
  taskId,
  proposedTaskCount,
  onGoToPlanReview,
  onExecuteAnyway,
  onCancel,
}: DecomposeRequiredModalProps) {
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        onCancel();
      }
    },
    [onCancel],
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onKeyDown={handleKeyDown}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 bg-blue-50">
          <h2 className="text-base font-semibold text-gray-900">
            Undecomposed Plan
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            This task has a plan with proposed sub-tasks that have not been
            decomposed yet.
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

          {/* Info box */}
          <div className="rounded-md bg-blue-50 border border-blue-200 px-3 py-2">
            <p className="text-xs text-blue-800">
              This task has{" "}
              <span className="font-semibold">{proposedTaskCount}</span>{" "}
              proposed sub-task{proposedTaskCount !== 1 ? "s" : ""} waiting for
              review. Review and confirm the plan to decompose them into
              actionable tasks before executing.
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-200 bg-gray-50 flex flex-col gap-3">
          <div className="flex items-center justify-end gap-2">
            <button
              onClick={onCancel}
              className="rounded-md px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={onGoToPlanReview}
              className="rounded-md px-4 py-2 text-sm font-medium text-white bg-green-600 hover:bg-green-700 transition-colors"
              autoFocus
            >
              Go to Plan Review
            </button>
          </div>
          {/* Deliberately de-emphasized danger link */}
          <div className="flex justify-end">
            <button
              onClick={onExecuteAnyway}
              className="text-xs text-red-500 hover:text-red-700 hover:underline transition-colors"
            >
              Execute Anyway
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
