/**
 * TaskCard -- displays a single task inside a Kanban column.
 * Shows: project ID, task ID, title, status badge, dependency indicator.
 * Running cards show elapsed time via a client-side timer.
 * Supports drag via @dnd-kit/core useDraggable.
 */

import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Task, TaskStatus, PlanStatus } from "../types";
import { generatePlan } from "../api";
import TaskCardPopover from "./TaskCardPopover";

interface TaskCardProps {
  task: Task;
  onClick?: () => void;
  onContextMenu?: (task: Task, position: { x: number; y: number }) => void;
  /** Bubbled from popover after plan generation succeeds. */
  onTaskUpdated?: (task: Task) => void;
}

const STATUS_COLORS: Record<TaskStatus, string> = {
  backlog: "bg-gray-200 text-gray-700",
  review: "bg-yellow-100 text-yellow-800",
  review_auto_approved: "bg-green-100 text-green-800",
  review_needs_human: "bg-orange-100 text-orange-800",
  queued: "bg-blue-100 text-blue-800",
  running: "bg-indigo-100 text-indigo-800",
  done: "bg-green-200 text-green-900",
  failed: "bg-red-200 text-red-900",
  blocked: "bg-red-100 text-red-800",
};

const STATUS_LABELS: Record<TaskStatus, string> = {
  backlog: "BACKLOG",
  review: "REVIEW",
  review_auto_approved: "AUTO-APPROVED",
  review_needs_human: "NEEDS HUMAN",
  queued: "QUEUED",
  running: "RUNNING",
  done: "DONE",
  failed: "FAILED",
  blocked: "BLOCKED",
};

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function ElapsedTimer({ startedAt }: { startedAt: string }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = new Date(startedAt).getTime();
    const update = () => {
      setElapsed(Math.floor((Date.now() - start) / 1000));
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [startedAt]);

  return (
    <span className="text-xs text-indigo-500 font-mono tabular-nums">
      {formatElapsed(elapsed)}
    </span>
  );
}

export default function TaskCard({ task, onClick, onContextMenu, onTaskUpdated }: TaskCardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({
      id: task.id,
      data: { task },
    });

  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.5 : 1,
  };

  const badgeClass = STATUS_COLORS[task.status];
  const label = STATUS_LABELS[task.status];
  const hasDeps = task.depends_on.length > 0;
  const isRunning = task.status === "running";
  // Centralized active-state check: pulse for running tasks OR active review
  const isActive =
    task.status === "running" || task.review_status === "running";
  const isGeneratingPlan = task.plan_status === "generating";
  const startedAt = task.execution?.started_at;
  const hasNoPlan = task.plan_status === "none";
  const isDoneOrTerminal = task.status === "done" || task.status === "failed" || task.status === "blocked";
  const showPlanButton = (hasNoPlan || task.plan_status === "failed") && !isDoneOrTerminal && !isGeneratingPlan;

  // Plan generation from card face
  const [generatingLocal, setGeneratingLocal] = useState(false);

  const handleGeneratePlan = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (generatingLocal || isGeneratingPlan) return;
    setGeneratingLocal(true);
    try {
      await generatePlan(task.id);
      onTaskUpdated?.({
        ...task,
        plan_status: "generating" as PlanStatus,
      });
    } catch {
      // Error handling deferred to popover; card button is a quick-action shortcut
    } finally {
      setGeneratingLocal(false);
    }
  }, [task, generatingLocal, isGeneratingPlan, onTaskUpdated]);

  // Hover popover state: 300ms delay before showing
  const [showPopover, setShowPopover] = useState(false);
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);

  const handleMouseEnter = useCallback(() => {
    hoverTimer.current = setTimeout(() => {
      if (cardRef.current) {
        setAnchorRect(cardRef.current.getBoundingClientRect());
        setShowPopover(true);
      }
    }, 300);
  }, []);

  const handleMouseLeave = useCallback(() => {
    if (hoverTimer.current) {
      clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
    setShowPopover(false);
  }, []);

  // Hide popover when dragging starts
  useEffect(() => {
    if (isDragging) {
      setShowPopover(false);
      if (hoverTimer.current) {
        clearTimeout(hoverTimer.current);
        hoverTimer.current = null;
      }
    }
  }, [isDragging]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (hoverTimer.current) clearTimeout(hoverTimer.current);
    };
  }, []);

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      if (onContextMenu) {
        e.preventDefault();
        e.stopPropagation();
        // Hide popover when opening context menu
        setShowPopover(false);
        if (hoverTimer.current) {
          clearTimeout(hoverTimer.current);
          hoverTimer.current = null;
        }
        onContextMenu(task, { x: e.clientX, y: e.clientY });
      }
    },
    [task, onContextMenu],
  );

  // Combine refs: dnd-kit setNodeRef + our cardRef
  const combinedRef = useCallback(
    (node: HTMLDivElement | null) => {
      setNodeRef(node);
      cardRef.current = node;
    },
    [setNodeRef],
  );

  return (
    <div
      ref={combinedRef}
      style={style}
      {...listeners}
      {...attributes}
      onClick={onClick}
      onContextMenu={handleContextMenu}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      className={`rounded-lg border bg-white p-3 shadow-sm hover:shadow-md transition-shadow cursor-grab active:cursor-grabbing ${isGeneratingPlan ? "border-blue-400 animate-pulse shadow-blue-100" : "border-gray-200"}`}
    >
      {/* Header: project + task ID */}
      <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
        <span className="font-mono">{task.project_id}</span>
        <span className="font-mono font-semibold text-gray-700">
          {task.local_task_id}
        </span>
      </div>

      {/* Title */}
      <p className="text-sm font-medium text-gray-900 leading-snug mb-2">
        {task.title}
      </p>

      {/* Footer: status badge + elapsed/dependency */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${badgeClass}${isActive ? " animate-pulse" : ""}`}
          >
            {label}
          </span>
          {hasNoPlan && !isGeneratingPlan && (
            <span
              className="inline-block rounded-full px-2 py-0.5 text-xs font-semibold bg-amber-100 text-amber-700"
              title="This task has no plan"
            >
              No Plan
            </span>
          )}
          {isGeneratingPlan && (
            <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold bg-blue-100 text-blue-700">
              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Planning
            </span>
          )}
          {isRunning && startedAt && <ElapsedTimer startedAt={startedAt} />}
        </div>

        <div className="flex items-center gap-1.5">
          {showPlanButton && (
            <button
              onClick={handleGeneratePlan}
              disabled={generatingLocal}
              className="rounded-full px-2 py-0.5 text-xs font-semibold bg-blue-50 text-blue-600 hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              title="Generate plan for this task"
            >
              Plan
            </button>
          )}
          {hasDeps && (
            <span
              className="text-xs text-gray-400 flex items-center gap-0.5"
              title={`Depends on: ${task.depends_on.join(", ")}`}
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
                  d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101"
                />
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10.172 13.828a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.102 1.101"
                />
              </svg>
              {task.depends_on.length}
            </span>
          )}
        </div>
      </div>

      {/* Hover popover via portal */}
      {showPopover && anchorRect && !isDragging && (
        <TaskCardPopover task={task} anchorRect={anchorRect} onTaskUpdated={onTaskUpdated} />
      )}
    </div>
  );
}
