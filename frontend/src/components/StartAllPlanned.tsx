/**
 * StartAllPlanned -- button that batch-starts all BACKLOG tasks with plan_status=ready.
 * Shows "Start N Planned" with count from client-side task list.
 * Disabled when N=0, loading spinner during request.
 */

import { useCallback, useMemo, useState } from "react";
import { ApiError, startAllPlanned } from "../api";
import type { Task } from "../types";

interface StartAllPlannedProps {
  projectId: string;
  tasks: Task[];
  onError: (msg: string) => void;
  onStarted?: (count: number) => void;
}

export default function StartAllPlanned({
  projectId,
  tasks,
  onError,
  onStarted,
}: StartAllPlannedProps) {
  const [loading, setLoading] = useState(false);

  const plannedCount = useMemo(
    () =>
      tasks.filter(
        (t) => t.status === "backlog" && t.plan_status === "ready",
      ).length,
    [tasks],
  );

  const handleClick = useCallback(async () => {
    setLoading(true);
    try {
      const result = await startAllPlanned(projectId);
      if (result.started > 0) {
        onStarted?.(result.started);
      }
      if (result.skipped > 0) {
        onError(
          `Started ${result.started}, skipped ${result.skipped} task(s)`,
        );
      }
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to start planned tasks";
      onError(msg);
    } finally {
      setLoading(false);
    }
  }, [projectId, onError]);

  return (
    <button
      onClick={handleClick}
      disabled={loading || plannedCount === 0}
      className="rounded px-2 py-0.5 text-xs font-medium text-emerald-700 bg-emerald-100 hover:bg-emerald-200 disabled:opacity-50 transition-colors"
      title={
        plannedCount === 0
          ? "No planned tasks to start"
          : `Start ${plannedCount} planned task(s)`
      }
    >
      {loading ? "..." : `Start ${plannedCount} Planned`}
    </button>
  );
}
