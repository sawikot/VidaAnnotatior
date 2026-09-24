import { create } from "zustand";
import { getAuthStatus, logout as apiLogout, setUnauthorizedHandler, type AuthUser } from "../services/api";

/**
 * Who is signed in. "loading" until the server has answered; "setup" while the app has no users yet
 * (the first administrator must be created); "signed-out"; "signed-in".
 */
export type AuthState = "loading" | "setup" | "signed-out" | "signed-in";

interface AuthStore {
  state: AuthState;
  user: AuthUser | null;
  load: () => Promise<void>;
  signedIn: (user: AuthUser) => void;
  signOut: () => Promise<void>;
}

export const useAuthStore = create<AuthStore>((set) => ({
  state: "loading",
  user: null,
  load: async () => {
    try {
      const status = await getAuthStatus();
      if (status.user) set({ state: "signed-in", user: status.user });
      else set({ state: status.needs_setup ? "setup" : "signed-out", user: null });
    } catch {
      set({ state: "signed-out", user: null });
    }
  },
  signedIn: (user) => set({ state: "signed-in", user }),
  signOut: async () => {
    try {
      await apiLogout();
    } finally {
      set({ state: "signed-out", user: null });
    }
  },
}));

// Any request refused with 401 (the session expired, or the account was disabled): back to sign-in.
setUnauthorizedHandler(() => {
  if (useAuthStore.getState().state === "signed-in") useAuthStore.setState({ state: "signed-out", user: null });
});

/** What the signed-in person may do (the server enforces the same rules; see backend api/access.py). */
export function useCan() {
  const user = useAuthStore((s) => s.user);
  return {
    user,
    /** Change project settings, add slides, run processing, manage members, create projects. */
    manage: user?.role === "admin" || user?.role === "manager",
    admin: user?.role === "admin",
  };
}

export const ROLE_LABELS: Record<string, string> = {
  admin: "Administrator",
  manager: "Project manager",
  annotator: "Annotator",
};
