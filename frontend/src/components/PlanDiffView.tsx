/**
 * PlanDiffView -- simple unified text diff between two plan snapshots.
 *
 * Pure text diff using a basic line-by-line comparison.  No semantic
 * diffing or external libraries required.  Shows added/removed lines
 * in green/red with +/- prefixes.
 */

import { useMemo, useState } from "react";

interface PlanDiffViewProps {
  /** The previous plan text (baseline). */
  oldText: string;
  /** The new/current plan text. */
  newText: string;
}

interface DiffLine {
  type: "same" | "add" | "remove";
  text: string;
}

/**
 * Compute a simple line-by-line unified diff.
 *
 * Uses a basic LCS (Longest Common Subsequence) approach for small texts,
 * falling back to line-by-line comparison.
 */
function computeDiff(oldText: string, newText: string): DiffLine[] {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");

  // Simple LCS-based diff for reasonable sizes
  const m = oldLines.length;
  const n = newLines.length;

  // For very large texts, use a simplified approach
  if (m * n > 100000) {
    return simpleDiff(oldLines, newLines);
  }

  // Build LCS table
  const dp: number[][] = Array.from({ length: m + 1 }, () =>
    Array(n + 1).fill(0),
  );
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack to produce diff
  const result: DiffLine[] = [];
  let i = m;
  let j = n;
  const stack: DiffLine[] = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      stack.push({ type: "same", text: oldLines[i - 1] });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      stack.push({ type: "add", text: newLines[j - 1] });
      j--;
    } else {
      stack.push({ type: "remove", text: oldLines[i - 1] });
      i--;
    }
  }

  // Reverse since we built it backwards
  while (stack.length > 0) {
    result.push(stack.pop()!);
  }

  return result;
}

/** Simplified diff for very large texts: remove all old, add all new. */
function simpleDiff(oldLines: string[], newLines: string[]): DiffLine[] {
  const result: DiffLine[] = [];
  for (const line of oldLines) {
    result.push({ type: "remove", text: line });
  }
  for (const line of newLines) {
    result.push({ type: "add", text: line });
  }
  return result;
}

export default function PlanDiffView({ oldText, newText }: PlanDiffViewProps) {
  const [expanded, setExpanded] = useState(false);

  const diffLines = useMemo(
    () => computeDiff(oldText, newText),
    [oldText, newText],
  );

  const hasChanges = diffLines.some((l) => l.type !== "same");

  if (!hasChanges) return null;

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50">
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full px-2.5 py-2 flex items-center justify-between text-xs font-semibold text-blue-700 hover:bg-blue-100 transition-colors rounded-lg"
      >
        <span>Plan was modified since previous attempt</span>
        <span
          className="inline-block transition-transform text-[10px]"
          style={{
            transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
          }}
        >
          &#9654;
        </span>
      </button>
      {expanded && (
        <div className="px-2.5 pb-2.5">
          <pre className="text-[10px] font-mono bg-white rounded border border-blue-100 p-2 overflow-x-auto max-h-64 overflow-y-auto leading-relaxed">
            {diffLines.map((line, idx) => (
              <div
                key={idx}
                className={
                  line.type === "add"
                    ? "bg-green-50 text-green-800"
                    : line.type === "remove"
                      ? "bg-red-50 text-red-800"
                      : "text-gray-600"
                }
              >
                <span className="select-none inline-block w-3 text-center">
                  {line.type === "add" ? "+" : line.type === "remove" ? "-" : " "}
                </span>
                {line.text}
              </div>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}
