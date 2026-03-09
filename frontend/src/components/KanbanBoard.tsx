/**
 * KanbanBoard -- 5-column board with drag-drop via @dnd-kit/core.
 * Columns: BACKLOG, REVIEW, QUEUED, RUNNING, DONE.
 * On drop, calls onMoveTask(taskId, targetStatus).
 */

import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { useDroppable } from "@dnd-kit/core";
import { useCallback, useMemo, useState } from "react";
import type { Task, KanbanColumn, TaskStatus, StreamSummary } from "../types";
import { KANBAN_COLUMNS, STATUS_TO_COLUMN, COLUMN_TO_STATUS } from "../types";

/** Sort orders available for the DONE column. */
type DoneSortOrder = "newest" | "oldest" | "by_task_id";

const DONE_SORT_KEY = "helixos_done_sort";
const DONE_FILTER_KEY = "helixos_done_filter";

/** Sub-statuses that map to the DONE column. */
const DONE_SUB_STATUSES: TaskStatus[] = ["done", "failed", "blocked"];

function loadDoneSort(): DoneSortOrder {
  try {
    const v = localStorage.getItem(DONE_SORT_KEY);
    if (v === "newest" || v === "oldest" || v === "by_task_id") return v;
  } catch { /* ignore */ }
  return "newest";
}

function saveDoneSort(order: DoneSortOrder): void {
  try { localStorage.setItem(DONE_SORT_KEY, order); } catch { /* ignore */ }
}

type SubStatusFilter = Record<TaskStatus, boolean>;

function loadDoneFilter(): SubStatusFilter {
  try {
    const raw = localStorage.getItem(DONE_FILTER_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<SubStatusFilter>;
      return {
        done: parsed.done !== false,
        failed: parsed.failed !== false,
        blocked: parsed.blocked !== false,
      } as SubStatusFilter;
    }
  } catch { /* ignore */ }
  return { done: true, failed: true, blocked: true } as SubStatusFilter;
}

function saveDoneFilter(filter: SubStatusFilter): void {
  try { localStorage.setItem(DONE_FILTER_KEY, JSON.stringify(filter)); } catch { /* ignore */ }
}

function sortDoneTasks(tasks: Task[], order: DoneSortOrder): Task[] {
  const sorted = [...tasks];
  switch (order) {
    case "newest":
      sorted.sort((a, b) => {
        const ta = a.completed_at ?? a.updated_at;
        const tb = b.completed_at ?? b.updated_at;
        return tb.localeCompare(ta);
      });
      break;
    case "oldest":
      sorted.sort((a, b) => {
        const ta = a.completed_at ?? a.updated_at;
        const tb = b.completed_at ?? b.updated_at;
        return ta.localeCompare(tb);
      });
      break;
    case "by_task_id":
      sorted.sort((a, b) => a.local_task_id.localeCompare(b.local_task_id));
      break;
  }
  return sorted;
}

import TaskCard from "./TaskCard";
import TaskContextMenu from "./TaskContextMenu";
import InlineTaskCreator from "./InlineTaskCreator";
import SkeletonCard from "./SkeletonCard";
import BackwardDragModal from "./BackwardDragModal";
import DecomposeRequiredModal from "./DecomposeRequiredModal";

interface KanbanBoardProps {
  tasks: Task[];
  loading: boolean;
  onMoveTask: (taskId: string, newStatus: TaskStatus, opts?: { reason?: string; force_decompose_bypass?: boolean }) => void;
  onSelectTask?: (task: Task) => void;
  projectId?: string;
  onTaskCreated?: (synced: boolean) => void;
  onError?: (msg: string) => void;
  /** Called when inline creator Tab key triggers enrich-expand. */
  onEnrichExpand?: (title: string) => void;
  /** Called after a task is successfully deleted. */
  onTaskDeleted?: () => void;
  /** Called to open the review submit modal for a task. */
  onSendToReview?: (task: Task) => void;
  /** Called to open the edit modal for a task. */
  onEditTask?: (task: Task) => void;
  /** Called when a task is updated (e.g., plan generated from popover). */
  onTaskUpdated?: (task: Task) => void;
  /** Per-task stream summaries for popover live activity display. */
  streamSummaries?: Record<string, StreamSummary>;
}

/** Column index for detecting backward drags. */
const COLUMN_ORDER: Record<KanbanColumn, number> = {
  BACKLOG: 0,
  REVIEW: 1,
  QUEUED: 2,
  RUNNING: 3,
  DONE: 4,
};

const COLUMN_STYLES: Record<KanbanColumn, string> = {
  BACKLOG: "border-t-gray-400",
  REVIEW: "border-t-yellow-400",
  QUEUED: "border-t-blue-400",
  RUNNING: "border-t-indigo-500",
  DONE: "border-t-green-500",
};

function groupByColumn(tasks: Task[]): Record<KanbanColumn, Task[]> {
  const groups: Record<KanbanColumn, Task[]> = {
    BACKLOG: [],
    REVIEW: [],
    QUEUED: [],
    RUNNING: [],
    DONE: [],
  };
  for (const task of tasks) {
    const col = STATUS_TO_COLUMN[task.status];
    groups[col].push(task);
  }
  return groups;
}

function DroppableColumn({
  column,
  children,
}: {
  column: KanbanColumn;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: column });

  return (
    <div
      ref={setNodeRef}
      className={`flex-1 overflow-y-auto p-2 space-y-2 transition-colors rounded-b-lg ${
        isOver ? "bg-blue-50" : ""
      }`}
    >
      {children}
    </div>
  );
}

const SUB_STATUS_BADGE: Record<string, { label: string; active: string; inactive: string }> = {
  done: {
    label: "DONE",
    active: "bg-green-200 text-green-900",
    inactive: "bg-gray-100 text-gray-400 line-through",
  },
  failed: {
    label: "FAILED",
    active: "bg-red-200 text-red-900",
    inactive: "bg-gray-100 text-gray-400 line-through",
  },
  blocked: {
    label: "BLOCKED",
    active: "bg-red-100 text-red-800",
    inactive: "bg-gray-100 text-gray-400 line-through",
  },
};

const SORT_OPTIONS: { value: DoneSortOrder; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "by_task_id", label: "By task ID" },
];

function DoneColumnHeader({
  tasks,
  sortOrder,
  onSortChange,
  filter,
  onFilterToggle,
}: {
  tasks: Task[];
  sortOrder: DoneSortOrder;
  onSortChange: (order: DoneSortOrder) => void;
  filter: SubStatusFilter;
  onFilterToggle: (status: TaskStatus) => void;
}) {
  const counts = useMemo(() => {
    const c: Record<string, number> = { done: 0, failed: 0, blocked: 0 };
    for (const t of tasks) c[t.status] = (c[t.status] ?? 0) + 1;
    return c;
  }, [tasks]);

  // Count visible tasks (after filter)
  const visibleCount = useMemo(() => {
    return tasks.filter((t) => filter[t.status] !== false).length;
  }, [tasks, filter]);

  return (
    <div className="px-3 py-2 border-b border-gray-200 space-y-1.5">
      {/* Top row: title + visible count + sort dropdown */}
      <div className="flex items-center justify-between gap-1">
        <div className="flex items-center gap-1.5">
          <h2 className="text-xs font-bold uppercase tracking-wide text-gray-600">
            DONE
          </h2>
          <span className="rounded-full bg-gray-200 px-2 py-0.5 text-xs font-semibold text-gray-700">
            {visibleCount}
          </span>
        </div>
        <select
          value={sortOrder}
          onChange={(e) => onSortChange(e.target.value as DoneSortOrder)}
          className="text-xs bg-white border border-gray-300 rounded px-1 py-0.5 text-gray-600 focus:outline-none focus:ring-1 focus:ring-green-400"
          title="Sort order"
        >
          {SORT_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
      {/* Sub-status filter badges */}
      <div className="flex items-center gap-1">
        {DONE_SUB_STATUSES.map((status) => {
          const cfg = SUB_STATUS_BADGE[status];
          const isActive = filter[status] !== false;
          const count = counts[status] ?? 0;
          return (
            <button
              key={status}
              onClick={() => onFilterToggle(status)}
              className={`rounded-full px-1.5 py-0 text-[10px] font-semibold cursor-pointer transition-colors ${
                isActive ? cfg.active : cfg.inactive
              }`}
              title={`${isActive ? "Hide" : "Show"} ${cfg.label} tasks`}
            >
              {cfg.label} {count}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function KanbanBoard({
  tasks,
  loading,
  onMoveTask,
  onSelectTask,
  projectId,
  onTaskCreated,
  onError,
  onEnrichExpand,
  onTaskDeleted,
  onSendToReview,
  onEditTask,
  onTaskUpdated,
  streamSummaries,
}: KanbanBoardProps) {
  const columns = groupByColumn(tasks);
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [contextMenu, setContextMenu] = useState<{
    task: Task;
    position: { x: number; y: number };
  } | null>(null);

  // Backward-drag modal state
  const [backwardDrag, setBackwardDrag] = useState<{
    taskId: string;
    taskTitle: string;
    taskLocalId: string;
    sourceColumn: KanbanColumn;
    targetColumn: KanbanColumn;
    newStatus: TaskStatus;
  } | null>(null);

  // Decompose-required modal state (forward drag to RUNNING with undecomposed plan)
  const [decompDrag, setDecompDrag] = useState<{
    task: Task;
    newStatus: TaskStatus;
  } | null>(null);

  // DONE column sort + sub-status filter (persisted in localStorage)
  const [doneSortOrder, setDoneSortOrder] = useState<DoneSortOrder>(loadDoneSort);
  const [doneFilter, setDoneFilter] = useState<SubStatusFilter>(loadDoneFilter);

  const handleDoneSortChange = useCallback((order: DoneSortOrder) => {
    setDoneSortOrder(order);
    saveDoneSort(order);
  }, []);

  const handleDoneFilterToggle = useCallback((status: TaskStatus) => {
    setDoneFilter((prev) => {
      const next = { ...prev, [status]: !prev[status] };
      saveDoneFilter(next);
      return next;
    });
  }, []);

  // Compute sorted + filtered DONE column tasks
  const doneTasks = useMemo(() => {
    const filtered = columns.DONE.filter((t) => doneFilter[t.status] !== false);
    return sortDoneTasks(filtered, doneSortOrder);
  }, [columns.DONE, doneFilter, doneSortOrder]);

  const handleContextMenu = useCallback(
    (task: Task, position: { x: number; y: number }) => {
      setContextMenu({ task, position });
    },
    [],
  );

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 8 },
    }),
  );

  function handleDragStart(event: DragStartEvent) {
    const task = (event.active.data.current as { task: Task } | undefined)
      ?.task;
    setActiveTask(task ?? null);
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveTask(null);
    const { active, over } = event;
    if (!over) return;

    const taskId = active.id as string;
    const targetColumn = over.id as KanbanColumn;
    const task = tasks.find((t) => t.id === taskId);
    if (!task) return;

    // Don't transition if dropped on same column
    const currentColumn = STATUS_TO_COLUMN[task.status];
    if (currentColumn === targetColumn) return;

    const newStatus = COLUMN_TO_STATUS[targetColumn];

    // Detect backward drag -- show styled confirmation modal
    const isBackward = COLUMN_ORDER[targetColumn] < COLUMN_ORDER[currentColumn];
    if (isBackward) {
      setBackwardDrag({
        taskId,
        taskTitle: task.title,
        taskLocalId: task.local_task_id,
        sourceColumn: currentColumn,
        targetColumn,
        newStatus,
      });
    } else if (
      targetColumn === "RUNNING" &&
      task.plan_status === "ready" &&
      task.proposed_tasks &&
      task.proposed_tasks.length > 0
    ) {
      // Decomposition gate: show modal instead of moving directly
      setDecompDrag({ task, newStatus });
    } else {
      onMoveTask(taskId, newStatus);
    }
  }

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <div className="grid grid-cols-5 gap-4 h-full min-h-0">
        {KANBAN_COLUMNS.map((col) => {
          const isDone = col === "DONE";
          const colTasks = isDone ? doneTasks : columns[col];

          return (
            <div
              key={col}
              className={`flex flex-col rounded-lg bg-gray-50 border-t-4 ${COLUMN_STYLES[col]} min-h-0`}
            >
              {/* Column header */}
              {isDone ? (
                <DoneColumnHeader
                  tasks={columns.DONE}
                  sortOrder={doneSortOrder}
                  onSortChange={handleDoneSortChange}
                  filter={doneFilter}
                  onFilterToggle={handleDoneFilterToggle}
                />
              ) : (
                <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200">
                  <div className="flex items-center gap-1.5">
                    <h2 className="text-xs font-bold uppercase tracking-wide text-gray-600">
                      {col}
                    </h2>
                    <span className="rounded-full bg-gray-200 px-2 py-0.5 text-xs font-semibold text-gray-700">
                      {loading ? "-" : columns[col].length}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {/* Planless task count for BACKLOG / REVIEW */}
                    {(col === "BACKLOG" || col === "REVIEW") && !loading && (() => {
                      const planless = columns[col].filter(
                        (t) => t.plan_status === "none",
                      ).length;
                      return planless > 0 ? (
                        <span
                          className="rounded-full bg-amber-100 text-amber-700 px-2 py-0.5 text-[10px] font-semibold"
                          title={`${planless} task${planless > 1 ? "s" : ""} without a plan`}
                        >
                          {planless} no plan
                        </span>
                      ) : null;
                    })()}
                    {/* Needs-human attention count for REVIEW column */}
                    {col === "REVIEW" && !loading && (() => {
                      const needsHuman = columns.REVIEW.filter(
                        (t) => t.status === "review_needs_human",
                      ).length;
                      return needsHuman > 0 ? (
                        <span
                          className="rounded-full bg-orange-100 text-orange-800 px-2 py-0.5 text-[10px] font-semibold animate-pulse"
                          title={`${needsHuman} task${needsHuman > 1 ? "s" : ""} need${needsHuman === 1 ? "s" : ""} human decision`}
                        >
                          {needsHuman} needs human
                        </span>
                      ) : null;
                    })()}
                  </div>
                </div>
              )}

              {/* Droppable area with cards */}
              <DroppableColumn column={col}>
                {loading ? (
                  <>
                    <SkeletonCard />
                    <SkeletonCard />
                  </>
                ) : colTasks.length === 0 && col !== "BACKLOG" ? (
                  <p className="text-xs text-gray-400 text-center py-4">
                    No tasks
                  </p>
                ) : (
                  colTasks.map((task) => (
                    <TaskCard
                      key={task.id}
                      task={task}
                      onClick={
                        onSelectTask
                          ? () => onSelectTask(task)
                          : undefined
                      }
                      onContextMenu={handleContextMenu}
                      onTaskUpdated={onTaskUpdated}
                      streamSummary={streamSummaries?.[task.id]}
                    />
                  ))
                )}
                {/* Inline task creator at bottom of Backlog column */}
                {col === "BACKLOG" && !loading && projectId && onTaskCreated && onError && (
                  <InlineTaskCreator
                    projectId={projectId}
                    onCreated={onTaskCreated}
                    onError={onError}
                    onEnrichExpand={onEnrichExpand}
                  />
                )}
              </DroppableColumn>
            </div>
          );
        })}
      </div>

      {/* Drag overlay -- follows pointer */}
      <DragOverlay>
        {activeTask ? (
          <div className="opacity-90 rotate-2 scale-105">
            <TaskCard task={activeTask} />
          </div>
        ) : null}
      </DragOverlay>

      {/* Right-click context menu */}
      {contextMenu && (
        <TaskContextMenu
          task={contextMenu.task}
          position={contextMenu.position}
          onClose={closeContextMenu}
          onMoveTask={onMoveTask}
          onSelectTask={onSelectTask}
          onTaskDeleted={onTaskDeleted}
          onError={onError}
          onSendToReview={onSendToReview}
          onEditTask={onEditTask}
          onTaskCancelled={onTaskDeleted}
        />
      )}

      {/* Backward-drag confirmation modal */}
      {backwardDrag && (
        <BackwardDragModal
          taskTitle={backwardDrag.taskTitle}
          taskId={backwardDrag.taskLocalId}
          sourceColumn={backwardDrag.sourceColumn}
          targetColumn={backwardDrag.targetColumn}
          onConfirm={(reason) => {
            onMoveTask(backwardDrag.taskId, backwardDrag.newStatus, { reason });
            setBackwardDrag(null);
          }}
          onCancel={() => setBackwardDrag(null)}
        />
      )}

      {/* Decomposition-required modal */}
      {decompDrag && (
        <DecomposeRequiredModal
          taskTitle={decompDrag.task.title}
          taskId={decompDrag.task.local_task_id}
          proposedTaskCount={decompDrag.task.proposed_tasks?.length ?? 0}
          onGoToPlanReview={() => {
            if (onSelectTask) onSelectTask(decompDrag.task);
            setDecompDrag(null);
          }}
          onExecuteAnyway={() => {
            onMoveTask(decompDrag.task.id, decompDrag.newStatus, {
              force_decompose_bypass: true,
            });
            setDecompDrag(null);
          }}
          onCancel={() => setDecompDrag(null)}
        />
      )}
    </DndContext>
  );
}
