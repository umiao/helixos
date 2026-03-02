/**
 * DirectoryPicker -- browsable directory tree for selecting a project folder.
 * Sandboxed to $HOME via the backend /api/filesystem/browse endpoint.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, browseDirectory } from "../api";
import type { BrowseEntry, BrowseResult } from "../types";

interface DirectoryPickerProps {
  /** Called when user selects a directory. */
  onSelect: (path: string) => void;
  /** Called when user cancels browsing. */
  onCancel: () => void;
}

export default function DirectoryPicker({
  onSelect,
  onCancel,
}: DirectoryPickerProps) {
  const [browseResult, setBrowseResult] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDirectory = useCallback(async (path?: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await browseDirectory(path);
      setBrowseResult(result);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to browse directory";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  // Load home directory on mount
  useEffect(() => {
    loadDirectory();
  }, [loadDirectory]);

  const handleNavigate = useCallback(
    (entry: BrowseEntry) => {
      loadDirectory(entry.path);
    },
    [loadDirectory],
  );

  const handleGoUp = useCallback(() => {
    if (browseResult?.parent) {
      loadDirectory(browseResult.parent);
    }
  }, [browseResult, loadDirectory]);

  // Extract display path (last 2-3 segments for breadcrumb)
  const displayPath = browseResult?.path ?? "...";

  return (
    <div className="space-y-3">
      {/* Current path breadcrumb */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleGoUp}
          disabled={!browseResult?.parent || loading}
          className="rounded px-2 py-1 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
          title="Go to parent directory"
        >
          ..
        </button>
        <span
          className="text-xs text-gray-600 font-mono truncate"
          title={displayPath}
        >
          {displayPath}
        </span>
      </div>

      {/* Error */}
      {error && (
        <p className="text-xs text-red-600 bg-red-50 px-3 py-2 rounded">
          {error}
        </p>
      )}

      {/* Directory listing */}
      <div className="border border-gray-200 rounded-md max-h-64 overflow-y-auto bg-white">
        {loading && (
          <div className="px-3 py-6 text-center text-xs text-gray-400">
            Loading...
          </div>
        )}

        {!loading && browseResult && browseResult.entries.length === 0 && (
          <div className="px-3 py-6 text-center text-xs text-gray-400">
            No subdirectories found
          </div>
        )}

        {!loading &&
          browseResult?.entries.map((entry) => (
            <div
              key={entry.path}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 border-b border-gray-100 last:border-b-0 cursor-pointer group"
              onClick={() => handleNavigate(entry)}
            >
              {/* Folder icon (text-based) */}
              <span className="text-xs text-gray-400 shrink-0">[dir]</span>

              {/* Name */}
              <span className="text-sm text-gray-700 truncate flex-1 group-hover:text-indigo-600">
                {entry.name}
              </span>

              {/* Indicator badges */}
              <div className="flex items-center gap-1 shrink-0">
                {entry.has_git && (
                  <span className="px-1 py-0.5 text-[10px] rounded bg-green-100 text-green-700">
                    git
                  </span>
                )}
                {entry.has_tasks_md && (
                  <span className="px-1 py-0.5 text-[10px] rounded bg-blue-100 text-blue-700">
                    TASKS
                  </span>
                )}
                {entry.has_claude_md && (
                  <span className="px-1 py-0.5 text-[10px] rounded bg-purple-100 text-purple-700">
                    CLAUDE
                  </span>
                )}
              </div>

              {/* Select button (visible on hover) */}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(entry.path);
                }}
                className="rounded px-2 py-0.5 text-[10px] font-medium bg-indigo-100 text-indigo-700 hover:bg-indigo-200 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
              >
                Select
              </button>
            </div>
          ))}
      </div>

      {/* Select current directory + Cancel */}
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => {
            if (browseResult?.path) {
              onSelect(browseResult.path);
            }
          }}
          disabled={!browseResult?.path || loading}
          className="rounded-md px-3 py-1.5 text-xs font-medium text-indigo-700 bg-indigo-50 hover:bg-indigo-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          Select current directory
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
