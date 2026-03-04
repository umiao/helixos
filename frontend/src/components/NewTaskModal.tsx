/**
 * NewTaskModal -- form to create a new task in a project's TASKS.md.
 * Fields: title (required), description, priority (P0/P1/P2).
 * Shows loading state during creation and success/error feedback.
 * "Enrich with AI" button calls POST /api/tasks/enrich to pre-fill
 * description and priority (editable before submit).
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, createTask, enrichTask } from "../api";

interface NewTaskModalProps {
  projectId: string;
  projectName: string;
  onClose: () => void;
  onCreated: (synced: boolean) => void;
  onError: (msg: string) => void;
  /** Optional initial title (e.g. from InlineTaskCreator Tab key). */
  initialTitle?: string;
  /** If true, auto-trigger enrichment on mount (when opened from inline creator). */
  autoEnrich?: boolean;
}

export default function NewTaskModal({
  projectId,
  projectName,
  onClose,
  onCreated,
  onError,
  initialTitle = "",
  autoEnrich = false,
}: NewTaskModalProps) {
  const [title, setTitle] = useState(initialTitle);
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState("P0");
  const [loading, setLoading] = useState(false);
  const [enriching, setEnriching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const doEnrich = useCallback(async (titleToEnrich: string) => {
    if (!titleToEnrich.trim()) {
      setError("Enter a title before enriching");
      return;
    }
    setEnriching(true);
    setError(null);
    try {
      const result = await enrichTask(titleToEnrich.trim());
      setDescription(result.description);
      setPriority(result.priority);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.status === 503
            ? "AI enrichment unavailable (Claude CLI not found)"
            : err.detail
          : "Enrichment failed";
      setError(msg);
    } finally {
      setEnriching(false);
    }
  }, []);

  // Auto-enrich on mount if requested (from InlineTaskCreator Tab key)
  useEffect(() => {
    if (autoEnrich && initialTitle.trim()) {
      doEnrich(initialTitle);
    }
    // Only run once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!title.trim()) {
        setError("Title is required");
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const result = await createTask(projectId, {
          title: title.trim(),
          description: description.trim(),
          priority,
        });
        if (result.success) {
          onCreated(result.synced);
          onClose();
        } else {
          setError(result.error || "Failed to create task");
        }
      } catch (err) {
        const msg =
          err instanceof ApiError ? err.detail : "Failed to create task";
        setError(msg);
        onError(msg);
      } finally {
        setLoading(false);
      }
    },
    [projectId, title, description, priority, onClose, onCreated, onError],
  );

  const busy = loading || enriching;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-bold text-gray-900">
            New Task -- {projectName}
          </h3>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-lg leading-none"
          >
            x
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {/* Title */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Title *
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Task title..."
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
              autoFocus
              disabled={busy}
            />
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Description
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description..."
              rows={3}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 resize-none"
              disabled={busy}
            />
          </div>

          {/* Priority */}
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Priority
            </label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 bg-white"
              disabled={busy}
            >
              <option value="P0">P0 -- Must Have</option>
              <option value="P1">P1 -- Should Have</option>
              <option value="P2">P2 -- Nice to Have</option>
            </select>
          </div>

          {/* Error */}
          {error && (
            <p className="text-xs text-red-600 bg-red-50 px-3 py-2 rounded">
              {error}
            </p>
          )}

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => doEnrich(title)}
              disabled={busy || !title.trim()}
              className="rounded-md border border-purple-300 bg-purple-50 px-3 py-1.5 text-sm font-medium text-purple-700 hover:bg-purple-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              title="Auto-generate description and priority with AI"
            >
              {enriching ? "Enriching..." : "Enrich with AI"}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 transition-colors"
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy || !title.trim()}
              className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Creating..." : "Create Task"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
