import { useEffect, useState } from "react";
import { listPatches } from "../../services/api";
import type { Patch } from "../../types/api";
import type { ViewportBbox } from "./WsiViewer";

// Canonical patch-status color mapping (see DESIGN.md / Main Workspace navigator legend).
export const PATCH_STATUS_COLORS: Record<string, string> = {
  unannotated: "#64748b",
  active: "#38bdf8",
  annotated: "#34d399",
  reviewed: "#818cf8",
  skipped: "#fbbf24",
  flagged: "#f87171",
};

interface Props {
  slideId: number;
  bbox: ViewportBbox | null;
  activePatchId?: number | null;
  onPatchClick?: (patch: Patch) => void;
  refreshKey?: number;
}

export function PatchGridOverlay({ slideId, bbox, activePatchId, onPatchClick, refreshKey }: Props) {
  const [patches, setPatches] = useState<Patch[]>([]);

  useEffect(() => {
    if (!bbox) return;
    const bboxStr = `${Math.floor(bbox.x0)},${Math.floor(bbox.y0)},${Math.ceil(bbox.x1)},${Math.ceil(bbox.y1)}`;
    listPatches(slideId, { bbox: bboxStr, limit: 2000 })
      .then((res) => setPatches(res.items))
      .catch(() => {});
  }, [slideId, bbox?.x0, bbox?.y0, bbox?.x1, bbox?.y1, refreshKey]);

  return (
    <g>
      {patches.map((p) => {
        const isActive = p.id === activePatchId;
        const color = PATCH_STATUS_COLORS[p.status] ?? PATCH_STATUS_COLORS.unannotated;
        return (
          <rect
            key={p.id}
            x={p.x}
            y={p.y}
            width={p.width_l0}
            height={p.height_l0}
            fill={p.flagged ? "#f87171" : color}
            fillOpacity={isActive ? 0.35 : 0.12}
            stroke={isActive ? "#38bdf8" : color}
            strokeWidth={isActive ? p.width_l0 * 0.01 : p.width_l0 * 0.004}
            style={{ cursor: onPatchClick ? "pointer" : undefined, pointerEvents: "auto" }}
            onClick={() => onPatchClick?.(p)}
          />
        );
      })}
    </g>
  );
}
