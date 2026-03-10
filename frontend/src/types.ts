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
  is_primary: boolean;
}

/** A clarifying question raised during review. */
export interface ReviewQuestion {
  id: string;
  text: string;
  answer: string;
  source_reviewer: string;
  created_at: string;
  answered_at: string | null;
}

export interface ReviewState {
  rounds_total: number;
  rounds_completed: number;
  consensus_score: number | null;
  human_decision_needed: boolean;
  decision_points: string[];
  human_choice: string | null;
  questions: ReviewQuestion[];
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

/** Plan generation lifecycle state -- backend is single source of truth. */
export type PlanStatus = "none" | "generating" | "failed" | "ready";

/** Canonical review lifecycle state -- backend is single source of truth. */
export type ReviewLifecycleState =
  | "not_started"
  | "running"
  | "partial"
  | "failed"
  | "rejected_single"
  | "rejected_consensus"
  | "approved";

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
  review_lifecycle_state: ReviewLifecycleState;
  plan_status: PlanStatus;
  /** Structured error type from last plan generation failure (if any). */
  plan_error_type?: string;
  /** Actionable error message from last plan generation failure (if any). */
  plan_error_message?: string;
  /** Proposed sub-tasks from plan generation (populated via SSE when plan_status=ready). */
  proposed_tasks?: ProposedTask[];
  /** Backend generation_id for race-condition filtering of stale SSE events. */
  plan_generation_id?: string;
  /** Whether the current plan contains proposed sub-tasks (backend-computed). */
  has_proposed_tasks?: boolean;
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

/** Result of AI-generated structured plan (legacy sync response). */
export interface GeneratePlanResult {
  plan: string;
  steps: { step: string; files?: string[] }[];
  acceptance_criteria: string[];
  formatted: string;
}

/** A proposed sub-task from plan generation (not yet assigned an ID). */
export interface ProposedTask {
  title: string;
  description: string;
  files: string[];
  suggested_priority: string;
  suggested_complexity: string;
  dependencies: string[];
  acceptance_criteria: string[];
}

/** 202 Accepted response from async plan generation endpoint. */
export interface GeneratePlanAccepted {
  task_id: string;
  plan_status: string;
  generation_id: string;
}

/** Response from confirming generated tasks. */
export interface ConfirmGeneratedTasksResponse {
  parent_task_id: string;
  written_ids: string[];
  auto_paused: boolean;
  detail: string;
}

/** Result of creating a task. */
export interface CreateTaskResult {
  task_id: string;
  success: boolean;
  backup_path: string | null;
  synced: boolean;
  sync_result: SyncResult | null;
  error: string | null;
  sync_error: string | null;
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
  plan_snapshot: string | null;
  lifecycle_state: ReviewLifecycleState;
  timestamp: string;
  conversation_turns: Record<string, unknown>[];
  conversation_summary: Record<string, unknown>;
  questions: ReviewQuestion[];
}

/** Paginated review history response. */
export interface ReviewHistoryResponse {
  task_id: string;
  total: number;
  offset: number;
  limit: number;
  entries: ReviewHistoryEntry[];
}

// ------------------------------------------------------------------
// Stream event types (execution_stream / stream-log)
// ------------------------------------------------------------------

/** A single content block inside an assistant message. */
export interface StreamContentBlock {
  type: "text" | "thinking" | "tool_use" | "tool_result";
  text?: string;
  id?: string;           // tool_use_id for tool_use blocks
  name?: string;         // tool name for tool_use blocks
  input?: unknown;       // tool input for tool_use blocks
  tool_use_id?: string;  // matching tool_use_id for tool_result blocks
  content?: unknown;     // tool result content
}

/** Raw stream-json event from the backend. */
export interface StreamEvent {
  type: string;
  /** Varies by event type: assistant has content blocks, tool_use/tool_result are top-level. */
  [key: string]: unknown;
}

/** Normalized display item for ConversationView. */
export interface StreamDisplayItem {
  key: string;
  type: "text" | "thinking" | "tool_use" | "tool_result" | "result" | "error";
  timestamp: string;
  /** For text blocks: the markdown content. */
  text?: string;
  /** For thinking blocks: the reasoning content. */
  thinking?: string;
  /** For tool_use blocks: tool name. */
  toolName?: string;
  /** For tool_use blocks: tool input. */
  toolInput?: unknown;
  /** For tool_use blocks: unique ID to match results. */
  toolUseId?: string;
  /** For tool_result blocks: the result content. */
  resultContent?: string;
  /** For tool_result blocks: matching tool_use_id. */
  matchToolUseId?: string;
  /** For result blocks: final result text. */
  resultText?: string;
  /** For error blocks: error message. */
  errorMessage?: string;
}

/** Lightweight summary of stream events for a task (popover display). */
export interface StreamSummary {
  /** Most recent text snippet or tool name. */
  lastActivity: string;
  /** Number of tool_use events seen so far. */
  toolCallCount: number;
}

/** Response from /api/projects/{project_id}/start-all-planned endpoint. */
export interface StartAllPlannedResponse {
  project_id: string;
  started: number;
  skipped: number;
  skipped_details: { task_id: string; reason: string; message: string }[];
  detail: string;
}

/** Response from /api/tasks/{task_id}/stream-log endpoint. */
export interface StreamLogResponse {
  task_id: string;
  file: string;
  events: StreamEvent[];
}

/** Per-project cost summary from /api/dashboard/costs. */
export interface ProjectCostSummary {
  project_id: string;
  name: string;
  total_reviews: number;
  total_cost_usd: number;
  avg_cost: number;
}

/** Response from /api/dashboard/costs endpoint. */
export interface CostDashboardResponse {
  projects: ProjectCostSummary[];
  grand_total_cost_usd: number;
}
