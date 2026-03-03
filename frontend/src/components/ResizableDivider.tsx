import { useCallback, useRef, useState } from "react";

const DEFAULT_HEIGHT = 224; // matches original h-56
const MIN_HEIGHT = 80;
const MAX_VIEWPORT_FRACTION = 0.6;

const STORAGE_KEY = "helixos_panel_height";

export function loadPanelHeight(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw !== null) {
      const parsed = parseFloat(raw);
      if (Number.isFinite(parsed) && parsed >= MIN_HEIGHT) {
        return Math.min(parsed, window.innerHeight * MAX_VIEWPORT_FRACTION);
      }
    }
  } catch {
    // localStorage unavailable
  }
  return DEFAULT_HEIGHT;
}

function savePanelHeight(height: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(height));
  } catch {
    // localStorage unavailable
  }
}

interface ResizableDividerProps {
  panelHeight: number;
  onHeightChange: (height: number) => void;
}

export default function ResizableDivider({
  panelHeight,
  onHeightChange,
}: ResizableDividerProps) {
  const [dragging, setDragging] = useState(false);
  const startYRef = useRef(0);
  const startHeightRef = useRef(0);

  const clampHeight = useCallback((h: number): number => {
    const maxH = window.innerHeight * MAX_VIEWPORT_FRACTION;
    return Math.max(MIN_HEIGHT, Math.min(h, maxH));
  }, []);

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.currentTarget.setPointerCapture(e.pointerId);
      startYRef.current = e.clientY;
      startHeightRef.current = panelHeight;
      setDragging(true);
    },
    [panelHeight],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      // Dragging up increases bottom panel height
      const delta = startYRef.current - e.clientY;
      const newHeight = clampHeight(startHeightRef.current + delta);
      onHeightChange(newHeight);
    },
    [dragging, clampHeight, onHeightChange],
  );

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      e.currentTarget.releasePointerCapture(e.pointerId);
      setDragging(false);
      // Persist final height
      const delta = startYRef.current - e.clientY;
      const finalHeight = clampHeight(startHeightRef.current + delta);
      savePanelHeight(finalHeight);
    },
    [dragging, clampHeight],
  );

  const handleDoubleClick = useCallback(() => {
    onHeightChange(DEFAULT_HEIGHT);
    savePanelHeight(DEFAULT_HEIGHT);
  }, [onHeightChange]);

  return (
    <div
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onDoubleClick={handleDoubleClick}
      className={`relative flex-shrink-0 cursor-row-resize select-none group ${
        dragging ? "bg-indigo-200" : "bg-gray-300 hover:bg-gray-400"
      }`}
      style={{ height: 6, touchAction: "none" }}
      title="Drag to resize panel. Double-click to reset."
    >
      {/* Grip dots */}
      <div className="absolute inset-0 flex items-center justify-center gap-1 pointer-events-none">
        <span
          className={`inline-block w-1 h-1 rounded-full ${
            dragging
              ? "bg-indigo-500"
              : "bg-gray-400 group-hover:bg-gray-600"
          }`}
        />
        <span
          className={`inline-block w-1 h-1 rounded-full ${
            dragging
              ? "bg-indigo-500"
              : "bg-gray-400 group-hover:bg-gray-600"
          }`}
        />
        <span
          className={`inline-block w-1 h-1 rounded-full ${
            dragging
              ? "bg-indigo-500"
              : "bg-gray-400 group-hover:bg-gray-600"
          }`}
        />
      </div>
    </div>
  );
}
