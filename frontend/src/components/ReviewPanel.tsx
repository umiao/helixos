/**
 * ReviewPanel -- displays review progress, verdicts, consensus score,
 * and decision buttons when human_decision_needed.
 */

import { useState } from "react";
import type { Task } from "../types";
import { submitReviewDecision, ApiError } from "../api";

interface ReviewPanelProps {
  task: Task | null;
  onDecisionSubmitted: (taskId: string, decision: string) => void;
  onError: (message: string) => void;
}

export default function ReviewPanel({
  task,
  onDecisionSubmitted,
  onError,
}: ReviewPanelProps) {
  const [submitting, setSubmitting] = useState(false);

  if (!task) {
    return (
      <div className="flex flex-col h-full bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="px-3 py-2 border-b border-gray-200">
          <h3 className="text-xs font-bold uppercase tracking-wide text-gray-600">
            Review Panel
          </h3>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <p className="text-sm text-gray-400">
            Select a task in review to see details
          </p>
        </div>
      </div>
    );
  }

  const review = task.review;
  const hasReview = review !== null && review !== undefined;

  const handleDecision = async (decision: "approve" | "reject") => {
    setSubmitting(true);
    try {
      await submitReviewDecision(task.id, decision);
      onDecisionSubmitted(task.id, decision);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to submit decision";
      onError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  // Progress bar percentage
  const progressPct = hasReview && review.rounds_total > 0
    ? Math.round((review.rounds_completed / review.rounds_total) * 100)
    : 0;

  return (
    <div className="flex flex-col h-full bg-white rounded-lg border border-gray-200 overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-200">
        <h3 className="text-xs font-bold uppercase tracking-wide text-gray-600">
          Review Panel
        </h3>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* Task info */}
        <div>
          <p className="text-xs text-gray-500 font-mono">{task.local_task_id}</p>
          <p className="text-sm font-medium text-gray-900">{task.title}</p>
        </div>

        {hasReview ? (
          <>
            {/* Review progress */}
            <div>
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>Review progress</span>
                <span>
                  {review.rounds_completed} / {review.rounds_total}
                </span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className="bg-indigo-500 h-2 rounded-full transition-all"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            </div>

            {/* Consensus score */}
            {review.consensus_score !== null && (
              <div>
                <p className="text-xs text-gray-500 mb-1">Consensus score</p>
                <div className="flex items-center gap-2">
                  <div className="w-full bg-gray-200 rounded-full h-3">
                    <div
                      className={`h-3 rounded-full transition-all ${
                        review.consensus_score >= 0.8
                          ? "bg-green-500"
                          : review.consensus_score >= 0.5
                            ? "bg-yellow-500"
                            : "bg-red-500"
                      }`}
                      style={{
                        width: `${Math.round(review.consensus_score * 100)}%`,
                      }}
                    />
                  </div>
                  <span className="text-sm font-semibold text-gray-700 whitespace-nowrap">
                    {(review.consensus_score * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            )}

            {/* Decision points */}
            {review.decision_points.length > 0 && (
              <div>
                <p className="text-xs text-gray-500 mb-1">Decision points</p>
                <ul className="space-y-1">
                  {review.decision_points.map((point, i) => (
                    <li
                      key={i}
                      className="text-xs text-gray-700 bg-gray-50 rounded px-2 py-1"
                    >
                      {point}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Human choice indicator */}
            {review.human_choice && (
              <div className="text-xs">
                <span className="text-gray-500">Human decision: </span>
                <span
                  className={`font-semibold ${
                    review.human_choice === "approve"
                      ? "text-green-700"
                      : "text-red-700"
                  }`}
                >
                  {review.human_choice.toUpperCase()}
                </span>
              </div>
            )}

            {/* Decision buttons */}
            {review.human_decision_needed && !review.human_choice && (
              <div className="pt-2 border-t border-gray-200">
                <p className="text-xs text-orange-600 font-medium mb-2">
                  Human decision required
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleDecision("approve")}
                    disabled={submitting}
                    className="flex-1 rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {submitting ? "..." : "Approve"}
                  </button>
                  <button
                    onClick={() => handleDecision("reject")}
                    disabled={submitting}
                    className="flex-1 rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {submitting ? "..." : "Reject"}
                  </button>
                </div>
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-gray-400">No review data available</p>
        )}
      </div>
    </div>
  );
}
