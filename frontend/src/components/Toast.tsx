/**
 * Toast notification component.
 * Renders a stack of temporary messages (success/error) that auto-dismiss.
 */

import { useEffect } from "react";

export interface ToastMessage {
  id: number;
  text: string;
  type: "success" | "error";
}

interface ToastProps {
  messages: ToastMessage[];
  onDismiss: (id: number) => void;
}

export default function Toast({ messages, onDismiss }: ToastProps) {
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {messages.map((msg) => (
        <ToastItem key={msg.id} message={msg} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastItem({
  message,
  onDismiss,
}: {
  message: ToastMessage;
  onDismiss: (id: number) => void;
}) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(message.id), 4000);
    return () => clearTimeout(timer);
  }, [message.id, onDismiss]);

  const bg =
    message.type === "error"
      ? "bg-red-600 text-white"
      : "bg-green-600 text-white";

  return (
    <div
      className={`rounded-lg px-4 py-2 shadow-lg text-sm ${bg} flex items-center justify-between gap-2`}
    >
      <span>{message.text}</span>
      <button
        onClick={() => onDismiss(message.id)}
        className="text-white/80 hover:text-white font-bold text-xs"
      >
        X
      </button>
    </div>
  );
}
