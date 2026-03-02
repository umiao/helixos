/**
 * SwimLane -- renders one KanbanBoard per project with a project header.
 * Each swim lane has its own DnD context (via KanbanBoard) so
 * drag-drop is scoped per project -- no cross-project dragging.
 */

import type { Project, Task, TaskStatus } from "../types";
import KanbanBoard from "./KanbanBoard";

interface SwimLaneProps {
  project: Project;
  tasks: Task[];
  loading: boolean;
  onMoveTask: (taskId: string, newStatus: TaskStatus) => void;
  onSelectTask?: (task: Task) => void;
  /** Whether this is the only swim lane (takes full height). */
  solo: boolean;
}

export default function SwimLane({
  project,
  tasks,
  loading,
  onMoveTask,
  onSelectTask,
  solo,
}: SwimLaneProps) {
  const taskCount = tasks.length;

  return (
    <div
      className={`flex flex-col min-h-0 ${solo ? "flex-1" : ""}`}
      style={solo ? undefined : { height: "320px" }}
    >
      {/* Project header bar */}
      <div className="flex items-center gap-3 px-4 py-1.5 bg-gray-200 border-b border-gray-300 rounded-t-md flex-shrink-0">
        <h2 className="text-sm font-bold text-gray-800 tracking-tight">
          {project.name}
        </h2>
        <span className="text-xs text-gray-500 font-mono">{project.id}</span>
        <span className="ml-auto text-xs text-gray-500">
          {taskCount} {taskCount === 1 ? "task" : "tasks"}
        </span>
      </div>

      {/* Kanban board -- has its own DndContext */}
      <div className="flex-1 min-h-0 p-2">
        <KanbanBoard
          tasks={tasks}
          loading={loading}
          onMoveTask={onMoveTask}
          onSelectTask={onSelectTask}
        />
      </div>
    </div>
  );
}
