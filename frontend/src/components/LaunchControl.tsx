/**
 * LaunchControl -- launch/stop toggle with port display and running indicator.
 * Shows a compact button that toggles between Launch and Stop states,
 * plus port number and uptime when running.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getProcessStatus, launchProject, stopProject } from "../api";
import type { ProcessStatus } from "../types";

interface LaunchControlProps {
  projectId: string;
  onError: (msg: string) => void;
  onStatusChange?: (running: boolean) => void;
}

export default function LaunchControl({
  projectId,
  onError,
  onStatusChange,
}: LaunchControlProps) {
  const [status, setStatus] = useState<ProcessStatus>({
    running: false,
    pid: null,
    port: null,
    uptime_seconds: null,
  });
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const s = await getProcessStatus(projectId);
      setStatus(s);
      onStatusChange?.(s.running);
    } catch {
      // Ignore poll errors silently
    }
  }, [projectId, onStatusChange]);

  // Poll process status every 5 seconds when running
  useEffect(() => {
    fetchStatus();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchStatus]);

  useEffect(() => {
    if (status.running) {
      pollRef.current = setInterval(fetchStatus, 5000);
    } else {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [status.running, fetchStatus]);

  const handleLaunch = useCallback(async () => {
    setLoading(true);
    try {
      const s = await launchProject(projectId);
      setStatus(s);
      onStatusChange?.(s.running);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to launch";
      onError(msg);
    } finally {
      setLoading(false);
    }
  }, [projectId, onError, onStatusChange]);

  const handleStop = useCallback(async () => {
    setLoading(true);
    try {
      await stopProject(projectId);
      setStatus({ running: false, pid: null, port: null, uptime_seconds: null });
      onStatusChange?.(false);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to stop";
      onError(msg);
    } finally {
      setLoading(false);
    }
  }, [projectId, onError, onStatusChange]);

  const formatUptime = (seconds: number | null): string => {
    if (seconds === null) return "";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${Math.round(seconds / 3600)}h`;
  };

  if (status.running) {
    return (
      <span className="flex items-center gap-1.5">
        <span className="inline-block w-2 h-2 rounded-full bg-green-500 animate-pulse" />
        {status.port && (
          <span className="text-xs text-green-700 font-mono">
            :{status.port}
          </span>
        )}
        {status.uptime_seconds !== null && (
          <span className="text-xs text-gray-500">
            {formatUptime(status.uptime_seconds)}
          </span>
        )}
        <button
          onClick={handleStop}
          disabled={loading}
          className="rounded px-2 py-0.5 text-xs font-medium text-red-700 bg-red-100 hover:bg-red-200 disabled:opacity-50 transition-colors"
          title="Stop the dev server"
        >
          {loading ? "..." : "Stop"}
        </button>
      </span>
    );
  }

  return (
    <button
      onClick={handleLaunch}
      disabled={loading}
      className="rounded px-2 py-0.5 text-xs font-medium text-emerald-700 bg-emerald-100 hover:bg-emerald-200 disabled:opacity-50 transition-colors"
      title="Launch the dev server"
    >
      {loading ? "..." : "Launch"}
    </button>
  );
}
