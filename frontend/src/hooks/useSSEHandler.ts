import { useCallback, useEffect, useRef } from "react";
import { normalizeStreamEvents } from "../components/ConversationView";
import { fetchTask, fetchTasks } from "../api";
import useSSE, { type SSEEvent } from "./useSSE";
import type { Task, TaskStatus, StreamDisplayItem, ProposedTask } from "../types";
import type { Project } from "../types";
import { planStatePatch } from "../utils/planState";

interface UseSSEHandlerDeps {
  addToast: (text: string, type: "success" | "error") => void;
  addLogEntry: (task_id: string, message: string, timestamp: string, source?: string) => void;
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>;
  setProjects: React.Dispatch<React.SetStateAction<Project[]>>;
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>;
  setBottomPanel: React.Dispatch<React.SetStateAction<"log" | "review" | "plan" | "running" | "costs">>;
  setReviewPhase: React.Dispatch<React.SetStateAction<string>>;
  setStreamEvents: React.Dispatch<React.SetStateAction<Record<string, StreamDisplayItem[]>>>;
  selectedTaskRef: React.MutableRefObject<Task | null>;
}

export function useSSEHandler(deps: UseSSEHandlerDeps) {
  const {
    addToast,
    addLogEntry,
    setTasks,
    setProjects,
    setSelectedTask,
    setBottomPanel,
    setReviewPhase,
    setStreamEvents,
    selectedTaskRef,
  } = deps;

  // Debounced board sync
  const boardSyncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debouncedBoardSync = useCallback(() => {
    if (boardSyncTimerRef.current !== null) {
      clearTimeout(boardSyncTimerRef.current);
    }
    boardSyncTimerRef.current = setTimeout(async () => {
      boardSyncTimerRef.current = null;
      try {
        const updated = await fetchTasks();
        setTasks(updated);
      } catch {
        // Silently ignore -- next sync event will retry
      }
    }, 500);
  }, [setTasks]);

  // Clean up timer on unmount
  useEffect(() => {
    return () => {
      if (boardSyncTimerRef.current !== null) {
        clearTimeout(boardSyncTimerRef.current);
      }
    };
  }, []);

  const handleSSEEvent = useCallback(
    (event: SSEEvent) => {
      switch (event.type) {
        case "status_change": {
          const newStatus = event.data.status as TaskStatus;
          setTasks((prev) =>
            prev.map((t) =>
              t.id === event.task_id ? { ...t, status: newStatus } : t,
            ),
          );
          addLogEntry(
            event.task_id,
            `Status changed to ${newStatus}`,
            event.timestamp,
          );
          if (newStatus === "review_needs_human") {
            addToast(
              `[${event.task_id}] Review needs human decision`,
              "error",
            );
            setBottomPanel("review");
            fetchTask(event.task_id)
              .then((updated) => {
                setTasks((prev) =>
                  prev.map((t) => (t.id === updated.id ? updated : t)),
                );
                setSelectedTask(updated);
              })
              .catch(() => {
                setTasks((prev) => {
                  const t = prev.find((x) => x.id === event.task_id);
                  if (t) setSelectedTask({ ...t, status: newStatus });
                  return prev;
                });
              });
            break;
          }
          fetchTask(event.task_id)
            .then((updated) => {
              setTasks((prev) =>
                prev.map((t) => (t.id === updated.id ? updated : t)),
              );
              setSelectedTask((sel) =>
                sel && sel.id === updated.id ? updated : sel,
              );
            })
            .catch(() => {
              // Ignore -- status already updated optimistically
            });
          break;
        }
        case "log": {
          const msg =
            typeof event.data.message === "string"
              ? event.data.message
              : JSON.stringify(event.data);
          const logSource = typeof event.data.source === "string"
            ? event.data.source
            : undefined;
          addLogEntry(event.task_id, msg, event.timestamp, logSource);
          break;
        }
        case "plan_status_change": {
          const newPlanStatus = event.data.plan_status as string;
          const eventGenId = (event.data.generation_id as string) || undefined;

          // Filter stale SSE events: if a completion event (ready/failed)
          // carries a generation_id that doesn't match the task's current
          // plan_generation_id, the event is from a superseded generation.
          if (
            (newPlanStatus === "ready" || newPlanStatus === "failed") &&
            eventGenId
          ) {
            let isStale = false;
            setTasks((prev) => {
              const target = prev.find((t) => t.id === event.task_id);
              if (
                target?.plan_generation_id &&
                target.plan_generation_id !== eventGenId
              ) {
                isStale = true;
              }
              return prev; // no mutation -- just reading
            });
            if (isStale) break;
          }

          // Build the optimistic patch using the shared utility
          const proposedTasks =
            newPlanStatus === "ready" && Array.isArray(event.data.proposed_tasks)
              ? (event.data.proposed_tasks as ProposedTask[])
              : undefined;
          const patch = planStatePatch(
            newPlanStatus as Task["plan_status"],
            {
              generationId: eventGenId,
              errorType: (event.data.error_type as string) || undefined,
              errorMessage: (event.data.error_message as string) || undefined,
              proposedTasks: proposedTasks,
            },
          );

          setTasks((prev) =>
            prev.map((t) =>
              t.id === event.task_id ? { ...t, ...patch } : t,
            ),
          );
          setSelectedTask((sel) =>
            sel && sel.id === event.task_id ? { ...sel, ...patch } : sel,
          );
          if (newPlanStatus === "ready" || newPlanStatus === "failed") {
            fetchTask(event.task_id)
              .then((updated) => {
                // Preserve proposed_tasks from SSE (not returned by API)
                const withProposed =
                  newPlanStatus === "ready" && proposedTasks
                    ? { ...updated, proposed_tasks: proposedTasks }
                    : updated;
                setTasks((prev) =>
                  prev.map((t) => (t.id === updated.id ? withProposed : t)),
                );
                setSelectedTask((sel) =>
                  sel && sel.id === updated.id ? withProposed : sel,
                );
              })
              .catch(() => { /* ignore */ });
          }
          // Auto-switch to Plan tab when plan becomes ready
          if (newPlanStatus === "ready") {
            if (selectedTaskRef.current?.id === event.task_id) {
              setBottomPanel("plan");
            }
          }
          break;
        }
        case "alert": {
          const alertMsg =
            typeof event.data.error === "string"
              ? event.data.error
              : JSON.stringify(event.data);
          addToast(`[${event.task_id}] ${alertMsg}`, "error");
          addLogEntry(event.task_id, `ALERT: ${alertMsg}`, event.timestamp);
          break;
        }
        case "review_progress": {
          const completed = event.data.completed as number;
          const total = event.data.total as number;
          const phase = (event.data.phase as string) ?? "";
          addLogEntry(
            event.task_id,
            `Review progress: ${completed}/${total} -- ${phase}`,
            event.timestamp,
            "review",
          );
          if (event.task_id === selectedTaskRef.current?.id) {
            setReviewPhase(phase);
          }
          break;
        }
        case "execution_paused": {
          const paused = event.data.paused as boolean;
          const projectId = event.task_id;
          setProjects((prev) =>
            prev.map((p) =>
              p.id === projectId ? { ...p, execution_paused: paused } : p,
            ),
          );
          addToast(
            `[${projectId}] Execution ${paused ? "paused" : "resumed"}`,
            paused ? "error" : "success",
          );
          break;
        }
        case "review_gate_changed": {
          const gateEnabled = event.data.review_gate_enabled as boolean;
          const gateProjectId = event.task_id;
          setProjects((prev) =>
            prev.map((p) =>
              p.id === gateProjectId
                ? { ...p, review_gate_enabled: gateEnabled }
                : p,
            ),
          );
          addToast(
            `[${gateProjectId}] Review gate ${gateEnabled ? "enabled" : "disabled"}`,
            gateEnabled ? "success" : "error",
          );
          break;
        }
        case "review_started": {
          setTasks((prev) =>
            prev.map((t) =>
              t.id === event.task_id
                ? { ...t, review_status: "running" as const }
                : t,
            ),
          );
          if (event.task_id === selectedTaskRef.current?.id) {
            setReviewPhase("");
          }
          addLogEntry(
            event.task_id,
            "Review pipeline started",
            event.timestamp,
            "review",
          );
          break;
        }
        case "review_failed": {
          setTasks((prev) =>
            prev.map((t) =>
              t.id === event.task_id
                ? { ...t, review_status: "failed" as const }
                : t,
            ),
          );
          fetchTask(event.task_id)
            .then((updated) => {
              setTasks((prev) =>
                prev.map((t) => (t.id === updated.id ? updated : t)),
              );
              setSelectedTask((sel) =>
                sel && sel.id === updated.id ? updated : sel,
              );
            })
            .catch(() => { /* ignore */ });
          break;
        }
        case "execution_stream": {
          const normalized = normalizeStreamEvents(
            [event.data as Record<string, unknown>],
            `sse-${Date.now()}`,
            event.timestamp,
          );
          if (normalized.length > 0) {
            setStreamEvents((prev) => {
              const existing = prev[event.task_id] ?? [];
              const updated = [...existing, ...normalized];
              return {
                ...prev,
                [event.task_id]: updated.length > 2000 ? updated.slice(-2000) : updated,
              };
            });
          }
          break;
        }
        case "board_sync": {
          debouncedBoardSync();
          break;
        }
        case "process_failed": {
          const failError =
            typeof event.data.error === "string"
              ? event.data.error
              : "Process crashed";
          const failType = event.data.subprocess_type as string;
          const failPid = event.data.pid as number;
          addToast(
            `[${event.task_id}] ${failError}`,
            "error",
          );
          addLogEntry(
            event.task_id,
            `PROCESS FAILED: ${failType} pid=${failPid} -- ${failError}`,
            event.timestamp,
          );
          break;
        }
      }
    },
    [addToast, addLogEntry, debouncedBoardSync, setTasks, setProjects, setSelectedTask, setBottomPanel, setReviewPhase, setStreamEvents, selectedTaskRef],
  );

  const { connected } = useSSE(handleSSEEvent);

  return { connected };
}
