import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchProjects,
  fetchTasks,
  syncAll,
  syncProject,
  ApiError,
  fetchSelectedProjects as fetchSelectedProjectsAPI,
  saveSelectedProjects as saveSelectedProjectsAPI,
} from "../api";
import type { Project, Task } from "../types";
import {
  loadSelectedProjects,
  saveSelectedProjects,
} from "../components/ProjectSelector";

export function useProjectState(
  addToast: (text: string, type: "success" | "error") => void,
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>,
) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjects, setSelectedProjects] = useState<string[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [syncingProjects, setSyncingProjects] = useState<Set<string>>(
    new Set(),
  );

  // Debounced save to API with localStorage fallback
  const saveTimeoutRef = useRef<number | null>(null);
  const pendingSaveRef = useRef<string[] | null>(null);

  const handleSelectedProjectsChange = useCallback((ids: string[]) => {
    setSelectedProjects(ids);
    pendingSaveRef.current = ids;

    // Cancel pending save
    if (saveTimeoutRef.current !== null) {
      clearTimeout(saveTimeoutRef.current);
    }

    // Debounce API save (1s)
    saveTimeoutRef.current = window.setTimeout(async () => {
      const idsToSave = pendingSaveRef.current;
      if (idsToSave === null) return;

      try {
        await saveSelectedProjectsAPI(idsToSave);
      } catch {
        // Fallback to localStorage on API failure
        saveSelectedProjects(idsToSave);
      }
      pendingSaveRef.current = null;
    }, 1000);
  }, []);

  // Flush pending saves on unmount or page close
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (pendingSaveRef.current !== null) {
        // Synchronous fallback to localStorage since we can't await
        saveSelectedProjects(pendingSaveRef.current);
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      if (saveTimeoutRef.current !== null) {
        clearTimeout(saveTimeoutRef.current);
      }
      // Flush on unmount
      if (pendingSaveRef.current !== null) {
        saveSelectedProjects(pendingSaveRef.current);
      }
    };
  }, []);

  const handleSyncAll = useCallback(async () => {
    setSyncing(true);
    try {
      const result = await syncAll();
      const totalAdded = result.results.reduce((s, r) => s + r.added, 0);
      const totalUpdated = result.results.reduce((s, r) => s + r.updated, 0);
      addToast(
        `Sync complete: ${totalAdded} added, ${totalUpdated} updated`,
        "success",
      );
      const updatedTasks = await fetchTasks();
      setTasks(updatedTasks);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Sync failed";
      addToast(msg, "error");
    } finally {
      setSyncing(false);
    }
  }, [addToast, setTasks]);

  const handleSyncProject = useCallback(
    async (projectId: string) => {
      setSyncingProjects((prev) => new Set(prev).add(projectId));
      try {
        const result = await syncProject(projectId);
        addToast(
          `[${projectId}] Sync: ${result.added} added, ${result.updated} updated`,
          "success",
        );
        const updatedTasks = await fetchTasks();
        setTasks(updatedTasks);
      } catch (err) {
        const msg =
          err instanceof ApiError ? err.detail : "Sync failed";
        addToast(msg, "error");
      } finally {
        setSyncingProjects((prev) => {
          const next = new Set(prev);
          next.delete(projectId);
          return next;
        });
      }
    },
    [addToast, setTasks],
  );

  const handleImported = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([fetchProjects(), fetchTasks()]);
      setProjects(p);
      setTasks(t);
      // Auto-select newly imported projects
      setSelectedProjects((prev) => {
        const newIds = p
          .filter((proj) => !prev.includes(proj.id))
          .map((proj) => proj.id);
        const updated = [...prev, ...newIds];
        // Save immediately (bypass debounce for import)
        saveSelectedProjectsAPI(updated).catch(() => {
          // Fallback to localStorage
          saveSelectedProjects(updated);
        });
        return updated;
      });
      addToast("Project imported successfully", "success");
    } catch {
      // Data will be stale but not broken
    }
  }, [addToast, setTasks]);

  const initializeProjects = useCallback(
    async (p: Project[]) => {
      setProjects(p);

      // Try API first, fallback to localStorage
      let saved: string[] | null = null;
      try {
        saved = await fetchSelectedProjectsAPI();
      } catch {
        // Fallback to localStorage
        saved = loadSelectedProjects();
      }

      if (saved !== null) {
        const validIds = saved.filter((id) => p.some((proj) => proj.id === id));
        // Don't auto-add new projects - preserve user's deselection
        setSelectedProjects(validIds);
      } else {
        const primaryIds = p.filter((proj) => proj.is_primary).map((proj) => proj.id);
        if (primaryIds.length > 0) {
          setSelectedProjects(primaryIds);
        } else if (p.length > 0) {
          setSelectedProjects([p[0].id]);
        } else {
          setSelectedProjects([]);
        }
      }
    },
    [],
  );

  return {
    projects,
    setProjects,
    selectedProjects,
    syncing,
    syncingProjects,
    handleSelectedProjectsChange,
    handleSyncAll,
    handleSyncProject,
    handleImported,
    initializeProjects,
  };
}
