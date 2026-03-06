/**
 * TaskCardPopover -- hover popover showing full task details.
 * Rendered via React portal to avoid clipping by overflow containers.
 * Positioned relative to the card's bounding rect.
 */

import { createPortal } from "react-dom";
import { useLayoutEffect, useRef, useState } from "react";
import type { Task, TaskStatus } from "../types";
import { generatePlan, ApiError } from "../api";
import type { PlanStatus } from "../types";

interface TaskCardPopoverProps {
  task: Task;
  anchorRect: DOMRect;
  /** Called after plan generation succeeds with the refreshed task. */
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

function formatTimestamp(ts: string | null): string {
  if (!ts) return "--";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-2 last:mb-0">
      <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-0.5">
        {label}
      </h4>
      <div className="text-sm text-gray-800">{children}</div>
    </div>
  );
}

export default function TaskCardPopover({ task, anchorRect, onTaskUpdated }: TaskCardPopoverProps) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  useLayoutEffect(() => {
    const el = popoverRef.current;
    if (!el) return;
    const popW = el.offsetWidth;
    const popH = el.offsetHeight;
    const margin = 8;

    // Try right of card first, then left, then below
    let left = anchorRect.right + margin;
    let top = anchorRect.top;

    if (left + popW > window.innerWidth) {
      left = anchorRect.left - popW - margin;
    }
    if (left < 0) {
      left = anchorRect.left;
      top = anchorRect.bottom + margin;
    }

    // Keep within vertical bounds
    if (top + popH > window.innerHeight) {
      top = Math.max(4, window.innerHeight - popH - 4);
    }
    if (top < 4) top = 4;

    setPos({ top, left });
  }, [anchorRect]);

  const hasNoPlan = task.plan_status === "none";
  const isDone = task.status === "done" || task.status === "failed" || task.status === "blocked";
  const planFailed = task.plan_status === "failed";
  const planGenerating = task.plan_status === "generating" || generating;
  const showGenerateButton = (hasNoPlan || planFailed) && !isDone;

  const handleGeneratePlan = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (generating) return;
    setGenerating(true);
    setGenError(null);
    try {
      await generatePlan(task.id);
      // 202 accepted -- SSE plan_status_change events drive UI updates.
      // Optimistically update local task to show generating state immediately.
      onTaskUpdated?.({
        ...task,
        plan_status: "generating" as PlanStatus,
      });
    } catch (err) {
      const msg = err instanceof ApiError ? err.detail : "Failed to generate plan";
      setGenError(msg);
    } finally {
      setGenerating(false);
    }
  };

  const badgeClass = STATUS_COLORS[task.status];
  const label = STATUS_LABELS[task.status];

  return createPortal(
    <div
      ref={popoverRef}
      style={{ top: pos.top, left: pos.left }}
      className="fixed z-[9999] w-80 max-h-[70vh] overflow-y-auto rounded-lg border border-gray-200 bg-white p-4 shadow-lg"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <span className="font-mono text-xs text-gray-500">{task.project_id}</span>
        <span className="font-mono text-xs font-semibold text-gray-700">
          {task.local_task_id}
        </span>
      </div>
      <p className="text-sm font-semibold text-gray-900 mb-2">{task.title}</p>

      {/* Status */}
      <div className="mb-3">
        <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${badgeClass}`}>
          {label}
        </span>
      </div>

      {/* Generate Plan button */}
      {showGenerateButton && (
        <div className="mb-3">
          {planFailed && !generating && (
            <p className="mb-1 text-xs text-red-600">
              {task.plan_error_message || "Plan generation failed"}
            </p>
          )}
          <button
            onClick={handleGeneratePlan}
            disabled={planGenerating}
            className="w-full rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-1.5"
          >
            {planGenerating && (
              <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {planGenerating ? "Generating plan..." : planFailed ? "Retry Plan" : "Generate Plan"}
          </button>
          {genError && (
            <p className="mt-1 text-xs text-red-600">{genError}</p>
          )}
        </div>
      )}

      {/* Description */}
      {task.description && (
        <Section label="Description">
          <p className="whitespace-pre-wrap text-gray-700 text-xs leading-relaxed">
            {task.description}
          </p>
        </Section>
      )}

      {/* Dependencies */}
      {task.depends_on.length > 0 && (
        <Section label="Dependencies">
          <div className="flex flex-wrap gap-1">
            {task.depends_on.map((dep) => (
              <span
                key={dep}
                className="inline-block rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-600"
              >
                {dep}
              </span>
            ))}
          </div>
        </Section>
      )}

      {/* Execution state */}
      {task.execution && (
        <Section label="Execution">
          <div className="space-y-0.5 text-xs">
            {task.execution.started_at && (
              <div>
                <span className="text-gray-500">Started: </span>
                {formatTimestamp(task.execution.started_at)}
              </div>
            )}
            {task.execution.finished_at && (
              <div>
                <span className="text-gray-500">Finished: </span>
                {formatTimestamp(task.execution.finished_at)}
              </div>
            )}
            {task.execution.exit_code !== null && (
              <div>
                <span className="text-gray-500">Exit code: </span>
                <span className={task.execution.exit_code === 0 ? "text-green-700" : "text-red-700"}>
                  {task.execution.exit_code}
                </span>
              </div>
            )}
            {task.execution.retry_count > 0 && (
              <div>
                <span className="text-gray-500">Retries: </span>
                {task.execution.retry_count}/{task.execution.max_retries}
              </div>
            )}
            {task.execution.error_summary && (
              <div className="mt-1 rounded bg-red-50 p-1.5 text-red-700 text-xs">
                {task.execution.error_summary}
              </div>
            )}
            {task.execution.log_tail.length > 0 && (
              <div className="mt-1">
                <div className="text-gray-500 mb-0.5">Log tail:</div>
                <pre className="rounded bg-gray-900 p-2 text-gray-200 text-xs overflow-x-auto max-h-32 leading-tight">
                  {task.execution.log_tail.join("\n")}
                </pre>
              </div>
            )}
          </div>
        </Section>
      )}

      {/* Review state */}
      {task.review && (
        <Section label="Review">
          <div className="space-y-0.5 text-xs">
            <div>
              <span className="text-gray-500">Progress: </span>
              {task.review.rounds_completed}/{task.review.rounds_total} rounds
            </div>
            {task.review.consensus_score !== null && (
              <div>
                <span className="text-gray-500">Consensus: </span>
                <span className={task.review.consensus_score >= 0.7 ? "text-green-700" : "text-orange-700"}>
                  {(task.review.consensus_score * 100).toFixed(0)}%
                </span>
              </div>
            )}
            {task.review.human_decision_needed && (
              <div className="text-orange-600 font-medium">Human decision needed</div>
            )}
            {task.review.human_choice && (
              <div>
                <span className="text-gray-500">Decision: </span>
                {task.review.human_choice}
              </div>
            )}
            {task.review.decision_points.length > 0 && (
              <div className="mt-1">
                <div className="text-gray-500 mb-0.5">Decision points:</div>
                <ul className="list-disc list-inside text-gray-700">
                  {task.review.decision_points.map((dp, i) => (
                    <li key={i}>{dp}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Section>
      )}

      {/* Timestamps */}
      <Section label="Timestamps">
        <div className="space-y-0.5 text-xs">
          <div>
            <span className="text-gray-500">Created: </span>
            {formatTimestamp(task.created_at)}
          </div>
          <div>
            <span className="text-gray-500">Updated: </span>
            {formatTimestamp(task.updated_at)}
          </div>
          {task.completed_at && (
            <div>
              <span className="text-gray-500">Completed: </span>
              {formatTimestamp(task.completed_at)}
            </div>
          )}
        </div>
      </Section>
    </div>,
    document.body
  );
}
