/**
 * Shared rendering utilities for stream/conversation display.
 *
 * Extracted from ConversationView.tsx so ReviewPanel can reuse
 * tool-badge colors without duplicating the color map.
 */

/** Color scheme for a tool badge. */
export interface ToolColorScheme {
  bg: string;
  text: string;
  border: string;
}

/** Color map for tool names -- consistent colors per tool. */
export const TOOL_COLORS: Record<string, ToolColorScheme> = {
  Read: { bg: "bg-blue-900/50", text: "text-blue-300", border: "border-blue-700" },
  Write: { bg: "bg-green-900/50", text: "text-green-300", border: "border-green-700" },
  Edit: { bg: "bg-yellow-900/50", text: "text-yellow-300", border: "border-yellow-700" },
  Bash: { bg: "bg-orange-900/50", text: "text-orange-300", border: "border-orange-700" },
  Glob: { bg: "bg-purple-900/50", text: "text-purple-300", border: "border-purple-700" },
  Grep: { bg: "bg-pink-900/50", text: "text-pink-300", border: "border-pink-700" },
  WebFetch: { bg: "bg-cyan-900/50", text: "text-cyan-300", border: "border-cyan-700" },
  WebSearch: { bg: "bg-teal-900/50", text: "text-teal-300", border: "border-teal-700" },
  Agent: { bg: "bg-indigo-900/50", text: "text-indigo-300", border: "border-indigo-700" },
};

export const DEFAULT_TOOL_COLOR: ToolColorScheme = {
  bg: "bg-gray-800",
  text: "text-gray-300",
  border: "border-gray-600",
};

/** Get the color scheme for a given tool name. */
export function getToolColor(name?: string): ToolColorScheme {
  if (!name) return DEFAULT_TOOL_COLOR;
  return TOOL_COLORS[name] ?? DEFAULT_TOOL_COLOR;
}
