import type { GeometryType } from "../types/api";
import { create } from "zustand";

/** "pan" only exists on the whole-slide view, where it leaves the mouse to the viewer (drag to move around). */
/** "brush" paints areas: it makes freehand outlines, and adds to or cuts into the area shapes already there. */
export type AnnotationTool = "pan" | "select" | "brush" | GeometryType;
export type SaveState = "idle" | "saving" | "saved" | "error";

/** new: every stroke is a shape of its own; add: a stroke that starts on a shape grows that shape; erase: strokes are cut out of shapes. */
export type BrushMode = "new" | "add" | "erase";
export interface BrushSettings {
  mode: BrushMode;
  /** Diameter in screen pixels, so the brush covers the same part of the screen at any zoom. */
  size: number;
  /** What erasing may cut into, with the brush or a drawn shape: every area shape, or only those of the active class. */
  eraseScope: "all" | "class";
}
/** For the tools that draw an area (rectangle, circle, polygon, freehand): each shape on its own, joined onto the shapes it touches, or cut out of them. */
export type ShapeMode = "new" | "add" | "erase";
export const BRUSH_MIN_SIZE = 4;
export const BRUSH_MAX_SIZE = 200;

interface AnnotationUiState {
  tool: AnnotationTool;
  setTool: (tool: AnnotationTool) => void;
  brush: BrushSettings;
  setBrush: (changes: Partial<BrushSettings>) => void;
  shapeMode: ShapeMode;
  setShapeMode: (mode: ShapeMode) => void;
  activeClassId: number | null;
  setActiveClassId: (id: number | null) => void;
  saveState: SaveState;
  setSaveState: (state: SaveState) => void;
}

export const useAnnotationStore = create<AnnotationUiState>((set) => ({
  tool: "polygon",
  setTool: (tool) => set({ tool }),
  brush: { mode: "add", size: 24, eraseScope: "class" },
  setBrush: (changes) =>
    set((s) => {
      const brush = { ...s.brush, ...changes };
      brush.size = Math.round(Math.min(BRUSH_MAX_SIZE, Math.max(BRUSH_MIN_SIZE, brush.size)));
      return { brush };
    }),
  shapeMode: "new",
  setShapeMode: (shapeMode) => set({ shapeMode }),
  activeClassId: null,
  setActiveClassId: (activeClassId) => set({ activeClassId }),
  saveState: "idle",
  setSaveState: (saveState) => set({ saveState }),
}));
