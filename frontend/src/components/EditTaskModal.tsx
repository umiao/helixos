/**
 * EditTaskModal -- inline edit modal for task title and description.
 * Opens from the right-click context menu "Edit" action.
 * Saves via PATCH /api/tasks/{id}.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { updateTask } from "../api";
import type { Task } from "../types";

interface EditTaskModalProps {
  task: Task;
  onClose: () => void;
  onSaved: (updated: Task) => void;
  onError: (msg: string) => void;
}

export default function EditTaskModal({
  task,
  onClose,
  onSaved,
  onError,
}: EditTaskModalProps) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [saving, setSaving] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);

  // Auto-focus title input on open
  useEffect(() => {
    titleRef.current?.focus();
    titleRef.current?.select();
  }, []);

  // Close on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const titleChanged = title !== task.title;
  const descriptionChanged = description !== task.description;
  const hasEdits = titleChanged || descriptionChanged;
  const canSave = hasEdits && title.trim().length > 0;

  const handleSave = useCallback(async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      const fields: { title?: string; description?: string } = {};
      if (titleChanged) fields.title = title;
      if (descriptionChanged) fields.description = description;
      const updated = await updateTask(task.id, fields);
      onSaved(updated);
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to update task";
      onError(msg);
    } finally {
      setSaving(false);
    }
  }, [task.id, title, description, titleChanged, descriptionChanged, canSave, onSaved, onError]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
          <h2 className="text-base font-semibold text-gray-900">Edit Task</h2>
          <div className="flex items-center gap-2 mt-1">
            <span className="text-xs font-mono bg-gray-100 px-2 py-0.5 rounded text-gray-600">
              {task.local_task_id}
            </span>
            <span className="text-xs text-gray-400">{task.project_id}</span>
          </div>
        </div>

        {/* Body */}
        <div className="px-6 py-4 space-y-4">
          {/* Title */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              Title
            </label>
            <input
              ref={titleRef}
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
              rows={8}
              placeholder="Task description or implementation plan..."
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent resize-y"
            />
          </div>
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
            onClick={handleSave}
            disabled={saving || !canSave}
            className="rounded-md px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
