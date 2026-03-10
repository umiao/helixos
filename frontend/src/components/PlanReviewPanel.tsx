/**
 * PlanReviewPanel -- unified plan review before batch task decomposition.
 *
 * Shows the generated plan summary and all proposed sub-tasks as a readable
 * document. Provides "Confirm and Create All Tasks", "Reject Plan", and
 * "Delete Plan" actions. Handles generating (cancel), failed (retry + delete),
 * decomposed (delete with warning), and ready states.
 *
 * T-P1-116, T-P0-136
 */

import { useState } from "react";
import { confirmGeneratedTasks, deletePlan, generatePlan, rejectPlan, updateTask } from "../api";
import type { Task } from "../types";
import { planStatePatch } from "../utils/planState";
import MarkdownRenderer from "./MarkdownRenderer";
import { ProposedTaskCard } from "./PlanComponents";

interface PlanReviewPanelProps {
  task: Task;
  onTaskUpdated: (updated: Task) => void;
  onError: (msg: string) => void;
  onConfirmed: (taskId: string, writtenIds: string[]) => void;
}

/** Inline confirmation for dangerous delete operations. */
function DeleteConfirmation({
  warningText,
  onConfirm,
  onCancel,
  deleting,
}: {
  warningText: string;
  onConfirm: () => void;
  onCancel: () => void;
  deleting: boolean;
}) {
  return (
    <div className="flex items-center gap-2 bg-red-50 border border-red-200 rounded px-3 py-1.5">
      <span className="text-xs text-red-700">{warningText}</span>
      <button
        onClick={onConfirm}
        disabled={deleting}
        className="px-2 py-0.5 text-xs font-medium bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
      >
        {deleting ? "Deleting..." : "Yes, Delete"}
      </button>
      <button
        onClick={onCancel}
        disabled={deleting}
        className="px-2 py-0.5 text-xs font-medium text-gray-600 hover:text-gray-800 disabled:opacity-50"
      >
        Cancel
      </button>
    </div>
  );
}

export default function PlanReviewPanel({
  task,
  onTaskUpdated,
  onError,
  onConfirmed,
}: PlanReviewPanelProps) {
  const [confirming, setConfirming] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // Inline plan editor state
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [editPreview, setEditPreview] = useState(false);

  const proposedTasks = task.proposed_tasks ?? [];

  const handleEditPlan = () => {
    setEditDraft(task.description || "");
    setEditing(true);
    setEditPreview(false);
  };

  const handleCancelEdit = () => {
    setEditing(false);
    setEditDraft("");
    setEditPreview(false);
  };

  const handleSavePlan = async () => {
    setSaving(true);
    try {
      const updated = await updateTask(task.id, { description: editDraft });
      setEditing(false);
      setEditDraft("");
      onTaskUpdated(updated);
    } catch (err) {
      onError(`Save failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deletePlan(task.id);
      onTaskUpdated({ ...task, ...planStatePatch("none") });
    } catch (err) {
      onError(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDeleting(false);
      setShowDeleteConfirm(false);
    }
  };

  // -- Generating state: spinner + cancel link (AC5)
  if (task.plan_status === "generating") {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-500">
        <div className="animate-spin h-8 w-8 border-2 border-indigo-500 border-t-transparent rounded-full" />
        <span className="text-sm">Generating plan for {task.local_task_id}...</span>
        {showDeleteConfirm ? (
          <DeleteConfirmation
            warningText="Cancel plan generation?"
            onConfirm={handleDelete}
            onCancel={() => setShowDeleteConfirm(false)}
            deleting={deleting}
          />
        ) : (
          <button
            onClick={() => setShowDeleteConfirm(true)}
            className="text-xs text-red-500 hover:text-red-700 underline"
          >
            Cancel
          </button>
        )}
      </div>
    );
  }

  // -- Failed state: error message with retry + delete (AC5, AC6)
  if (task.plan_status === "failed") {
    const handleRetry = async () => {
      setRetrying(true);
      try {
        const accepted = await generatePlan(task.id);
        onTaskUpdated({ ...task, ...planStatePatch("generating", { generationId: accepted.generation_id }) });
      } catch (err) {
        onError(`Retry failed: ${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setRetrying(false);
      }
    };

    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-500">
        <div className="text-red-500 text-sm font-medium">Plan generation failed</div>
        {task.plan_error_message && (
          <p className="text-xs text-gray-600 max-w-md text-center">{task.plan_error_message}</p>
        )}
        {task.plan_error_type && (
          <span className="text-[10px] font-mono text-gray-400">{task.plan_error_type}</span>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={handleRetry}
            disabled={retrying || deleting}
            className="px-3 py-1.5 text-xs font-medium bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
          >
            {retrying ? "Retrying..." : "Retry"}
          </button>
          {!showDeleteConfirm && (
            <button
              onClick={() => setShowDeleteConfirm(true)}
              disabled={retrying || deleting}
              className="px-3 py-1.5 text-xs font-medium text-red-600 border border-red-300 rounded hover:bg-red-50 disabled:opacity-50"
            >
              Delete Plan
            </button>
          )}
        </div>
        {showDeleteConfirm && (
          <DeleteConfirmation
            warningText="This will remove the failed plan."
            onConfirm={handleDelete}
            onCancel={() => setShowDeleteConfirm(false)}
            deleting={deleting}
          />
        )}
      </div>
    );
  }

  // -- No plan state
  if (task.plan_status === "none") {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-gray-400">
        <span className="text-sm">No plan generated for {task.local_task_id}</span>
        <span className="text-xs">Use the "Plan" button on the task card to generate one.</span>
      </div>
    );
  }

  // -- Decomposed state with delete option (AC7)
  if (task.plan_status === "decomposed") {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-gray-500">
        <span className="text-sm font-medium text-green-600">Plan decomposed</span>
        <span className="text-xs">Tasks have been created from this plan.</span>
        {showDeleteConfirm ? (
          <DeleteConfirmation
            warningText="This will not remove already-created subtasks."
            onConfirm={handleDelete}
            onCancel={() => setShowDeleteConfirm(false)}
            deleting={deleting}
          />
        ) : (
          <button
            onClick={() => setShowDeleteConfirm(true)}
            disabled={deleting}
            className="px-3 py-1.5 text-xs font-medium text-red-600 border border-red-300 rounded hover:bg-red-50 disabled:opacity-50 mt-2"
          >
            Delete Plan
          </button>
        )}
      </div>
    );
  }

  // -- Ready state: unified plan review (AC2, AC4)
  const handleConfirm = async () => {
    setConfirming(true);
    try {
      const result = await confirmGeneratedTasks(task.id);
      onTaskUpdated({ ...task, ...planStatePatch("decomposed") });
      onConfirmed(task.id, result.written_ids);
    } catch (err) {
      onError(`Confirm failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setConfirming(false);
    }
  };

  const handleReject = async () => {
    setRejecting(true);
    try {
      await rejectPlan(task.id);
      onTaskUpdated({ ...task, ...planStatePatch("none") });
    } catch (err) {
      onError(`Reject failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setRejecting(false);
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 bg-gray-50 shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-800">Plan Review</span>
          <span className="text-xs font-mono text-gray-500">{task.local_task_id}</span>
          <span className="text-xs text-gray-400">|</span>
          <span className="text-xs text-gray-600">{task.title}</span>
        </div>
        <div className="flex items-center gap-2">
          {showDeleteConfirm ? (
            <DeleteConfirmation
              warningText="Delete this plan? This cannot be undone."
              onConfirm={handleDelete}
              onCancel={() => setShowDeleteConfirm(false)}
              deleting={deleting}
            />
          ) : (
            <>
              <button
                onClick={handleEditPlan}
                disabled={editing || rejecting || confirming || deleting}
                className="px-3 py-1 text-xs font-medium text-gray-600 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
                title="Edit the plan text before confirming"
              >
                Edit Plan
              </button>
              <button
                onClick={() => setShowDeleteConfirm(true)}
                disabled={editing || rejecting || confirming || deleting}
                className="px-3 py-1 text-xs font-medium text-red-600 border border-red-300 rounded hover:bg-red-50 disabled:opacity-50"
              >
                Delete Plan
              </button>
              <button
                onClick={handleReject}
                disabled={editing || rejecting || confirming || deleting}
                className="px-3 py-1 text-xs font-medium text-gray-600 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
              >
                {rejecting ? "Rejecting..." : "Reject Plan"}
              </button>
              <button
                onClick={handleConfirm}
                disabled={editing || confirming || rejecting || deleting}
                className="px-3 py-1 text-xs font-medium bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
              >
                {confirming ? "Creating Tasks..." : `Confirm and Create All Tasks (${proposedTasks.length})`}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {/* Plan summary -- rendered as markdown, or editable textarea */}
        <div>
          <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">Plan Summary</h3>
          {editing ? (
            <div className="space-y-2">
              {/* Edit / Preview tabs */}
              <div className="flex gap-1 border-b border-gray-200 pb-1">
                <button
                  onClick={() => setEditPreview(false)}
                  className={`px-2 py-0.5 text-[10px] font-medium rounded-t ${!editPreview ? "bg-indigo-100 text-indigo-700" : "text-gray-500 hover:text-gray-700"}`}
                >
                  Edit
                </button>
                <button
                  onClick={() => setEditPreview(true)}
                  className={`px-2 py-0.5 text-[10px] font-medium rounded-t ${editPreview ? "bg-indigo-100 text-indigo-700" : "text-gray-500 hover:text-gray-700"}`}
                >
                  Preview
                </button>
              </div>
              {editPreview ? (
                <MarkdownRenderer
                  content={editDraft}
                  maxHeight="none"
                  showSizeToggle={false}
                />
              ) : (
                <textarea
                  value={editDraft}
                  onChange={(e) => setEditDraft(e.target.value)}
                  rows={12}
                  disabled={saving}
                  className="w-full rounded-md border border-indigo-300 bg-white px-2 py-1.5 text-xs text-gray-700 font-mono focus:outline-none focus:ring-1 focus:ring-indigo-400 focus:border-indigo-400 disabled:opacity-50 resize-y"
                />
              )}
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
          ) : task.description?.trim() ? (
            <MarkdownRenderer
              content={task.description}
              maxHeight="none"
              showSizeToggle={false}
            />
          ) : (
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
              <span className="text-xs text-gray-400">(No plan text)</span>
            </div>
          )}
        </div>

        {/* Proposed tasks */}
        {proposedTasks.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">
              Proposed Tasks ({proposedTasks.length})
            </h3>
            <div className="space-y-2">
              {proposedTasks.map((pt, i) => (
                <ProposedTaskCard key={i} task={pt} index={i} />
              ))}
            </div>
          </div>
        )}

        {proposedTasks.length === 0 && (
          <div className="text-xs text-gray-400 text-center py-4">
            No proposed tasks in the plan. The plan may only contain implementation steps.
          </div>
        )}
      </div>
    </div>
  );
}
