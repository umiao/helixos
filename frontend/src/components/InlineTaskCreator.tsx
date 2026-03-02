/**
 * InlineTaskCreator -- shows an "Add task..." placeholder in the Backlog column.
 * Clicking expands into an inline title input. Press Enter to create, Escape to cancel.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, createTask } from "../api";

interface InlineTaskCreatorProps {
  projectId: string;
  onCreated: () => void;
  onError: (msg: string) => void;
}

export default function InlineTaskCreator({
  projectId,
  onCreated,
  onError,
}: InlineTaskCreatorProps) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
    }
  }, [editing]);

  const handleSubmit = useCallback(async () => {
    const trimmed = title.trim();
    if (!trimmed) {
      setEditing(false);
      setTitle("");
      return;
    }

    setLoading(true);
    try {
      const result = await createTask(projectId, { title: trimmed });
      if (result.success) {
        setTitle("");
        setEditing(false);
        onCreated();
      } else {
        onError(result.error || "Failed to create task");
      }
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to create task";
      onError(msg);
    } finally {
      setLoading(false);
    }
  }, [projectId, title, onCreated, onError]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !loading) {
        e.preventDefault();
        handleSubmit();
      } else if (e.key === "Escape") {
        setEditing(false);
        setTitle("");
      }
    },
    [handleSubmit, loading],
  );

  const handleBlur = useCallback(() => {
    // If there's text, submit it; otherwise just close
    if (title.trim() && !loading) {
      handleSubmit();
    } else if (!loading) {
      setEditing(false);
      setTitle("");
    }
  }, [title, loading, handleSubmit]);

  if (editing) {
    return (
      <div className="rounded-lg border border-dashed border-indigo-300 bg-indigo-50/50 p-2">
        <input
          ref={inputRef}
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleBlur}
          placeholder="Task title... (Enter to create, Esc to cancel)"
          disabled={loading}
          className="w-full rounded border border-gray-300 bg-white px-2 py-1 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
        />
        {loading && (
          <p className="text-xs text-indigo-500 mt-1">Creating...</p>
        )}
      </div>
    );
  }

  return (
    <button
      onClick={() => setEditing(true)}
      className="w-full rounded-lg border border-dashed border-gray-300 py-2 text-xs text-gray-400 hover:border-indigo-300 hover:text-indigo-500 hover:bg-indigo-50/30 transition-colors"
      title="Add a new task to backlog"
    >
      + Add task...
    </button>
  );
}
