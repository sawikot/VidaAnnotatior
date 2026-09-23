import type { AnnotationTool } from "../../stores/annotationStore";
import type { AnnotationClass, TissueRegionMode } from "../../types/api";
import { PAN_TOOL, TOOLS, type ToolDef } from "../annotations/tools";

export const REGION_COLORS: Record<TissueRegionMode, string> = { add: "#22c55e", remove: "#ef4444" };

/** ShapeLayer colours shapes by class: the two region modes stand in as classes 1 and 2. */
export const REGION_CLASSES: AnnotationClass[] = [
  { id: 1, name: "Add", color_hex: REGION_COLORS.add, hotkey: null, order_index: 0 },
  { id: 2, name: "Remove", color_hex: REGION_COLORS.remove, hotkey: null, order_index: 1 },
];
export const classIdOf = (mode: TissueRegionMode) => (mode === "add" ? 1 : 2);

const AREA_TOOL_IDS: AnnotationTool[] = ["select", "rectangle", "polygon", "freehand", "circle"];
/** Only area tools make sense for marking tissue. */
export const REGION_TOOLS: ToolDef[] = [PAN_TOOL, ...TOOLS.filter((t) => AREA_TOOL_IDS.includes(t.id))];
