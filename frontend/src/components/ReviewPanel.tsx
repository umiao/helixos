/**
 * ReviewPanel -- conversation-style review history with persistent DB data,
 * review progress, consensus score, and human decision buttons.
 *
 * Features:
 *   - Inline plan editor (Edit Plan / Save / Cancel)
 *   - Review attempt grouping with plan snapshots
 *   - Plan diff between attempts (via PlanDiffView)
 *
 * Renders based on task.review_status:
 *   idle    -- "No review requested" or "Re-review" button after request_changes
 *   running -- spinner + "Review in progress..."
 *   done    -- existing review results / history
 *   failed  -- error message + "Retry Review" button
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Task, ReviewHistoryEntry } from "../types";
import {
  submitReviewDecision,
  fetchReviewHistory,
  fetchTask,
  retryReview,
  updateTask,
  generatePlan,
  ApiError,
} from "../api";
import PlanDiffView from "./PlanDiffView";

type DecisionType = "approve" | "reject" | "request_changes";

/** Group of review entries sharing the same review_attempt. */
interface AttemptGroup {
  attempt: number;
  entries: ReviewHistoryEntry[];
  /** The plan snapshot for this attempt (from the first entry with a non-null snapshot). */
  planSnapshot: string | null;
  /** Timestamp of the first entry in the group. */
  timestamp: string;
}

interface ReviewPanelProps {
  task: Task | null;
  /** Current review phase label from SSE (e.g. "Starting feasibility... review..."). */
  reviewPhase?: string;
  onDecisionSubmitted: (taskId: string, decision: string) => void;
  onError: (message: string) => void;
  /** Called after an inline plan edit saves successfully. */
  onTaskUpdated?: (task: Task) => void;
}

const POLL_INTERVAL_MS = 5000;

export default function ReviewPanel({
  task,
  reviewPhase,
  onDecisionSubmitted,
  onError,
  onTaskUpdated,
}: ReviewPanelProps) {
  const [submitting, setSubmitting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [reason, setReason] = useState("");
  const [selectedDecision, setSelectedDecision] = useState<DecisionType | null>(null);
  const [historyEntries, setHistoryEntries] = useState<ReviewHistoryEntry[]>(
    [],
  );
  const [historyLoading, setHistoryLoading] = useState(false);
  const [expandedRaw, setExpandedRaw] = useState<Record<number, boolean>>({});
  const [planExpanded, setPlanExpanded] = useState(true);

  // Inline plan editor state
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);

  const toggleRawResponse = useCallback((entryId: number) => {
    setExpandedRaw((prev) => ({ ...prev, [entryId]: !prev[entryId] }));
  }, []);

  // Fetch review history when task changes
  useEffect(() => {
    if (!task) {
      setHistoryEntries([]);
      setReason("");
      setSelectedDecision(null);
      setExpandedRaw({});
      setEditing(false);
      setGenerating(false);
      return;
    }
    setExpandedRaw({});
    setSelectedDecision(null);
    setReason("");
    setEditing(false);

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

  // Group history entries by review_attempt
  const attemptGroups: AttemptGroup[] = useMemo(() => {
    if (historyEntries.length === 0) return [];

    const groupMap = new Map<number, AttemptGroup>();

    for (const entry of historyEntries) {
      const attempt = entry.review_attempt;
      let group = groupMap.get(attempt);
      if (!group) {
        group = {
          attempt,
          entries: [],
          planSnapshot: null,
          timestamp: entry.timestamp,
        };
        groupMap.set(attempt, group);
      }
      group.entries.push(entry);
      // First entry with a non-null plan_snapshot wins
      if (!group.planSnapshot && entry.plan_snapshot) {
        group.planSnapshot = entry.plan_snapshot;
      }
    }

    // Sort by attempt number ascending
    return Array.from(groupMap.values()).sort((a, b) => a.attempt - b.attempt);
  }, [historyEntries]);

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
  const isRunning = reviewStatus === "running";

  const handleDecision = async (decision: DecisionType) => {
    setSubmitting(true);
    try {
      await submitReviewDecision(task.id, decision, reason);
      setReason("");
      setSelectedDecision(null);
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

  const handleEditPlan = () => {
    setEditDraft(task.description || "");
    setEditing(true);
  };

  const handleCancelEdit = () => {
    setEditing(false);
    setEditDraft("");
  };

  const handleSavePlan = async () => {
    setSaving(true);
    try {
      const updated = await updateTask(task.id, { description: editDraft });
      setEditing(false);
      setEditDraft("");
      onTaskUpdated?.(updated);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to save plan";
      onError(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleGeneratePlan = async () => {
    setGenerating(true);
    try {
      await generatePlan(task.id);
      // Endpoint auto-saves to task.description; refresh task state
      const updated = await fetchTask(task.id);
      onTaskUpdated?.(updated);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "Failed to generate plan";
      onError(msg);
    } finally {
      setGenerating(false);
    }
  };

  // Can submit: for request_changes, reason is required
  const canSubmitDecision =
    selectedDecision !== null &&
    !submitting &&
    (selectedDecision !== "request_changes" || reason.trim().length > 0);

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

  const humanDecisionColor = (decision: string) => {
    if (decision === "approve") return "text-green-700";
    if (decision === "request_changes") return "text-amber-700";
    return "text-red-700";
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

  // Check if task is in REVIEW with idle status (i.e., after request_changes)
  const showReReviewButton =
    task.status === "review" &&
    reviewStatus === "idle" &&
    historyEntries.some((e) => e.human_decision === "request_changes");

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

  /** Render a single review entry card. */
  const renderEntry = (entry: ReviewHistoryEntry) => (
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
          {entry.cost_usd != null && (
            <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-gray-600" title="Approximate LLM cost">
              ~${entry.cost_usd < 0.01 ? entry.cost_usd.toFixed(4) : entry.cost_usd.toFixed(2)}
            </span>
          )}
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

      {/* Human decision inline + reason */}
      {entry.human_decision && (
        <div className="mt-1.5 pt-1.5 border-t border-gray-200">
          <span className="text-[10px] text-gray-500">
            Human decision:{" "}
          </span>
          <span
            className={`text-[10px] font-semibold ${humanDecisionColor(entry.human_decision)}`}
          >
            {entry.human_decision === "request_changes"
              ? "REQUEST CHANGES"
              : entry.human_decision.toUpperCase()}
          </span>
          {entry.human_reason && entry.human_reason.trim() && (
            <p className="text-[10px] text-gray-500 mt-0.5 italic">
              {entry.human_reason}
            </p>
          )}
        </div>
      )}

      {/* Collapsible raw response (debug) -- hidden when empty/legacy */}
      {entry.raw_response && entry.raw_response.length > 0 && (
        <div className="mt-1.5 pt-1.5 border-t border-gray-200">
          <button
            onClick={() => toggleRawResponse(entry.id)}
            className="text-[10px] text-gray-400 hover:text-gray-600 transition-colors flex items-center gap-1"
          >
            <span className="inline-block transition-transform" style={{
              transform: expandedRaw[entry.id] ? "rotate(90deg)" : "rotate(0deg)",
            }}>
              &#9654;
            </span>
            Show Full Response (debug)
          </button>
          {expandedRaw[entry.id] && (
            <div className="mt-1.5">
              <div className="rounded bg-amber-50 border border-amber-200 px-2 py-1 mb-1.5">
                <p className="text-[10px] text-amber-700">
                  This is the raw LLM output for debugging. Use the structured summary above for decision-making.
                </p>
              </div>
              <pre className="text-[10px] text-gray-600 bg-gray-100 rounded p-2 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap break-words font-mono leading-relaxed">
                {entry.raw_response}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );

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

        {/* review_status == running: spinner + phase label */}
        {reviewStatus === "running" && (
          <div className="flex items-center gap-3 py-4 px-3 rounded-lg bg-blue-50 border border-blue-200">
            <div className="w-5 h-5 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
            <div>
              <p className="text-sm font-medium text-blue-700">
                {reviewPhase || "Review in progress..."}
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

        {/* Re-review button (shown after request_changes when idle) */}
        {showReReviewButton && (
          <div className="py-3 px-3 rounded-lg bg-amber-50 border border-amber-200 space-y-2">
            <p className="text-sm font-medium text-amber-700">
              Changes requested -- ready for re-review
            </p>
            <p className="text-xs text-amber-600">
              Edit the plan if needed, then click Re-review to run the pipeline again with your feedback.
            </p>
            <button
              onClick={handleRetryReview}
              disabled={retrying}
              className="rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {retrying ? "Starting..." : "Re-review"}
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

        {/* Plan Under Review -- collapsible section with inline editor */}
        <div className="rounded-lg border border-gray-200 bg-gray-50">
          <button
            onClick={() => setPlanExpanded((prev) => !prev)}
            className="w-full px-2.5 py-2 flex items-center justify-between text-xs font-semibold text-gray-600 hover:bg-gray-100 transition-colors rounded-lg"
          >
            <span>Plan Under Review</span>
            <span
              className="inline-block transition-transform text-[10px]"
              style={{
                transform: planExpanded ? "rotate(90deg)" : "rotate(0deg)",
              }}
            >
              &#9654;
            </span>
          </button>
          {planExpanded && (
            <div className="px-2.5 pb-2.5">
              {editing ? (
                <div className="space-y-2">
                  <textarea
                    value={editDraft}
                    onChange={(e) => setEditDraft(e.target.value)}
                    rows={8}
                    disabled={saving}
                    className="w-full rounded-md border border-indigo-300 bg-white px-2 py-1.5 text-xs text-gray-700 font-mono focus:outline-none focus:ring-1 focus:ring-indigo-400 focus:border-indigo-400 disabled:opacity-50 resize-y"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={handleSavePlan}
                      disabled={saving}
                      className="rounded-md bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {saving ? "Saving..." : "Save"}
                    </button>
                    <button
                      onClick={handleCancelEdit}
                      disabled={saving}
                      className="rounded-md bg-gray-200 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  {task.description && task.description.trim() ? (
                    <pre className="text-xs text-gray-700 whitespace-pre-wrap break-words font-sans leading-relaxed">
                      {task.description}
                    </pre>
                  ) : (
                    <p className="text-xs text-gray-400 italic">
                      (No plan content provided to reviewer)
                    </p>
                  )}
                  {!isRunning && (
                    <div className="mt-2 flex gap-2">
                      <button
                        onClick={handleEditPlan}
                        disabled={generating}
                        className="rounded-md bg-gray-200 px-2.5 py-1 text-[10px] font-medium text-gray-600 hover:bg-gray-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        title="Edit the plan before re-review"
                      >
                        Edit Plan
                      </button>
                      <button
                        onClick={handleGeneratePlan}
                        disabled={generating}
                        className="rounded-md bg-indigo-100 px-2.5 py-1 text-[10px] font-medium text-indigo-700 hover:bg-indigo-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        title="Generate a structured plan using AI (uses codebase context)"
                      >
                        {generating ? "Generating..." : "Generate Plan"}
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {/* Review history grouped by attempt */}
        {historyLoading && historyEntries.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-2">
            Loading review history...
          </p>
        ) : attemptGroups.length > 0 ? (
          <div className="space-y-3">
            {attemptGroups.map((group, groupIdx) => {
              // Find previous group's plan_snapshot for diff
              const prevSnapshot = groupIdx > 0
                ? attemptGroups[groupIdx - 1].planSnapshot
                : null;
              const currentSnapshot = group.planSnapshot;

              return (
                <div key={group.attempt} className="space-y-2">
                  {/* Attempt header */}
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-bold uppercase text-gray-500 bg-gray-100 px-2 py-0.5 rounded">
                      Attempt {group.attempt}
                    </span>
                    <span className="text-[10px] text-gray-400">
                      {formatTime(group.timestamp)}
                    </span>
                  </div>

                  {/* Plan diff banner (if plan changed from previous attempt) */}
                  {prevSnapshot && currentSnapshot && prevSnapshot !== currentSnapshot && (
                    <PlanDiffView
                      oldText={prevSnapshot}
                      newText={currentSnapshot}
                    />
                  )}

                  {/* Review entries for this attempt */}
                  {group.entries.map(renderEntry)}
                </div>
              );
            })}
          </div>
        ) : hasReview ? (
          <p className="text-xs text-gray-400 text-center py-2">
            No review rounds recorded yet
          </p>
        ) : reviewStatus !== "idle" ? null : null}

        {/* Decision area: 3-button selection + reason + submit */}
        {hasReview && review.human_decision_needed && !review.human_choice && (
          <div className="pt-2 border-t border-gray-200 space-y-2">
            <p className="text-xs text-orange-600 font-medium">
              Human decision required
            </p>

            {/* Disabled tooltip when review is running */}
            {isRunning && (
              <p className="text-[10px] text-blue-500 italic">
                Review in progress, please wait
              </p>
            )}

            {/* 3-button selection row */}
            <div className="flex gap-2">
              <button
                onClick={() => setSelectedDecision("approve")}
                disabled={submitting || isRunning}
                title={isRunning ? "Review in progress, please wait" : "Approve this plan"}
                className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  selectedDecision === "approve"
                    ? "bg-green-600 text-white ring-2 ring-green-400"
                    : "bg-green-50 text-green-700 border border-green-300 hover:bg-green-100"
                }`}
              >
                Approve
              </button>
              <button
                onClick={() => setSelectedDecision("request_changes")}
                disabled={submitting || isRunning}
                title={isRunning ? "Review in progress, please wait" : "Request changes to this plan"}
                className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  selectedDecision === "request_changes"
                    ? "bg-amber-600 text-white ring-2 ring-amber-400"
                    : "bg-amber-50 text-amber-700 border border-amber-300 hover:bg-amber-100"
                }`}
              >
                Request Changes
              </button>
              <button
                onClick={() => setSelectedDecision("reject")}
                disabled={submitting || isRunning}
                title={isRunning ? "Review in progress, please wait" : "Reject this plan"}
                className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  selectedDecision === "reject"
                    ? "bg-red-600 text-white ring-2 ring-red-400"
                    : "bg-red-50 text-red-700 border border-red-300 hover:bg-red-100"
                }`}
              >
                Reject
              </button>
            </div>

            {/* Reason textarea -- border color matches selected decision */}
            {selectedDecision && (
              <>
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder={
                    selectedDecision === "request_changes"
                      ? "Describe the changes needed (required)"
                      : "Reason for your decision (optional)"
                  }
                  rows={2}
                  disabled={submitting || isRunning}
                  className={`w-full rounded-md px-2 py-1.5 text-xs text-gray-700 bg-white placeholder-gray-400 focus:outline-none focus:ring-1 disabled:opacity-50 resize-none border ${
                    selectedDecision === "approve"
                      ? "border-green-300 focus:ring-green-400 focus:border-green-400"
                      : selectedDecision === "request_changes"
                        ? "border-amber-300 focus:ring-amber-400 focus:border-amber-400"
                        : "border-red-300 focus:ring-red-400 focus:border-red-400"
                  }`}
                />
                <button
                  onClick={() => handleDecision(selectedDecision)}
                  disabled={!canSubmitDecision || isRunning}
                  title={
                    isRunning
                      ? "Review in progress, please wait"
                      : selectedDecision === "request_changes" && !reason.trim()
                        ? "Reason is required for Request Changes"
                        : undefined
                  }
                  className={`w-full rounded-md px-3 py-1.5 text-sm font-medium text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                    selectedDecision === "approve"
                      ? "bg-green-600 hover:bg-green-700"
                      : selectedDecision === "request_changes"
                        ? "bg-amber-600 hover:bg-amber-700"
                        : "bg-red-600 hover:bg-red-700"
                  }`}
                >
                  {submitting
                    ? "Submitting..."
                    : selectedDecision === "approve"
                      ? "Confirm Approve"
                      : selectedDecision === "request_changes"
                        ? "Submit Changes Request"
                        : "Confirm Reject"}
                </button>
              </>
            )}
          </div>
        )}

        {/* Human choice already made */}
        {hasReview && review.human_choice && (
          <div className="text-xs pt-2 border-t border-gray-200">
            <span className="text-gray-500">Human decision: </span>
            <span
              className={`font-semibold ${humanDecisionColor(review.human_choice)}`}
            >
              {review.human_choice === "request_changes"
                ? "REQUEST CHANGES"
                : review.human_choice.toUpperCase()}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
