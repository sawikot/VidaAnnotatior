import type { GeometryType } from "../types/api";
import { create } from "zustand";

export type AnnotationTool = "select" | GeometryType;
export type SaveState = "idle" | "saving" | "saved" | "error";

interface AnnotationUiState {
  tool: AnnotationTool;
  setTool: (tool: AnnotationTool) => void;
  activeClassId: number | null;
  setActiveClassId: (id: number | null) => void;
  saveState: SaveState;
  setSaveState: (state: SaveState) => void;
}

export const useAnnotationStore = create<AnnotationUiState>((set) => ({
  tool: "polygon",
  setTool: (tool) => set({ tool }),
  activeClassId: null,
  setActiveClassId: (activeClassId) => set({ activeClassId }),
  saveState: "idle",
  setSaveState: (saveState) => set({ saveState }),
}));
