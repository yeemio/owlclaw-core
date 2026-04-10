import { useCallback, useMemo, useState } from "react";
import { ToastContext } from "@/components/system/toastContext";

type ToastItem = {
  id: number;
  message: string;
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const pushToast = useCallback((message: string) => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((item) => item.id !== id));
    }, 2500);
  }, []);

  const value = useMemo(() => ({ pushToast }), [pushToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed right-4 top-4 z-50 space-y-2">
        {toasts.map((item) => (
          <div key={item.id} className="rounded-md border border-border/70 bg-card/95 px-3 py-2 text-xs shadow-lg">
            {item.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
