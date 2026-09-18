import { create } from "zustand";
import type { ProjectDetail, Slide } from "../types/api";

/** Holds just enough of the "currently open" project/slide for the shared app
 * chrome (header pill, nav highlighting) to display without every page needing
 * to re-fetch it. Pages set this after their own data fetch resolves -- the
 * server response is always the source of truth, this is a display cache. */
interface ContextState {
  activeProject: ProjectDetail | null;
  activeSlide: Slide | null;
  setActiveProject: (p: ProjectDetail | null) => void;
  setActiveSlide: (s: Slide | null) => void;
}

export const useContextStore = create<ContextState>((set) => ({
  activeProject: null,
  activeSlide: null,
  setActiveProject: (activeProject) => set({ activeProject }),
  setActiveSlide: (activeSlide) => set({ activeSlide }),
}));
