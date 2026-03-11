/**
 * PlanComponents -- shared plan display components used by both
 * PlanReviewPanel (Plan tab) and ReviewPanel (Review tab).
 *
 * Extracted from PlanReviewPanel.tsx to avoid duplication.
 */

import { useState } from "react";
import type { ProposedTask } from "../types";
import MarkdownRenderer from "./MarkdownRenderer";

/** Parsed structure of plan_json from backend. */
export interface ParsedPlan {
  plan: string;
  steps: { step: string; files?: string[] }[];
  acceptance_criteria: string[];
  proposed_tasks: ProposedTask[];
}

/** Priority badge color mapping. */
export function priorityColor(p: string): string {
  switch (p) {
    case "P0": return "bg-red-100 text-red-700";
    case "P1": return "bg-orange-100 text-orange-700";
    case "P2": return "bg-yellow-100 text-yellow-700";
    case "P3": return "bg-blue-100 text-blue-700";
    default: return "bg-gray-100 text-gray-600";
  }
}

/** Complexity badge color mapping. */
export function complexityColor(c: string): string {
  switch (c) {
    case "S": return "bg-green-100 text-green-700";
    case "M": return "bg-blue-100 text-blue-700";
    case "L": return "bg-purple-100 text-purple-700";
    default: return "bg-gray-100 text-gray-600";
  }
}

export function ProposedTaskCard({ task, index }: { task: ProposedTask; index: number }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-gray-200 rounded-lg p-3 bg-white">
      <div className="flex items-start gap-2">
        <span className="text-xs font-mono text-gray-400 mt-0.5">#{index + 1}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm text-gray-900">{task.title}</span>
            <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${priorityColor(task.suggested_priority)}`}>
              {task.suggested_priority}
            </span>
            <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${complexityColor(task.suggested_complexity)}`}>
              {task.suggested_complexity}
            </span>
          </div>
          <p className="text-xs text-gray-600 mt-1 line-clamp-2">{task.description}</p>

          {/* Expandable details */}
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-[10px] text-indigo-600 hover:text-indigo-800 mt-1"
          >
            {expanded ? "Hide details" : "Show details"}
          </button>

          {expanded && (
            <div className="mt-2 space-y-2">
              {task.acceptance_criteria.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold text-gray-500 uppercase">Acceptance Criteria</span>
                  <ul className="mt-0.5 space-y-0.5">
                    {task.acceptance_criteria.map((ac, i) => (
                      <li key={i} className="text-xs text-gray-700 flex gap-1">
                        <span className="text-gray-400">{i + 1}.</span>
                        <span>{ac}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {task.files.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold text-gray-500 uppercase">Files</span>
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {task.files.map((f, i) => (
                      <span key={i} className="text-[10px] font-mono bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {task.dependencies.length > 0 && (
                <div>
                  <span className="text-[10px] font-semibold text-gray-500 uppercase">Dependencies</span>
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {task.dependencies.map((d, i) => (
                      <span key={i} className="text-[10px] font-mono bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">
                        {d}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Renders a parsed plan_json as structured UI with steps, ACs, and proposed tasks. */
export function StructuredPlanView({ plan }: { plan: ParsedPlan }) {
  return (
    <div className="space-y-3">
      {/* Plan summary */}
      {plan.plan && plan.plan.trim() && (
        <div>
          <h4 className="text-[10px] font-semibold text-gray-500 uppercase mb-1">Summary</h4>
          <MarkdownRenderer
            content={plan.plan}
            maxHeight="12rem"
          />
        </div>
      )}

      {/* Implementation steps */}
      {plan.steps.length > 0 && (
        <div>
          <h4 className="text-[10px] font-semibold text-gray-500 uppercase mb-1">
            Implementation Steps ({plan.steps.length})
          </h4>
          <ol className="space-y-1.5">
            {plan.steps.map((s, i) => (
              <li key={i} className="flex gap-2 text-xs text-gray-700">
                <span className="text-gray-400 font-mono shrink-0">{i + 1}.</span>
                <div className="min-w-0">
                  <span>{s.step}</span>
                  {s.files && s.files.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-0.5">
                      {s.files.map((f, fi) => (
                        <span key={fi} className="text-[10px] font-mono bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded">
                          {f}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Acceptance criteria */}
      {plan.acceptance_criteria.length > 0 && (
        <div>
          <h4 className="text-[10px] font-semibold text-gray-500 uppercase mb-1">
            Acceptance Criteria ({plan.acceptance_criteria.length})
          </h4>
          <ol className="space-y-0.5">
            {plan.acceptance_criteria.map((ac, i) => (
              <li key={i} className="text-xs text-gray-700 flex gap-1">
                <span className="text-gray-400 font-mono shrink-0">{i + 1}.</span>
                <span>{ac}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Proposed tasks */}
      {plan.proposed_tasks.length > 0 && (
        <div>
          <h4 className="text-[10px] font-semibold text-gray-500 uppercase mb-1">
            Proposed Tasks ({plan.proposed_tasks.length})
          </h4>
          <div className="space-y-2">
            {plan.proposed_tasks.map((pt, i) => (
              <ProposedTaskCard key={i} task={pt} index={i} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
