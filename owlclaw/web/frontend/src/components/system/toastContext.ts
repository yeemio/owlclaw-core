import { createContext } from "react";

export type ToastContextValue = {
  pushToast: (message: string) => void;
};

export const ToastContext = createContext<ToastContextValue | null>(null);
