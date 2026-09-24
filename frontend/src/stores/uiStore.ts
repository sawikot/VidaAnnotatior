import { create } from "zustand";

export interface Toast {
  id: number;
  message: string;
  tone: "info" | "success" | "error";
}

interface UiState {
  toasts: Toast[];
  pushToast: (message: string, tone?: Toast["tone"]) => void;
  dismissToast: (id: number) => void;
  /** The keyboard-shortcut list (opened with "?" or the header's help button). */
  shortcutsOpen: boolean;
  setShortcutsOpen: (open: boolean) => void;
}

let toastId = 0;

export const useUiStore = create<UiState>((set) => ({
  toasts: [],
  pushToast: (message, tone = "info") => {
    const id = ++toastId;
    set((s) => ({ toasts: [...s.toasts, { id, message, tone }] }));
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, 3200);
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  shortcutsOpen: false,
  setShortcutsOpen: (open) => set({ shortcutsOpen: open }),
}));
