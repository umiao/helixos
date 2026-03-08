import { useCallback, useState } from "react";
import type { ToastMessage } from "../components/Toast";

let toastIdCounter = 0;

export function useToasts() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const addToast = useCallback(
    (text: string, type: "success" | "error") => {
      const id = ++toastIdCounter;
      setToasts((prev) => [...prev, { id, text, type }]);
    },
    [],
  );

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return { toasts, addToast, dismissToast };
}
