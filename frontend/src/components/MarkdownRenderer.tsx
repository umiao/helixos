/**
 * MarkdownRenderer -- unified markdown rendering component.
 *
 * Renders markdown content (headings, lists, code blocks, bold, italic, links)
 * via react-markdown. Provides consistent styling tokens, scroll container,
 * and optional font size toggle (S/M/L) with localStorage persistence.
 *
 * Usage sites: plan content in ReviewPanel, plan_snapshot diffs in PlanDiffView,
 * reviewer raw output.
 */

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypePrism from "rehype-prism-plus";

type FontSize = "S" | "M" | "L";

const FONT_SIZE_KEY = "helixos-md-font-size";

const FONT_SIZE_CLASSES: Record<FontSize, string> = {
  S: "text-[10px] leading-relaxed",
  M: "text-xs leading-relaxed",
  L: "text-sm leading-relaxed",
};

function loadFontSize(): FontSize {
  try {
    const stored = localStorage.getItem(FONT_SIZE_KEY);
    if (stored === "S" || stored === "M" || stored === "L") return stored;
  } catch {
    // localStorage unavailable
  }
  return "M";
}

function saveFontSize(size: FontSize) {
  try {
    localStorage.setItem(FONT_SIZE_KEY, size);
  } catch {
    // localStorage unavailable
  }
}

interface MarkdownRendererProps {
  /** The markdown text to render. */
  content: string;
  /** Maximum height before scrolling (CSS value). Default: "16rem" (max-h-64). */
  maxHeight?: string;
  /** Show font size toggle buttons. Default: true. */
  showSizeToggle?: boolean;
  /** Additional CSS classes for the outer container. */
  className?: string;
}

export default function MarkdownRenderer({
  content,
  maxHeight = "16rem",
  showSizeToggle = true,
  className = "",
}: MarkdownRendererProps) {
  const [fontSize, setFontSize] = useState<FontSize>(loadFontSize);

  const handleSizeChange = (size: FontSize) => {
    setFontSize(size);
    saveFontSize(size);
  };

  const sizes: FontSize[] = ["S", "M", "L"];

  return (
    <div className={`relative ${className}`}>
      {showSizeToggle && (
        <div className="flex justify-end mb-1">
          <div className="inline-flex rounded border border-gray-200 overflow-hidden">
            {sizes.map((s) => (
              <button
                key={s}
                onClick={() => handleSizeChange(s)}
                className={`px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
                  fontSize === s
                    ? "bg-indigo-100 text-indigo-700"
                    : "bg-white text-gray-500 hover:bg-gray-50"
                }`}
                title={`Font size: ${s === "S" ? "Small" : s === "M" ? "Medium" : "Large"}`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}
      <div
        className={`overflow-y-auto overflow-x-auto bg-gray-50 rounded border border-gray-100 p-2.5 ${FONT_SIZE_CLASSES[fontSize]}`}
        style={{ maxHeight }}
      >
        <div className="prose-compact">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[[rehypePrism, { ignoreMissing: true }]]}
            components={{
              h1: ({ children }) => (
                <h1 className="font-bold text-gray-900 mb-1.5 mt-2 border-b border-gray-200 pb-1" style={{ fontSize: "1.25em" }}>
                  {children}
                </h1>
              ),
              h2: ({ children }) => (
                <h2 className="font-bold text-gray-800 mb-1 mt-2" style={{ fontSize: "1.1em" }}>
                  {children}
                </h2>
              ),
              h3: ({ children }) => (
                <h3 className="font-semibold text-gray-800 mb-1 mt-1.5" style={{ fontSize: "1.05em" }}>
                  {children}
                </h3>
              ),
              p: ({ children }) => (
                <p className="text-gray-700 mb-1.5">{children}</p>
              ),
              ul: ({ children }) => (
                <ul className="list-disc list-inside text-gray-700 mb-1.5 space-y-0.5 pl-1">
                  {children}
                </ul>
              ),
              ol: ({ children }) => (
                <ol className="list-decimal list-inside text-gray-700 mb-1.5 space-y-0.5 pl-1">
                  {children}
                </ol>
              ),
              li: ({ children }) => (
                <li className="text-gray-700">{children}</li>
              ),
              code: ({ className: codeClassName, children, ...props }) => {
                const isBlock = codeClassName?.startsWith("language-");
                if (isBlock) {
                  return (
                    <code
                      className="block bg-gray-800 text-gray-100 rounded p-2 my-1.5 overflow-x-auto font-mono text-[0.9em] whitespace-pre"
                      {...props}
                    >
                      {children}
                    </code>
                  );
                }
                return (
                  <code
                    className="bg-gray-200 text-gray-800 rounded px-1 py-0.5 font-mono text-[0.9em]"
                    {...props}
                  >
                    {children}
                  </code>
                );
              },
              pre: ({ children }) => (
                <pre className="my-1.5">{children}</pre>
              ),
              a: ({ href, children }) => (
                <a
                  href={href}
                  className="text-indigo-600 hover:text-indigo-800 underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {children}
                </a>
              ),
              blockquote: ({ children }) => (
                <blockquote className="border-l-2 border-gray-300 pl-2 my-1.5 text-gray-600 italic">
                  {children}
                </blockquote>
              ),
              strong: ({ children }) => (
                <strong className="font-semibold text-gray-900">{children}</strong>
              ),
              em: ({ children }) => (
                <em className="italic text-gray-700">{children}</em>
              ),
              hr: () => <hr className="my-2 border-gray-200" />,
              table: ({ children }) => (
                <div className="overflow-x-auto my-1.5">
                  <table className="min-w-full border-collapse border border-gray-200">
                    {children}
                  </table>
                </div>
              ),
              th: ({ children }) => (
                <th className="border border-gray-200 bg-gray-100 px-2 py-1 text-left font-semibold text-gray-700">
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td className="border border-gray-200 px-2 py-1 text-gray-700">
                  {children}
                </td>
              ),
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
