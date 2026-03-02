/**
 * API client stubs with typed functions.
 * Currently returns mock data; will be replaced with real fetch calls in T-P0-8b.
 */

import type { Project, Task } from "./types";

const MOCK_PROJECTS: Project[] = [
  {
    id: "helixos",
    name: "HelixOS",
    repo_path: "/home/user/helixos",
    workspace_path: "/home/user/helixos",
    tasks_file: "TASKS.md",
    executor_type: "code",
    max_concurrency: 2,
    env_keys: ["ANTHROPIC_API_KEY"],
    claude_md_path: null,
  },
  {
    id: "data-pipeline",
    name: "Data Pipeline",
    repo_path: "/home/user/data-pipeline",
    workspace_path: "/home/user/data-pipeline",
    tasks_file: "TASKS.md",
    executor_type: "code",
    max_concurrency: 1,
    env_keys: [],
    claude_md_path: null,
  },
];

const MOCK_TASKS: Task[] = [
  {
    id: "helixos:T-P0-10",
    project_id: "helixos",
    local_task_id: "T-P0-10",
    title: "API endpoints (CRUD + sync + execute + review)",
    description: "Full REST API for the orchestrator dashboard.",
    status: "backlog",
    executor_type: "code",
    depends_on: ["helixos:T-P0-6b", "helixos:T-P0-7"],
    review: null,
    execution: null,
    created_at: "2026-03-01T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
    completed_at: null,
  },
  {
    id: "helixos:T-P0-8b",
    project_id: "helixos",
    local_task_id: "T-P0-8b",
    title: "Dashboard drag-drop + API integration",
    description: "Kanban drag-drop with real API calls.",
    status: "review",
    executor_type: "code",
    depends_on: ["helixos:T-P0-8a", "helixos:T-P0-10"],
    review: {
      rounds_total: 3,
      rounds_completed: 2,
      reviews: [],
      consensus_score: 0.85,
      human_decision_needed: false,
      decision_points: [],
      human_choice: null,
    },
    execution: null,
    created_at: "2026-03-01T01:00:00Z",
    updated_at: "2026-03-01T02:00:00Z",
    completed_at: null,
  },
  {
    id: "helixos:T-P0-13",
    project_id: "helixos",
    local_task_id: "T-P0-13",
    title: "Integration testing (end-to-end)",
    description: "Full lifecycle tests with mocked externals.",
    status: "queued",
    executor_type: "code",
    depends_on: ["helixos:T-P0-10"],
    review: null,
    execution: null,
    created_at: "2026-03-01T02:00:00Z",
    updated_at: "2026-03-01T03:00:00Z",
    completed_at: null,
  },
  {
    id: "data-pipeline:T-P0-1",
    project_id: "data-pipeline",
    local_task_id: "T-P0-1",
    title: "Set up ingestion pipeline",
    description: "Configure data sources and ingestion schedule.",
    status: "running",
    executor_type: "code",
    depends_on: [],
    review: null,
    execution: {
      started_at: "2026-03-01T04:00:00Z",
      finished_at: null,
      retry_count: 0,
      max_retries: 3,
      exit_code: null,
      log_tail: ["Starting pipeline setup...", "Configuring sources..."],
      result: "pending",
      error_summary: null,
    },
    created_at: "2026-03-01T03:00:00Z",
    updated_at: "2026-03-01T04:00:00Z",
    completed_at: null,
  },
  {
    id: "helixos:T-P0-9",
    project_id: "helixos",
    local_task_id: "T-P0-9",
    title: "SSE event stream endpoint",
    description: "Server-sent events for real-time dashboard updates.",
    status: "done",
    executor_type: "code",
    depends_on: ["helixos:T-P0-6a"],
    review: null,
    execution: {
      started_at: "2026-03-01T04:30:00Z",
      finished_at: "2026-03-01T05:00:00Z",
      retry_count: 0,
      max_retries: 3,
      exit_code: 0,
      log_tail: ["All tests passing.", "Build complete."],
      result: "success",
      error_summary: null,
    },
    created_at: "2026-03-01T04:00:00Z",
    updated_at: "2026-03-01T05:00:00Z",
    completed_at: "2026-03-01T05:00:00Z",
  },
];

/** Fetch all projects. */
export async function fetchProjects(): Promise<Project[]> {
  // TODO: Replace with fetch("/api/projects") in T-P0-8b
  return MOCK_PROJECTS;
}

/** Fetch all tasks, optionally filtered by project_id or status. */
export async function fetchTasks(filters?: {
  project_id?: string;
  status?: string;
}): Promise<Task[]> {
  // TODO: Replace with fetch("/api/tasks?...") in T-P0-8b
  let tasks = MOCK_TASKS;
  if (filters?.project_id) {
    tasks = tasks.filter((t) => t.project_id === filters.project_id);
  }
  if (filters?.status) {
    tasks = tasks.filter((t) => t.status === filters.status);
  }
  return tasks;
}

/** Fetch a single task by ID. */
export async function fetchTask(taskId: string): Promise<Task | undefined> {
  // TODO: Replace with fetch(`/api/tasks/${taskId}`) in T-P0-8b
  return MOCK_TASKS.find((t) => t.id === taskId);
}

/** Trigger sync for all projects. */
export async function syncAll(): Promise<void> {
  // TODO: Replace with fetch("/api/sync-all", { method: "POST" }) in T-P0-8b
  console.log("[mock] Sync all triggered");
}
