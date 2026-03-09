/**
 * API client with typed functions for the HelixOS backend.
 * All calls go through the Vite dev proxy (/api -> localhost:8000).
 */

import type {
  BrowseResult,
  ConfirmGeneratedTasksResponse,
  CostDashboardResponse,
  CreateTaskResult,
  EnrichResult,
  ExecutionLogsResponse,
  GeneratePlanAccepted,
  ImportResult,
  ProcessStatus,
  Project,
  ReviewHistoryResponse,
  StartAllPlannedResponse,
  StreamLogResponse,
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

/** Update a task's title and/or description via PATCH. Returns the updated task. */
export async function updateTask(
  taskId: string,
  fields: { title?: string; description?: string },
): Promise<Task> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    },
  );
  return handleResponse<Task>(res);
}

/** Update a task's status via PATCH. Returns the updated task.
 *  Supports optional reason (for backward transitions) and
 *  expected_updated_at (optimistic locking). */
export async function updateTaskStatus(
  taskId: string,
  status: TaskStatus,
  opts?: { reason?: string; expected_updated_at?: string; force_decompose_bypass?: boolean },
): Promise<Task> {
  const payload: Record<string, unknown> = { status };
  if (opts?.reason) payload.reason = opts.reason;
  if (opts?.expected_updated_at) payload.expected_updated_at = opts.expected_updated_at;
  if (opts?.force_decompose_bypass) payload.force_decompose_bypass = opts.force_decompose_bypass;

  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/status`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );

  // Handle review gate blocked (428)
  if (res.status === 428) {
    let detail = "Review gate blocked";
    let gateAction = "";
    let blockedTaskId = "";
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
      if (body.gate_action) gateAction = body.gate_action;
      if (body.task_id) blockedTaskId = body.task_id;
    } catch { /* ignore */ }
    const err = new ApiError(428, detail);
    (err as ApiError & { gate_action?: string; task_id?: string }).gate_action = gateAction;
    (err as ApiError & { gate_action?: string; task_id?: string }).task_id = blockedTaskId;
    throw err;
  }

  // Handle conflict response (optimistic lock failure)
  if (res.status === 409) {
    let detail = "Conflict";
    let conflict = false;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
      if (body.conflict) conflict = body.conflict;
    } catch { /* ignore */ }
    const err = new ApiError(409, detail);
    (err as ApiError & { conflict?: boolean }).conflict = conflict;
    throw err;
  }

  return handleResponse<Task>(res);
}

/** Retry the review pipeline for a task. Returns 202 on success. */
export async function retryReview(
  taskId: string,
): Promise<{ detail: string; task_id: string }> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/review`,
    { method: "POST" },
  );
  return handleResponse(res);
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

/** Enrich a task title with AI-suggested description and priority. */
export async function enrichTask(title: string): Promise<EnrichResult> {
  const res = await fetch("/api/tasks/enrich", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  return handleResponse<EnrichResult>(res);
}

/** Start async plan generation for a task (returns 202 immediately). */
export async function generatePlan(
  taskId: string,
): Promise<GeneratePlanAccepted> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/generate-plan`,
    { method: "POST" },
  );
  return handleResponse<GeneratePlanAccepted>(res);
}

/** Pause task execution for a project. */
export async function pauseExecution(
  projectId: string,
): Promise<{ detail: string; project_id: string; paused: boolean }> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/pause-execution`,
    { method: "POST" },
  );
  return handleResponse(res);
}

/** Resume task execution for a project. */
export async function resumeExecution(
  projectId: string,
): Promise<{ detail: string; project_id: string; paused: boolean }> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/resume-execution`,
    { method: "POST" },
  );
  return handleResponse(res);
}

/** Start all planned (BACKLOG + plan_status=ready) tasks for a project. */
export async function startAllPlanned(
  projectId: string,
): Promise<StartAllPlannedResponse> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/start-all-planned`,
    { method: "POST" },
  );
  return handleResponse<StartAllPlannedResponse>(res);
}

/** Toggle the review gate for a project. */
export async function setReviewGate(
  projectId: string,
  enabled: boolean,
): Promise<{
  detail: string;
  project_id: string;
  review_gate_enabled: boolean;
}> {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/review-gate?enabled=${enabled}`,
    { method: "PATCH" },
  );
  return handleResponse(res);
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

/** Soft-delete a task. Returns void on 204, throws ApiError on failure. */
export async function deleteTask(
  taskId: string,
  force: boolean = false,
): Promise<void> {
  const params = new URLSearchParams();
  if (force) params.set("force", "true");
  const qs = params.toString();
  const url = `/api/tasks/${encodeURIComponent(taskId)}${qs ? `?${qs}` : ""}`;
  const res = await fetch(url, { method: "DELETE" });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    let dependents: string[] | undefined;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
      if (body.dependents) dependents = body.dependents;
    } catch {
      // body not JSON
    }
    const err = new ApiError(res.status, detail);
    (err as ApiError & { dependents?: string[] }).dependents = dependents;
    throw err;
  }
}

/** Fetch paginated execution logs for a task. */
export async function fetchExecutionLogs(
  taskId: string,
  opts?: { limit?: number; offset?: number; level?: string },
): Promise<ExecutionLogsResponse> {
  const params = new URLSearchParams();
  if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts?.offset !== undefined) params.set("offset", String(opts.offset));
  if (opts?.level) params.set("level", opts.level);
  const qs = params.toString();
  const url = `/api/tasks/${encodeURIComponent(taskId)}/logs${qs ? `?${qs}` : ""}`;
  const res = await fetch(url);
  return handleResponse<ExecutionLogsResponse>(res);
}

/** Fetch the stream-json log (persisted JSONL events) for a task. */
export async function fetchStreamLog(
  taskId: string,
): Promise<StreamLogResponse> {
  const url = `/api/tasks/${encodeURIComponent(taskId)}/stream-log`;
  const res = await fetch(url);
  return handleResponse<StreamLogResponse>(res);
}

/** Cancel a running task. Returns confirmation with graceful/forced indicator. */
export async function cancelTask(
  taskId: string,
): Promise<{ detail: string; task_id: string; graceful: boolean }> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/cancel`,
    { method: "POST" },
  );
  return handleResponse(res);
}

/** Fetch paginated review history for a task. */
export async function fetchReviewHistory(
  taskId: string,
  opts?: { limit?: number; offset?: number },
): Promise<ReviewHistoryResponse> {
  const params = new URLSearchParams();
  if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts?.offset !== undefined) params.set("offset", String(opts.offset));
  const qs = params.toString();
  const url = `/api/tasks/${encodeURIComponent(taskId)}/reviews${qs ? `?${qs}` : ""}`;
  const res = await fetch(url);
  return handleResponse<ReviewHistoryResponse>(res);
}

/** Fetch aggregate cost/usage data grouped by project. */
export async function fetchCostDashboard(): Promise<CostDashboardResponse> {
  const res = await fetch("/api/dashboard/costs");
  return handleResponse<CostDashboardResponse>(res);
}

/** Confirm generated tasks -- batch-write proposed tasks to TASKS.md. */
export async function confirmGeneratedTasks(
  taskId: string,
): Promise<ConfirmGeneratedTasksResponse> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/confirm-generated-tasks`,
    { method: "POST" },
  );
  return handleResponse<ConfirmGeneratedTasksResponse>(res);
}

/** Reject a plan, resetting plan_status to 'none'. */
export async function rejectPlan(
  taskId: string,
): Promise<{ task_id: string; plan_status: string }> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/reject-plan`,
    { method: "POST" },
  );
  return handleResponse(res);
}

/** Delete a plan from any non-none state, resetting plan_status to 'none'. */
export async function deletePlan(
  taskId: string,
): Promise<{ task_id: string; plan_status: string; previous_status: string }> {
  const res = await fetch(
    `/api/tasks/${encodeURIComponent(taskId)}/plan`,
    { method: "DELETE" },
  );
  return handleResponse(res);
}
