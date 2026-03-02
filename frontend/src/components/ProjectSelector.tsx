/**
 * ProjectSelector -- multi-select checkbox dropdown for filtering projects.
 * Persists selected project IDs to localStorage.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { Project } from "../types";

const STORAGE_KEY = "helixos-selected-projects";

interface ProjectSelectorProps {
  projects: Project[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
  onImportClick?: () => void;
}

/** Load selected project IDs from localStorage, or null if not set. */
export function loadSelectedProjects(): string[] | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed as string[];
  } catch {
    // Corrupt data -- ignore
  }
  return null;
}

/** Save selected project IDs to localStorage. */
export function saveSelectedProjects(ids: string[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  } catch {
    // Storage full or unavailable -- ignore
  }
}

export default function ProjectSelector({
  projects,
  selectedIds,
  onChange,
  onImportClick,
}: ProjectSelectorProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const allSelected = selectedIds.length === projects.length;
  const noneSelected = selectedIds.length === 0;

  const toggleProject = useCallback(
    (id: string) => {
      if (selectedIds.includes(id)) {
        onChange(selectedIds.filter((s) => s !== id));
      } else {
        onChange([...selectedIds, id]);
      }
    },
    [selectedIds, onChange],
  );

  const selectAll = useCallback(() => {
    onChange(projects.map((p) => p.id));
  }, [projects, onChange]);

  const selectNone = useCallback(() => {
    onChange([]);
  }, [onChange]);

  // Label for the button
  let buttonLabel: string;
  if (allSelected || noneSelected) {
    buttonLabel = "All projects";
  } else if (selectedIds.length === 1) {
    const proj = projects.find((p) => p.id === selectedIds[0]);
    buttonLabel = proj ? proj.name : "1 project";
  } else {
    buttonLabel = `${selectedIds.length} projects`;
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="rounded-md border border-gray-300 px-2 py-1 text-sm text-gray-700 bg-white hover:bg-gray-50 flex items-center gap-1"
      >
        <span>{buttonLabel}</span>
        <svg
          className={`w-3.5 h-3.5 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 top-full mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-50">
          {/* Select all / none */}
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-100">
            <button
              onClick={selectAll}
              className="text-xs text-indigo-600 hover:text-indigo-800 font-medium"
            >
              Select all
            </button>
            <button
              onClick={selectNone}
              className="text-xs text-gray-500 hover:text-gray-700 font-medium"
            >
              Clear
            </button>
          </div>

          {/* Project checkboxes */}
          <div className="max-h-60 overflow-y-auto py-1">
            {projects.map((project) => {
              const checked = selectedIds.includes(project.id);
              return (
                <label
                  key={project.id}
                  className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleProject(project.id)}
                    className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <span className="text-sm text-gray-700 truncate">
                    {project.name}
                  </span>
                  <span className="text-xs text-gray-400 font-mono ml-auto">
                    {project.id}
                  </span>
                </label>
              );
            })}
            {projects.length === 0 && (
              <p className="text-xs text-gray-400 text-center py-3">
                No projects
              </p>
            )}
          </div>

          {/* Import Project action */}
          {onImportClick && (
            <div className="border-t border-gray-100">
              <button
                onClick={() => {
                  setOpen(false);
                  onImportClick();
                }}
                className="flex items-center gap-2 w-full px-3 py-2 text-sm text-indigo-600 hover:bg-indigo-50 font-medium transition-colors"
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
                    d="M12 4v16m8-8H4"
                  />
                </svg>
                Import Project
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
