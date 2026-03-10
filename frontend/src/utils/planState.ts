/**
 * planState.ts -- shared utility for computing optimistic plan state patches.
 *
 * Single source of truth for which Task fields must be cleared/set when
 * the plan_status transitions. Used by TaskCard, TaskCardPopover,
 * PlanReviewPanel, and useSSEHandler to avoid fragile inline clearing.
 */

import type { Task, PlanStatus } from "../types";

/**
 * Return a partial Task object with the correct field values for a given
 * plan status transition.  Callers spread this into their optimistic update:
 *
 *   onTaskUpdated({ ...task, ...planStatePatch("generating") })
 */
export function planStatePatch(
  status: PlanStatus | "decomposed",
  opts?: {
    /** Generation ID from backend 202 response or SSE event. */
    generationId?: string;
    /** Error fields for "failed" status. */
    errorType?: string;
    errorMessage?: string;
    /** Proposed tasks for "ready" status. */
    proposedTasks?: Task["proposed_tasks"];
    /** Formatted plan text for "ready" status (from SSE event). */
    description?: string;
  },
): Partial<Task> {
  const base: Partial<Task> = { plan_status: status as Task["plan_status"] };

  switch (status) {
    case "none":
      return {
        ...base,
        plan_error_type: undefined,
        plan_error_message: undefined,
        proposed_tasks: undefined,
        plan_generation_id: undefined,
        has_proposed_tasks: false,
      };

    case "generating":
      return {
        ...base,
        plan_error_type: undefined,
        plan_error_message: undefined,
        proposed_tasks: undefined,
        ...(opts?.generationId != null
          ? { plan_generation_id: opts.generationId }
          : {}),
      };

    case "failed":
      return {
        ...base,
        plan_error_type: opts?.errorType,
        plan_error_message: opts?.errorMessage,
        proposed_tasks: undefined,
      };

    case "ready":
      return {
        ...base,
        plan_error_type: undefined,
        plan_error_message: undefined,
        ...(opts?.proposedTasks != null
          ? { proposed_tasks: opts.proposedTasks, has_proposed_tasks: opts.proposedTasks.length > 0 }
          : {}),
        ...(opts?.generationId != null
          ? { plan_generation_id: opts.generationId }
          : {}),
        ...(opts?.description != null
          ? { description: opts.description }
          : {}),
      };

    case "decomposed":
      return {
        ...base,
        plan_error_type: undefined,
        plan_error_message: undefined,
      };

    default:
      return base;
  }
}
