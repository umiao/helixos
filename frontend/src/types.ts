/**
 * TypeScript interfaces matching backend Pydantic models (src/models.py).
 */

export type TaskStatus =
  | "backlog"
  | "review"
  | "review_auto_approved"
  | "review_needs_human"
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "blocked";

export type ExecutorType = "code" | "agent" | "scheduled";

export interface Project {
  id: string;
  name: string;
  repo_path: string | null;
  workspace_path: string | null;
  tasks_file: string;
  executor_type: ExecutorType;
  max_concurrency: number;
  env_keys: string[];
  claude_md_path: string | null;
  execution_paused: boolean;
  review_gate_enabled: boolean;
}

export interface ReviewState {
  rounds_total: number;
  rounds_completed: number;
  consensus_score: number | null;
  human_decision_needed: boolean;
  decision_points: string[];
  human_choice: string | null;
}

export interface ExecutionState {
  started_at: string | null;
  finished_at: string | null;
  retry_count: number;
  max_retries: number;
  exit_code: number | null;
  log_tail: string[];
  result: string;
  error_summary: string | null;
}

export type ReviewStatus = "idle" | "running" | "done" | "failed";

export interface Task {
  id: string;
  project_id: string;
  local_task_id: string;
  title: string;
  description: string;
  status: TaskStatus;
  executor_type: ExecutorType;
  depends_on: string[];
  review: ReviewState | null;
  execution: ExecutionState | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  review_status: ReviewStatus;
}

export interface Dependency {
  upstream_task: string;
  downstream_task: string;
  contract_path: string | null;
  fulfilled: boolean;
}

/** Kanban column identifiers. */
export type KanbanColumn = "BACKLOG" | "REVIEW" | "QUEUED" | "RUNNING" | "DONE";

/** Map task statuses to Kanban columns. */
export const STATUS_TO_COLUMN: Record<TaskStatus, KanbanColumn> = {
  backlog: "BACKLOG",
  review: "REVIEW",
  review_auto_approved: "REVIEW",
  review_needs_human: "REVIEW",
  queued: "QUEUED",
  running: "RUNNING",
  done: "DONE",
  failed: "DONE",
  blocked: "DONE",
};

export const KANBAN_COLUMNS: KanbanColumn[] = [
  "BACKLOG",
  "REVIEW",
  "QUEUED",
  "RUNNING",
  "DONE",
];

/** Map Kanban columns to the target task status for drag-drop transitions. */
export const COLUMN_TO_STATUS: Record<KanbanColumn, TaskStatus> = {
  BACKLOG: "backlog",
  REVIEW: "review",
  QUEUED: "queued",
  RUNNING: "running",
  DONE: "done",
};

/** Process status for a project's dev server. */
export interface ProcessStatus {
  running: boolean;
  pid: number | null;
  port: number | null;
  uptime_seconds: number | null;
}

/** Result of validating a project directory. */
export interface ValidationResult {
  valid: boolean;
  name: string;
  path: string;
  has_git: boolean;
  has_tasks_md: boolean;
  has_claude_config: boolean;
  suggested_id: string;
  warnings: string[];
  limited_mode_reasons: string[];
}

/** Sync result for a single project. */
export interface SyncResult {
  project_id: string;
  added: number;
  updated: number;
  unchanged: number;
  warnings: string[];
}

/** Result of importing a project. */
export interface ImportResult {
  project_id: string;
  name: string;
  repo_path: string;
  port: number | null;
  synced: boolean;
  sync_result: SyncResult | null;
  warnings: string[];
}

/** Result of AI-assisted task enrichment. */
export interface EnrichResult {
  description: string;
  priority: string;
}

/** Result of creating a task. */
export interface CreateTaskResult {
  task_id: string;
  success: boolean;
  backup_path: string | null;
  synced: boolean;
  sync_result: SyncResult | null;
  error: string | null;
}

/** A single entry returned by the directory browser. */
export interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
  has_git: boolean;
  has_tasks_md: boolean;
  has_claude_md: boolean;
}

/** Response from the directory browser endpoint. */
export interface BrowseResult {
  path: string;
  parent: string | null;
  entries: BrowseEntry[];
}

/** A single execution log entry from the database. */
export interface ExecutionLogEntry {
  id: number;
  task_id: string;
  timestamp: string;
  level: string;
  message: string;
  source: string;
}

/** Paginated execution logs response. */
export interface ExecutionLogsResponse {
  task_id: string;
  total: number;
  offset: number;
  limit: number;
  entries: ExecutionLogEntry[];
}

/** A single review history entry from the database. */
export interface ReviewHistoryEntry {
  id: number;
  task_id: string;
  round_number: number;
  reviewer_model: string;
  reviewer_focus: string;
  verdict: string;
  summary: string;
  suggestions: string[];
  consensus_score: number | null;
  human_decision: string | null;
  human_reason: string | null;
  raw_response: string;
  cost_usd: number | null;
  review_attempt: number;
  timestamp: string;
}

/** Paginated review history response. */
export interface ReviewHistoryResponse {
  task_id: string;
  total: number;
  offset: number;
  limit: number;
  entries: ReviewHistoryEntry[];
}
