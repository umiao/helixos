/**
 * ReviewSubmitModal -- edit + preview modal for submitting tasks for review.
 * Opens when the review gate blocks a BACKLOG -> QUEUED transition (428).
 * Allows editing title/description, then submits BACKLOG -> REVIEW.
 */

import { useCallback, useState } from "react";
import { updateTask, updateTaskStatus } from "../api";
import type { Task } from "../types";

interface ReviewSubmitModalProps {
  task: Task;
  onClose: () => void;
  onSubmitted: (taskId: string) => void;
  onError: (msg: string) => void;
}

export default function ReviewSubmitModal({
  task,
  onClose,
  onSubmitted,
  onError,
}: ReviewSubmitModalProps) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [submitting, setSubmitting] = useState(false);

  const titleChanged = title !== task.title;
  const descriptionChanged = description !== task.description;
  const hasEdits = titleChanged || descriptionChanged;

  const handleSubmit = useCallback(async () => {
    setSubmitting(true);
    try {
      // If title/description changed, PATCH the task first
      if (hasEdits) {
        const fields: { title?: string; description?: string } = {};
        if (titleChanged) fields.title = title;
        if (descriptionChanged) fields.description = description;
        await updateTask(task.id, fields);
      }
      // Transition to REVIEW
      await updateTaskStatus(task.id, "review");
      onSubmitted(task.id);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to submit for review";
      onError(msg);
    } finally {
      setSubmitting(false);
    }
  }, [task.id, title, description, titleChanged, descriptionChanged, hasEdits, onSubmitted, onError]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
          <h2 className="text-base font-semibold text-gray-900">
            Submit for Review
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            Review gate is enabled. Edit task details before submitting.
          </p>
        </div>

        {/* Body */}
        <div className="px-6 py-4 space-y-4">
          {/* Task ID badge */}
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono bg-gray-100 px-2 py-0.5 rounded text-gray-600">
              {task.local_task_id}
            </span>
            <span className="text-xs text-gray-400">
              {task.project_id}
            </span>
          </div>

          {/* Title */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Title
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent"
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Description
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={6}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent resize-y"
            />
          </div>

          {/* Preview section */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Preview (what reviewers will see)
            </label>
            <div className="rounded-md bg-gray-50 border border-gray-200 p-3 text-sm">
              <div className="font-semibold text-gray-800">{title || task.title}</div>
              <div className="mt-2 text-gray-600 whitespace-pre-wrap text-xs leading-relaxed max-h-32 overflow-y-auto">
                {description || task.description || "(no description)"}
              </div>
            </div>
          </div>

          {/* Edit indicator */}
          {hasEdits && (
            <p className="text-xs text-amber-600">
              Fields will be saved before submitting for review.
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-200 bg-gray-50 flex items-center justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !title.trim()}
            className="rounded-md px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? "Submitting..." : "Submit for Review"}
          </button>
        </div>
      </div>
    </div>
  );
}
