/**
 * SwimLane -- renders one KanbanBoard per project with SwimLaneHeader.
 * Each swim lane has its own DnD context (via KanbanBoard) so
 * drag-drop is scoped per project -- no cross-project dragging.
 */

import type { Project, Task, TaskStatus, StreamSummary } from "../types";
import KanbanBoard from "./KanbanBoard";
import SwimLaneHeader from "./SwimLaneHeader";

interface SwimLaneProps {
  project: Project;
  tasks: Task[];
  loading: boolean;
  onMoveTask: (taskId: string, newStatus: TaskStatus, opts?: { reason?: string; force_decompose_bypass?: boolean }) => void;
  onSelectTask?: (task: Task) => void;
  /** Whether this is the only swim lane (takes full height). */
  solo: boolean;
  syncing: boolean;
  onSync: () => void;
  onNewTask: () => void;
  onTaskCreated: (synced: boolean) => void;
  onError: (msg: string) => void;
  onPauseToggle?: (paused: boolean) => void;
  /** Called when inline creator Tab triggers enrich-expand into NewTaskModal. */
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
  /** Called when Start All Planned successfully starts tasks. */
  onStarted?: (count: number) => void;
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
  onTaskCreated,
  onError,
  onPauseToggle,
  onEnrichExpand,
  onTaskDeleted,
  onSendToReview,
  onEditTask,
  onTaskUpdated,
  streamSummaries,
  onStarted,
}: SwimLaneProps) {
  return (
    <div
      className={`flex flex-col min-h-0 ${solo ? "flex-1" : ""}`}
      style={solo ? undefined : { height: "320px" }}
    >
      {/* Project header bar with actions */}
      <SwimLaneHeader
        project={project}
        tasks={tasks}
        taskCount={tasks.length}
        syncing={syncing}
        onSync={onSync}
        onNewTask={onNewTask}
        onError={onError}
        onPauseToggle={onPauseToggle}
        onStarted={onStarted}
      />

      {/* Kanban board -- has its own DndContext */}
      <div className="flex-1 min-h-0 p-2">
        <KanbanBoard
          tasks={tasks}
          loading={loading}
          onMoveTask={onMoveTask}
          onSelectTask={onSelectTask}
          projectId={project.id}
          onTaskCreated={onTaskCreated}
          onError={onError}
          onEnrichExpand={onEnrichExpand}
          onTaskDeleted={onTaskDeleted}
          onSendToReview={onSendToReview}
          onEditTask={onEditTask}
          onTaskUpdated={onTaskUpdated}
          streamSummaries={streamSummaries}
        />
      </div>
    </div>
  );
}
