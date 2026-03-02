/**
 * API client with typed functions for the HelixOS backend.
 * All calls go through the Vite dev proxy (/api -> localhost:8000).
 */

import type {
  BrowseResult,
  CreateTaskResult,
  ImportResult,
  ProcessStatus,
  Project,
  SyncResult,
  Task,
  TaskStatus,
  ValidationResult,
} from "./types";

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
  results: SyncResult[];
}> {
  const res = await fetch("/api/sync-all", { method: "POST" });
  return handleResponse(res);
}

/** Sync a single project's TASKS.md. */
export async function syncProject(projectId: string): Promise<SyncResult> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/sync`,
    { method: "POST" },
  );
  return handleResponse<SyncResult>(res);
}

/** Browse a directory on the server (sandboxed to $HOME). */
export async function browseDirectory(
  path?: string,
): Promise<BrowseResult> {
  const params = new URLSearchParams();
  if (path) params.set("path", path);
  const qs = params.toString();
  const url = qs ? `/api/filesystem/browse?${qs}` : "/api/filesystem/browse";
  const res = await fetch(url);
  return handleResponse<BrowseResult>(res);
}

/** Validate a directory for project import. */
export async function validateProject(
  path: string,
): Promise<ValidationResult> {
  const res = await fetch("/api/projects/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return handleResponse<ValidationResult>(res);
}

/** Import a project into the orchestrator. */
export async function importProject(params: {
  path: string;
  project_id?: string;
  name?: string;
  project_type?: string;
  launch_command?: string;
  preferred_port?: number;
}): Promise<ImportResult> {
  const res = await fetch("/api/projects/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  return handleResponse<ImportResult>(res);
}

/** Create a new task in a project's TASKS.md. */
export async function createTask(
  projectId: string,
  params: { title: string; description?: string; priority?: string },
): Promise<CreateTaskResult> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/tasks`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
  );
  return handleResponse<CreateTaskResult>(res);
}

/** Launch the dev server for a project. */
export async function launchProject(
  projectId: string,
): Promise<ProcessStatus> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/launch`,
    { method: "POST" },
  );
  return handleResponse<ProcessStatus>(res);
}

/** Stop the dev server for a project. */
export async function stopProject(
  projectId: string,
): Promise<{ detail: string; project_id: string }> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/stop`,
    { method: "POST" },
  );
  return handleResponse(res);
}

/** Get the dev server status for a project. */
export async function getProcessStatus(
  projectId: string,
): Promise<ProcessStatus> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/process-status`,
  );
  return handleResponse<ProcessStatus>(res);
}
