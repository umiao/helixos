/**
 * ConversationView -- structured conversation display for task execution.
 *
 * Renders stream-json events as a conversation: assistant text in dark bubbles
 * with markdown, tool calls as collapsible color-coded badges, tool results
 * indented below matching tool call, and a result banner at the end.
 *
 * Data flow:
 * - On mount: fetchStreamLog(taskId) loads persisted JSONL events
 * - Live SSE execution_stream events are passed via props (already stored in App.tsx)
 * - Merge: live events with timestamp > latest persisted event
 */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypePrism from "rehype-prism-plus";
import type { StreamDisplayItem } from "../types";
import { fetchStreamLog } from "../api";
import { getToolColor } from "../utils/streamUtils";

/** Max display items to keep (prevents DOM overload). */
const MAX_ITEMS = 2000;

/** Code blocks larger than this (in bytes) skip Prism highlighting. */
const CODE_SIZE_LIMIT = 5 * 1024;

/** Generate a compact summary for a tool_use block.
 *  e.g., "Read src/foo.py", "Bash: npm test", "Edit config.ts" */
function toolSummary(
  toolName: string | undefined,
  toolInput: unknown,
  resultContent?: string,
): string {
  const name = toolName ?? "unknown";
  let detail = "";

  if (toolInput && typeof toolInput === "object") {
    const inp = toolInput as Record<string, unknown>;
    // Try common input field patterns
    if (typeof inp.file_path === "string") {
      detail = shortenPath(inp.file_path);
    } else if (typeof inp.command === "string") {
      const cmd = inp.command as string;
      detail = cmd.length > 60 ? cmd.slice(0, 57) + "..." : cmd;
    } else if (typeof inp.pattern === "string") {
      detail = inp.pattern as string;
    } else if (typeof inp.query === "string") {
      const q = inp.query as string;
      detail = q.length > 50 ? q.slice(0, 47) + "..." : q;
    } else if (typeof inp.url === "string") {
      detail = inp.url as string;
    }
  } else if (typeof toolInput === "string") {
    try {
      const parsed = JSON.parse(toolInput);
      if (parsed && typeof parsed === "object") {
        return toolSummary(toolName, parsed, resultContent);
      }
    } catch {
      // Not JSON, use as-is
      if (toolInput.length > 60) {
        detail = toolInput.slice(0, 57) + "...";
      } else {
        detail = toolInput;
      }
    }
  }

  // Append line count from result if available
  let lineInfo = "";
  if (resultContent) {
    const lineCount = resultContent.split("\n").length;
    if (lineCount > 1) {
      lineInfo = ` (${lineCount} lines)`;
    }
  }

  if (detail) {
    return `${name}: ${detail}${lineInfo}`;
  }
  return lineInfo ? `${name}${lineInfo}` : name;
}

/** Shorten a file path to just the last 2-3 segments. */
function shortenPath(p: string): string {
  const parts = p.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length <= 3) return parts.join("/");
  return ".../" + parts.slice(-2).join("/");
}

/** Strip language tag from fenced code blocks larger than CODE_SIZE_LIMIT
 *  so rehype-prism-plus skips them (renders as plain <pre>). */
function stripLargeCodeBlockLanguages(md: string): string {
  return md.replace(
    /^(```)\w+\n([\s\S]*?)^```/gm,
    (_match, fence: string, body: string) =>
      body.length > CODE_SIZE_LIMIT ? `${fence}\n${body}\`\`\`` : _match,
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const REMARK_PLUGINS: any[] = [remarkGfm];
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const REHYPE_PLUGINS: any[] = [[rehypePrism, { ignoreMissing: true }]];

interface ConversationViewProps {
  /** Task ID to display conversation for. */
  taskId: string;
  /** Status of the task (for elapsed timer). */
  taskStatus?: string;
  /** ISO timestamp when execution started. */
  executionStartedAt?: string | null;
  /** Live stream events from SSE (already normalized in App.tsx). */
  liveItems: StreamDisplayItem[];
  /** Callback to toggle back to plain log view. */
  onToggleView: () => void;
}

/** Parse raw stream events into normalized display items. */
export function normalizeStreamEvents(
  events: Record<string, unknown>[],
  keyPrefix: string,
  baseTimestamp?: string,
): StreamDisplayItem[] {
  const items: StreamDisplayItem[] = [];
  let counter = 0;
  const ts = baseTimestamp ?? new Date().toISOString();

  for (const event of events) {
    const eventType = (event.type as string) ?? "";
    counter++;
    const key = `${keyPrefix}-${counter}`;

    if (eventType === "assistant") {
      // Extract text from content blocks
      const content = event.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (typeof block === "object" && block !== null) {
            const b = block as Record<string, unknown>;
            if (b.type === "text" && typeof b.text === "string" && b.text.trim()) {
              items.push({
                key: `${key}-text`,
                type: "text",
                timestamp: ts,
                text: b.text,
              });
            }
          }
        }
      } else if (typeof content === "string" && content.trim()) {
        items.push({ key, type: "text", timestamp: ts, text: content });
      }
    } else if (eventType === "content_block_delta") {
      const delta = event.delta as Record<string, unknown> | undefined;
      const text = delta?.text;
      if (typeof text === "string" && text.trim()) {
        items.push({ key, type: "text", timestamp: ts, text });
      }
    } else if (eventType === "tool_use") {
      const toolInput = event.input;
      let inputStr = "";
      if (toolInput && typeof toolInput === "object") {
        try {
          inputStr = JSON.stringify(toolInput, null, 2);
        } catch {
          inputStr = String(toolInput);
        }
      }
      items.push({
        key,
        type: "tool_use",
        timestamp: ts,
        toolName: (event.name as string) ?? "unknown",
        toolInput: inputStr,
        toolUseId: (event.id as string) ?? undefined,
      });
    } else if (eventType === "tool_result") {
      const content = event.content;
      let resultStr = "";
      if (typeof content === "string") {
        resultStr = content;
      } else if (content != null) {
        try {
          resultStr = JSON.stringify(content, null, 2);
        } catch {
          resultStr = String(content);
        }
      }
      items.push({
        key,
        type: "tool_result",
        timestamp: ts,
        resultContent: resultStr,
        matchToolUseId: (event.tool_use_id as string) ?? undefined,
      });
    } else if (eventType === "result") {
      const resultText = typeof event.result === "string"
        ? event.result
        : (event.subtype === "success" ? "Completed successfully" : "Execution finished");
      items.push({ key, type: "result", timestamp: ts, resultText });
    }
  }

  return items;
}

export default function ConversationView({
  taskId,
  taskStatus,
  executionStartedAt,
  liveItems,
  onToggleView,
}: ConversationViewProps) {
  const [persistedItems, setPersistedItems] = useState<StreamDisplayItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const prevScrollTop = useRef(0);
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());

  // Elapsed timer
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (taskStatus !== "running" || !executionStartedAt) {
      setElapsedSeconds(0);
      return;
    }
    const startMs = new Date(executionStartedAt).getTime();
    const tick = () => setElapsedSeconds(Math.max(0, Math.floor((Date.now() - startMs) / 1000)));
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [taskStatus, executionStartedAt]);

  const formatElapsed = (totalSec: number) => {
    const mins = Math.floor(totalSec / 60);
    const secs = totalSec % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  // Load persisted stream log on mount / taskId change
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setPersistedItems([]);
    setExpandedTools(new Set());

    fetchStreamLog(taskId)
      .then((resp) => {
        if (cancelled) return;
        const items = normalizeStreamEvents(resp.events, "db");
        setPersistedItems(items);
      })
      .catch(() => {
        // Non-critical - will show live events only
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [taskId]);

  // Merge persisted + live items, dedup by checking timestamps
  const mergedItems = (() => {
    if (liveItems.length === 0) return persistedItems.slice(-MAX_ITEMS);
    if (persistedItems.length === 0) return liveItems.slice(-MAX_ITEMS);
    // Use all persisted, then append live items
    // Live items are already filtered by App.tsx to only include items for this task
    const combined = [...persistedItems, ...liveItems];
    return combined.slice(-MAX_ITEMS);
  })();

  // Count tool calls
  const toolCallCount = mergedItems.filter((i) => i.type === "tool_use").length;

  // Auto-scroll
  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [mergedItems.length, autoScroll]);

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
    if (el.scrollTop < prevScrollTop.current && !atBottom) {
      setAutoScroll(false);
    } else if (atBottom) {
      setAutoScroll(true);
    }
    prevScrollTop.current = el.scrollTop;
  }, []);

  const toggleExpand = (key: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  // Find matching tool result for a tool_use item
  const findToolResult = (toolUseId: string | undefined): StreamDisplayItem | null => {
    if (!toolUseId) return null;
    return mergedItems.find(
      (i) => i.type === "tool_result" && i.matchToolUseId === toolUseId,
    ) ?? null;
  };

  // Track which tool_result keys we render inline (under their tool_use)
  const inlineResultKeys = new Set<string>();
  for (const item of mergedItems) {
    if (item.type === "tool_use" && item.toolUseId) {
      const result = findToolResult(item.toolUseId);
      if (result) inlineResultKeys.add(result.key);
    }
  }

  return (
    <div className="flex flex-col h-full bg-gray-900 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-bold uppercase tracking-wide text-gray-300">
            Conversation
          </h3>
          <span className="text-xs text-indigo-400 font-mono">{taskId}</span>
          {taskStatus === "running" && elapsedSeconds > 0 && (
            <span className="text-xs font-mono text-yellow-400 bg-yellow-900 px-1.5 py-0.5 rounded">
              {formatElapsed(elapsedSeconds)} elapsed
            </span>
          )}
          {toolCallCount > 0 && (
            <span className="text-xs text-gray-400">
              {toolCallCount} tool call{toolCallCount !== 1 ? "s" : ""}
            </span>
          )}
          {loading && (
            <span className="text-xs text-gray-500">Loading...</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onToggleView}
            className="rounded bg-gray-700 px-2 py-0.5 text-xs text-gray-300 hover:bg-gray-600 border border-gray-600"
          >
            Plain Log
          </button>
          {!autoScroll && (
            <button
              onClick={() => setAutoScroll(true)}
              className="rounded bg-indigo-600 px-2 py-0.5 text-xs text-white hover:bg-indigo-700"
            >
              Resume scroll
            </button>
          )}
        </div>
      </div>

      {/* Conversation content */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-3 space-y-2"
      >
        {mergedItems.length === 0 ? (
          <p className="text-gray-500 text-center py-4 text-sm">
            {loading ? "Loading conversation..." : "No conversation events yet"}
          </p>
        ) : (
          mergedItems.map((item) => {
            // Skip tool_result items that are rendered inline under their tool_use
            if (item.type === "tool_result" && inlineResultKeys.has(item.key)) {
              return null;
            }

            if (item.type === "text") {
              return (
                <div key={item.key} className="flex justify-start">
                  <div className="max-w-[85%] bg-gray-800 rounded-lg px-3 py-2 text-sm text-gray-200 leading-relaxed border border-gray-700">
                    <div className="prose-conversation">
                      <ReactMarkdown
                        remarkPlugins={REMARK_PLUGINS}
                        rehypePlugins={REHYPE_PLUGINS}
                        components={{
                          p: ({ children }) => <p className="text-gray-200 mb-1.5 last:mb-0">{children}</p>,
                          h1: ({ children }) => <h1 className="font-bold text-gray-100 mb-1 mt-1.5 text-base">{children}</h1>,
                          h2: ({ children }) => <h2 className="font-bold text-gray-100 mb-1 mt-1.5 text-sm">{children}</h2>,
                          h3: ({ children }) => <h3 className="font-semibold text-gray-200 mb-1 mt-1 text-sm">{children}</h3>,
                          ul: ({ children }) => <ul className="list-disc list-inside text-gray-300 mb-1.5 space-y-0.5 pl-1">{children}</ul>,
                          ol: ({ children }) => <ol className="list-decimal list-inside text-gray-300 mb-1.5 space-y-0.5 pl-1">{children}</ol>,
                          li: ({ children }) => <li className="text-gray-300">{children}</li>,
                          code: ({ className: codeClassName, children, ...props }) => {
                            const isBlock = codeClassName?.startsWith("language-");
                            if (isBlock) {
                              return (
                                <code
                                  className={`${codeClassName} block bg-gray-950 rounded p-2 my-1.5 overflow-x-auto font-mono text-[0.9em] whitespace-pre`}
                                  {...props}
                                >
                                  {children}
                                </code>
                              );
                            }
                            return (
                              <code className="bg-gray-700 text-gray-200 rounded px-1 py-0.5 font-mono text-[0.9em]" {...props}>
                                {children}
                              </code>
                            );
                          },
                          pre: ({ children }) => <pre className="my-1.5">{children}</pre>,
                          strong: ({ children }) => <strong className="font-semibold text-gray-100">{children}</strong>,
                          em: ({ children }) => <em className="italic text-gray-300">{children}</em>,
                          a: ({ href, children }) => (
                            <a href={href} className="text-indigo-400 hover:text-indigo-300 underline" target="_blank" rel="noopener noreferrer">
                              {children}
                            </a>
                          ),
                          blockquote: ({ children }) => (
                            <blockquote className="border-l-2 border-gray-600 pl-2 my-1.5 text-gray-400 italic">
                              {children}
                            </blockquote>
                          ),
                        }}
                      >
                        {stripLargeCodeBlockLanguages(item.text ?? "")}
                      </ReactMarkdown>
                    </div>
                  </div>
                </div>
              );
            }

            if (item.type === "tool_use") {
              const colors = getToolColor(item.toolName);
              const isExpanded = expandedTools.has(item.key);
              const matchedResult = item.toolUseId ? findToolResult(item.toolUseId) : null;
              const summary = toolSummary(item.toolName, item.toolInput, matchedResult?.resultContent);

              return (
                <div key={item.key} className={`rounded border ${colors.border} overflow-hidden ml-2`}>
                  {/* Collapsed summary header -- always visible */}
                  <button
                    onClick={() => toggleExpand(item.key)}
                    className={`flex items-center gap-1.5 w-full px-2 py-1.5 text-xs font-mono cursor-pointer ${colors.bg} ${colors.text} hover:opacity-90 transition-opacity text-left`}
                  >
                    <span className="flex-shrink-0 text-[10px] opacity-70">{isExpanded ? "\u25BC" : "\u25B6"}</span>
                    <span className="truncate">{summary}</span>
                  </button>

                  {/* Expanded: tool input + result */}
                  {isExpanded && (
                    <div className="border-t border-gray-700/50">
                      {/* Tool input */}
                      {item.toolInput && (
                        <div className="bg-gray-800/50">
                          <div className="px-2 py-0.5 text-[10px] text-gray-500 uppercase font-medium border-b border-gray-700/30">
                            Input
                          </div>
                          <pre className="p-2 text-xs text-gray-300 font-mono overflow-x-auto whitespace-pre-wrap max-h-40 overflow-y-auto leading-relaxed">
                            {typeof item.toolInput === "string"
                              ? item.toolInput.length > 2000
                                ? item.toolInput.slice(0, 2000) + "\n... (truncated)"
                                : item.toolInput
                              : JSON.stringify(item.toolInput, null, 2)}
                          </pre>
                        </div>
                      )}
                      {/* Matched tool result */}
                      {matchedResult && (
                        <div className="bg-gray-800/30">
                          <div className="px-2 py-0.5 text-[10px] text-gray-500 uppercase font-medium border-t border-b border-gray-700/30">
                            Output
                          </div>
                          <pre className="p-2 text-xs text-gray-300 font-mono overflow-x-auto whitespace-pre-wrap max-h-48 overflow-y-auto leading-relaxed">
                            {(matchedResult.resultContent ?? "").length > 3000
                              ? (matchedResult.resultContent ?? "").slice(0, 3000) + "\n... (truncated)"
                              : matchedResult.resultContent ?? "(empty)"}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            }

            // Orphaned tool_result (no matching tool_use) -- hidden entirely per AC1
            if (item.type === "tool_result") {
              return null;
            }

            if (item.type === "result") {
              return (
                <div key={item.key} className="flex justify-center py-2">
                  <div className="bg-green-900/50 border border-green-700 rounded-lg px-4 py-2 text-xs text-green-300 font-medium">
                    {item.resultText ?? "Execution complete"}
                  </div>
                </div>
              );
            }

            return null;
          })
        )}
      </div>

      {/* Footer */}
      <div className="px-3 py-1 bg-gray-800 border-t border-gray-700 text-xs text-gray-500 flex justify-between">
        <span>
          {mergedItems.length} events
          {toolCallCount > 0 && ` | ${toolCallCount} tool calls`}
        </span>
        <span>{autoScroll ? "Auto-scroll ON" : "Auto-scroll OFF"}</span>
      </div>
    </div>
  );
}
