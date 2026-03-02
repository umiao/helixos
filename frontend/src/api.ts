/**
 * API client with typed functions for the HelixOS backend.
 * All calls go through the Vite dev proxy (/api -> localhost:8000).
 */

import type { Project, Task, TaskStatus } from "./types";

/** Error class for API errors with status code and detail. */
export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Parse an API error from a Response object. */
async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) {
        detail = body.detail;
      }
    } catch {
      // body not JSON, keep generic message
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

/** Fetch all projects. */
export async function fetchProjects(): Promise<Project[]> {
  const res = await fetch("/api/projects");
  return handleResponse<Project[]>(res);
}

/** Fetch all tasks, optionally filtered by project_id or status. */
export async function fetchTasks(filters?: {
  project_id?: string;
  status?: string;
}): Promise<Task[]> {
  const params = new URLSearchParams();
  if (filters?.project_id) params.set("project_id", filters.project_id);
  if (filters?.status) params.set("status", filters.status);
  const qs = params.toString();
  const url = qs ? `/api/tasks?${qs}` : "/api/tasks";
  const res = await fetch(url);
  return handleResponse<Task[]>(res);
}

/** Fetch a single task by ID. */
export async function fetchTask(taskId: string): Promise<Task> {
  const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
  return handleResponse<Task>(res);
}

/** Update a task's status via PATCH. Returns the updated task. */
export async function updateTaskStatus(
  taskId: string,
  status: TaskStatus,
): Promise<Task> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    },
  );
  return handleResponse<Task>(res);
}

/** Submit a human review decision (approve/reject) for a task. */
export async function submitReviewDecision(
  taskId: string,
  decision: string,
  reason: string = "",
): Promise<Task> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/review/decide`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, reason }),
    },
  );
  return handleResponse<Task>(res);
}

/** Trigger sync for all projects. Returns sync results. */
export async function syncAll(): Promise<{
  results: Array<{
    project_id: string;
    added: number;
    updated: number;
    unchanged: number;
    warnings: string[];
  }>;
}> {
  const res = await fetch("/api/sync-all", { method: "POST" });
  return handleResponse(res);
}
