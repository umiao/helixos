/**
 * SwimLane -- renders one KanbanBoard per project with SwimLaneHeader.
 * Each swim lane has its own DnD context (via KanbanBoard) so
 * drag-drop is scoped per project -- no cross-project dragging.
 */

import type { Project, Task, TaskStatus } from "../types";
import KanbanBoard from "./KanbanBoard";
import SwimLaneHeader from "./SwimLaneHeader";

interface SwimLaneProps {
  project: Project;
  tasks: Task[];
  loading: boolean;
  onMoveTask: (taskId: string, newStatus: TaskStatus) => void;
  onSelectTask?: (task: Task) => void;
  /** Whether this is the only swim lane (takes full height). */
  solo: boolean;
  syncing: boolean;
  onSync: () => void;
  onNewTask: () => void;
  onError: (msg: string) => void;
}

export default function SwimLane({
  project,
  tasks,
  loading,
  onMoveTask,
  onSelectTask,
  solo,
  syncing,
  onSync,
  onNewTask,
  onError,
}: SwimLaneProps) {
  return (
    <div
      className={`flex flex-col min-h-0 ${solo ? "flex-1" : ""}`}
      style={solo ? undefined : { height: "320px" }}
    >
      {/* Project header bar with actions */}
      <SwimLaneHeader
        project={project}
        taskCount={tasks.length}
        syncing={syncing}
        onSync={onSync}
        onNewTask={onNewTask}
        onError={onError}
      />

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
