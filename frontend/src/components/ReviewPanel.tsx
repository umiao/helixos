/**
 * ReviewPanel -- conversation-style review history with persistent DB data,
 * review progress, consensus score, and human decision buttons.
 *
 * Renders based on task.review_status:
 *   idle    -- "No review requested"
 *   running -- spinner + "Review in progress..."
 *   done    -- existing review results / history
 *   failed  -- error message + "Retry Review" button
 */

import { useEffect, useState } from "react";
import type { Task, ReviewHistoryEntry } from "../types";
import {
  submitReviewDecision,
  fetchReviewHistory,
  retryReview,
  ApiError,
} from "../api";

interface ReviewPanelProps {
  task: Task | null;
  onDecisionSubmitted: (taskId: string, decision: string) => void;
  onError: (message: string) => void;
}

const POLL_INTERVAL_MS = 5000;

export default function ReviewPanel({
  task,
  onDecisionSubmitted,
  onError,
}: ReviewPanelProps) {
  const [submitting, setSubmitting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [reason, setReason] = useState("");
  const [historyEntries, setHistoryEntries] = useState<ReviewHistoryEntry[]>(
    [],
  );
  const [historyLoading, setHistoryLoading] = useState(false);

  // Fetch review history when task changes
  useEffect(() => {
    if (!task) {
      setHistoryEntries([]);
      setReason("");
      return;
    }

    let cancelled = false;
    const load = async () => {
      setHistoryLoading(true);
      try {
        const resp = await fetchReviewHistory(task.id, { limit: 100 });
        if (!cancelled) {
          setHistoryEntries(resp.entries);
        }
      } catch {
        // Non-critical
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    };
    load();

    return () => {
      cancelled = true;
    };
  }, [task?.id]);

  // Poll for updates when task is in a review state or review is running
  useEffect(() => {
    if (!task) return;
    const shouldPoll =
      ["review", "review_auto_approved", "review_needs_human"].includes(
        task.status,
      ) || task.review_status === "running";
    if (!shouldPoll) return;

    const interval = setInterval(async () => {
      try {
        const resp = await fetchReviewHistory(task.id, { limit: 100 });
        setHistoryEntries(resp.entries);
      } catch {
        // Ignore
      }
    }, POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [task?.id, task?.status, task?.review_status]);

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
  const reviewStatus = task.review_status ?? "idle";

  const handleDecision = async (decision: "approve" | "reject") => {
    setSubmitting(true);
    try {
      await submitReviewDecision(task.id, decision, reason);
      setReason("");
      onDecisionSubmitted(task.id, decision);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to submit decision";
      onError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetryReview = async () => {
    setRetrying(true);
    try {
      await retryReview(task.id);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to retry review";
      onError(msg);
    } finally {
      setRetrying(false);
    }
  };

  const progressPct =
    hasReview && review.rounds_total > 0
      ? Math.round((review.rounds_completed / review.rounds_total) * 100)
      : 0;

  const verdictBadge = (verdict: string) => {
    const v = verdict.toLowerCase();
    if (v === "approve" || v === "approved") {
      return (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-green-100 text-green-800">
          approve
        </span>
      );
    }
    if (v === "reject" || v === "rejected") {
      return (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-red-100 text-red-800">
          reject
        </span>
      );
    }
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-yellow-100 text-yellow-800">
        {verdict}
      </span>
    );
  };

  const formatTime = (ts: string) => {
    try {
      return new Date(ts).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    } catch {
      return ts;
    }
  };

  // Review status badge for the header
  const reviewStatusBadge = () => {
    switch (reviewStatus) {
      case "running":
        return (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-blue-100 text-blue-700 animate-pulse">
            reviewing
          </span>
        );
      case "done":
        return (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-green-100 text-green-700">
            complete
          </span>
        );
      case "failed":
        return (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-red-100 text-red-700">
            failed
          </span>
        );
      default:
        return null;
    }
  };

  return (
    <div className="flex flex-col h-full bg-white rounded-lg border border-gray-200 overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-gray-200 flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wide text-gray-600">
          Review Panel
        </h3>
        <div className="flex items-center gap-2">
          {reviewStatusBadge()}
          <span className="text-xs text-gray-500 font-mono">
            {task.local_task_id}
          </span>
          <span className="text-xs font-medium text-gray-700 truncate max-w-48">
            {task.title}
          </span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {/* review_status == idle: no review requested */}
        {reviewStatus === "idle" && !hasReview && historyEntries.length === 0 && (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-gray-400">
              No review requested
            </p>
          </div>
        )}

        {/* review_status == running: spinner */}
        {reviewStatus === "running" && (
          <div className="flex items-center gap-3 py-4 px-3 rounded-lg bg-blue-50 border border-blue-200">
            <div className="w-5 h-5 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
            <div>
              <p className="text-sm font-medium text-blue-700">
                Review in progress...
              </p>
              <p className="text-xs text-blue-500 mt-0.5">
                The review pipeline is analyzing this task
              </p>
            </div>
          </div>
        )}

        {/* review_status == failed: error + retry button */}
        {reviewStatus === "failed" && (
          <div className="py-4 px-3 rounded-lg bg-red-50 border border-red-200 space-y-2">
            <p className="text-sm font-medium text-red-700">
              Review pipeline failed
            </p>
            <p className="text-xs text-red-500">
              The review pipeline encountered an error. You can retry or move the task back.
            </p>
            <button
              onClick={handleRetryReview}
              disabled={retrying}
              className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {retrying ? "Retrying..." : "Retry Review"}
            </button>
          </div>
        )}

        {/* Progress bar (shown when review data exists) */}
        {hasReview && (
          <div className="pb-2 border-b border-gray-100">
            <div className="flex justify-between text-xs text-gray-500 mb-1">
              <span>Review progress</span>
              <span>
                {review.rounds_completed} / {review.rounds_total}
              </span>
            </div>
            <div className="w-full bg-gray-200 rounded-full h-1.5">
              <div
                className="bg-indigo-500 h-1.5 rounded-full transition-all"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>
        )}

        {/* Conversation-style review history */}
        {historyLoading && historyEntries.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-2">
            Loading review history...
          </p>
        ) : historyEntries.length > 0 ? (
          <div className="space-y-2">
            {historyEntries.map((entry) => (
              <div
                key={entry.id}
                className="rounded-lg border border-gray-200 bg-gray-50 p-2.5"
              >
                {/* Reviewer header */}
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] font-semibold uppercase text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">
                      {entry.reviewer_focus}
                    </span>
                    <span className="text-[10px] text-gray-400">
                      {entry.reviewer_model}
                    </span>
                    <span className="text-[10px] text-gray-400">
                      Round {entry.round_number}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {verdictBadge(entry.verdict)}
                    <span className="text-[10px] text-gray-400">
                      {formatTime(entry.timestamp)}
                    </span>
                  </div>
                </div>

                {/* Summary */}
                <p className="text-xs text-gray-700 leading-relaxed">
                  {entry.summary}
                </p>

                {/* Suggestions */}
                {entry.suggestions.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5">
                    {entry.suggestions.map((s, i) => (
                      <li
                        key={i}
                        className="text-xs text-gray-600 pl-3 relative before:content-['-'] before:absolute before:left-0.5 before:text-gray-400"
                      >
                        {s}
                      </li>
                    ))}
                  </ul>
                )}

                {/* Consensus score (shown when present, typically on final round) */}
                {entry.consensus_score !== null && (
                  <div className="mt-1.5 pt-1.5 border-t border-gray-200 flex items-center gap-2">
                    <span className="text-[10px] text-gray-500">
                      Consensus:
                    </span>
                    <div className="flex-1 bg-gray-200 rounded-full h-1.5 max-w-24">
                      <div
                        className={`h-1.5 rounded-full ${
                          entry.consensus_score >= 0.8
                            ? "bg-green-500"
                            : entry.consensus_score >= 0.5
                              ? "bg-yellow-500"
                              : "bg-red-500"
                        }`}
                        style={{
                          width: `${Math.round(entry.consensus_score * 100)}%`,
                        }}
                      />
                    </div>
                    <span className="text-[10px] font-semibold text-gray-600">
                      {(entry.consensus_score * 100).toFixed(0)}%
                    </span>
                  </div>
                )}

                {/* Human decision inline */}
                {entry.human_decision && (
                  <div className="mt-1.5 pt-1.5 border-t border-gray-200">
                    <span className="text-[10px] text-gray-500">
                      Human decision:{" "}
                    </span>
                    <span
                      className={`text-[10px] font-semibold ${
                        entry.human_decision === "approve"
                          ? "text-green-700"
                          : "text-red-700"
                      }`}
                    >
                      {entry.human_decision.toUpperCase()}
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : hasReview ? (
          <p className="text-xs text-gray-400 text-center py-2">
            No review rounds recorded yet
          </p>
        ) : reviewStatus !== "idle" ? null : null}

        {/* Decision area: reason + buttons */}
        {hasReview && review.human_decision_needed && !review.human_choice && (
          <div className="pt-2 border-t border-gray-200 space-y-2">
            <p className="text-xs text-orange-600 font-medium">
              Human decision required
            </p>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Reason for your decision (optional)"
              rows={2}
              disabled={submitting}
              className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs text-gray-700 bg-white placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-indigo-400 focus:border-indigo-400 disabled:opacity-50 resize-none"
            />
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

        {/* Human choice already made */}
        {hasReview && review.human_choice && (
          <div className="text-xs pt-2 border-t border-gray-200">
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
      </div>
    </div>
  );
}
