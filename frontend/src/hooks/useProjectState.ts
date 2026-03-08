import { useCallback, useState } from "react";
import {
  fetchProjects,
  fetchTasks,
  syncAll,
  syncProject,
  ApiError,
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

  const handleSelectedProjectsChange = useCallback((ids: string[]) => {
    setSelectedProjects(ids);
    saveSelectedProjects(ids);
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
      setSelectedProjects((prev) => {
        const newIds = p
          .filter((proj) => !prev.includes(proj.id))
          .map((proj) => proj.id);
        const updated = [...prev, ...newIds];
        saveSelectedProjects(updated);
        return updated;
      });
      addToast("Project imported successfully", "success");
    } catch {
      // Data will be stale but not broken
    }
  }, [addToast, setTasks]);

  const initializeProjects = useCallback(
    (p: Project[]) => {
      setProjects(p);
      const saved = loadSelectedProjects();
      if (saved !== null) {
        const validIds = saved.filter((id) => p.some((proj) => proj.id === id));
        const newIds = p
          .filter((proj) => !saved.includes(proj.id))
          .map((proj) => proj.id);
        setSelectedProjects([...validIds, ...newIds]);
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
