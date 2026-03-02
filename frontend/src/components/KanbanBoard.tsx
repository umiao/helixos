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
import { useCallback, useState } from "react";
import type { Task, KanbanColumn, TaskStatus } from "../types";
import { KANBAN_COLUMNS, STATUS_TO_COLUMN, COLUMN_TO_STATUS } from "../types";
import TaskCard from "./TaskCard";
import TaskContextMenu from "./TaskContextMenu";
import InlineTaskCreator from "./InlineTaskCreator";
import SkeletonCard from "./SkeletonCard";

interface KanbanBoardProps {
  tasks: Task[];
  loading: boolean;
  onMoveTask: (taskId: string, newStatus: TaskStatus) => void;
  onSelectTask?: (task: Task) => void;
  projectId?: string;
  onTaskCreated?: () => void;
  onError?: (msg: string) => void;
}

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

export default function KanbanBoard({
  tasks,
  loading,
  onMoveTask,
  onSelectTask,
  projectId,
  onTaskCreated,
  onError,
}: KanbanBoardProps) {
  const columns = groupByColumn(tasks);
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [contextMenu, setContextMenu] = useState<{
    task: Task;
    position: { x: number; y: number };
  } | null>(null);

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
    onMoveTask(taskId, newStatus);
  }

  return (
    <DndContext
      sensors={sensors}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
    >
      <div className="grid grid-cols-5 gap-4 h-full min-h-0">
        {KANBAN_COLUMNS.map((col) => (
          <div
            key={col}
            className={`flex flex-col rounded-lg bg-gray-50 border-t-4 ${COLUMN_STYLES[col]} min-h-0`}
          >
            {/* Column header */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200">
              <h2 className="text-xs font-bold uppercase tracking-wide text-gray-600">
                {col}
              </h2>
              <span className="rounded-full bg-gray-200 px-2 py-0.5 text-xs font-semibold text-gray-700">
                {loading ? "-" : columns[col].length}
              </span>
            </div>

            {/* Droppable area with cards */}
            <DroppableColumn column={col}>
              {loading ? (
                <>
                  <SkeletonCard />
                  <SkeletonCard />
                </>
              ) : columns[col].length === 0 && col !== "BACKLOG" ? (
                <p className="text-xs text-gray-400 text-center py-4">
                  No tasks
                </p>
              ) : (
                columns[col].map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    onClick={
                      onSelectTask
                        ? () => onSelectTask(task)
                        : undefined
                    }
                    onContextMenu={handleContextMenu}
                  />
                ))
              )}
              {/* Inline task creator at bottom of Backlog column */}
              {col === "BACKLOG" && !loading && projectId && onTaskCreated && onError && (
                <InlineTaskCreator
                  projectId={projectId}
                  onCreated={onTaskCreated}
                  onError={onError}
                />
              )}
            </DroppableColumn>
          </div>
        ))}
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
        />
      )}
    </DndContext>
  );
}
