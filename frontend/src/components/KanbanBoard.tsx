/**
 * KanbanBoard -- 5-column board: BACKLOG, REVIEW, QUEUED, RUNNING, DONE.
 * Groups tasks by column using STATUS_TO_COLUMN mapping.
 */

import type { Task, KanbanColumn } from "../types";
import { KANBAN_COLUMNS, STATUS_TO_COLUMN } from "../types";
import TaskCard from "./TaskCard";

interface KanbanBoardProps {
  tasks: Task[];
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

export default function KanbanBoard({ tasks }: KanbanBoardProps) {
  const columns = groupByColumn(tasks);

  return (
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
              {columns[col].length}
            </span>
          </div>

          {/* Cards */}
          <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {columns[col].length === 0 ? (
              <p className="text-xs text-gray-400 text-center py-4">
                No tasks
              </p>
            ) : (
              columns[col].map((task) => (
                <TaskCard key={task.id} task={task} />
              ))
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
