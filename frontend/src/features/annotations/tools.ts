import type { AnnotationTool } from "../../stores/annotationStore";

export interface ToolDef {
  id: AnnotationTool;
  icon: string;
  key: string;
  label: string;
}

/** The drawing tools, in toolbar order. The patch view and the whole-slide view offer the same set. */
export const TOOLS: ToolDef[] = [
  { id: "select", icon: "near_me", key: "V", label: "Select / Move" },
  { id: "point", icon: "control_point", key: "N", label: "Point" },
  { id: "line", icon: "horizontal_rule", key: "L", label: "Line (drag)" },
  { id: "freehand_line", icon: "gesture", key: "G", label: "Freehand line (drag)" },
  { id: "rectangle", icon: "crop_square", key: "R", label: "Rectangle (drag)" },
  { id: "circle", icon: "radio_button_unchecked", key: "C", label: "Circle (drag from the centre)" },
  { id: "polygon", icon: "pentagon", key: "P", label: "Polygon (click points, double-click or Enter to finish)" },
  { id: "freehand", icon: "draw", key: "F", label: "Freehand polygon (drag)" },
];

/** Whole-slide view only: leaves the mouse to the viewer so a drag moves around the slide. */
export const PAN_TOOL: ToolDef = { id: "pan", icon: "pan_tool", key: "H", label: "Pan (drag to move around; hold Space in any tool)" };

export const HOTKEYS: Record<string, AnnotationTool> = Object.fromEntries([PAN_TOOL, ...TOOLS].map((t) => [t.key.toLowerCase(), t.id]));

/** The project's configuration decides which drawing tools it offers; Select is always there. */
export function visibleTools(enabledTools: string[] | undefined): ToolDef[] {
  const enabled = enabledTools ?? [];
  return TOOLS.filter((t) => t.id === "select" || enabled.length === 0 || enabled.includes(t.id));
}
