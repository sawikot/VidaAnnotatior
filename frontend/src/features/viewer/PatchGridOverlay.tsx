import { useEffect, useRef, useState } from "react";
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

const SETTLE_MS = 150;
const LIMIT = 2000;

/** Fetched area and whether everything in it came back (under the limit). */
interface Fetched {
  box: ViewportBbox;
  complete: boolean;
  key: string;
}

const contains = (outer: ViewportBbox, inner: ViewportBbox) =>
  inner.x0 >= outer.x0 && inner.y0 >= outer.y0 && inner.x1 <= outer.x1 && inner.y1 <= outer.y1;

export function PatchGridOverlay({ slideId, bbox, activePatchId, onPatchClick, refreshKey }: Props) {
  const [patches, setPatches] = useState<Patch[]>([]);
  const fetched = useRef<Fetched | null>(null);

  useEffect(() => {
    if (!bbox) return;
    const key = `${slideId}:${refreshKey ?? 0}`;
    const have = fetched.current;
    // Still inside an area fetched in full, for the same slide and data: nothing new to show.
    if (have && have.key === key && have.complete && contains(have.box, bbox)) return;

    const timer = window.setTimeout(() => {
      // Half a view of margin on every side, so small pans need no request at all.
      const mx = (bbox.x1 - bbox.x0) / 2;
      const my = (bbox.y1 - bbox.y0) / 2;
      const box = { x0: bbox.x0 - mx, y0: bbox.y0 - my, x1: bbox.x1 + mx, y1: bbox.y1 + my };
      const bboxStr = `${Math.floor(box.x0)},${Math.floor(box.y0)},${Math.ceil(box.x1)},${Math.ceil(box.y1)}`;
      listPatches(slideId, { bbox: bboxStr, limit: LIMIT })
        .then((res) => {
          fetched.current = { box, complete: res.items.length < LIMIT, key };
          setPatches(res.items);
        })
        .catch(() => {});
    }, have && have.key === key ? SETTLE_MS : 0);
    return () => window.clearTimeout(timer);
  }, [slideId, bbox?.x0, bbox?.y0, bbox?.x1, bbox?.y1, refreshKey]);

  const activePatch = patches.find((p) => p.id === activePatchId);

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
      {/* A crisp, constant-pixel-width marker for the active patch, drawn last (on
          top) so it's never lost against the tissue image or overlapped by
          neighboring patches -- the per-item stroke above scales with the viewBox
          (40000+ units wide), so at small-viewport scales (the workspace minimap)
          it shrinks to sub-pixel and becomes invisible. vector-effect keeps this
          ring's stroke a fixed screen size regardless of zoom. */}
      {activePatch && (
        <g style={{ pointerEvents: "none" }}>
          <rect
            x={activePatch.x - activePatch.width_l0 * 0.2}
            y={activePatch.y - activePatch.height_l0 * 0.2}
            width={activePatch.width_l0 * 1.4}
            height={activePatch.height_l0 * 1.4}
            fill="none"
            stroke="#38bdf8"
            strokeWidth={1.5}
            strokeDasharray="6 4"
            vectorEffect="non-scaling-stroke"
            opacity={0.7}
          />
          <rect
            className="patch-active-pulse"
            x={activePatch.x}
            y={activePatch.y}
            width={activePatch.width_l0}
            height={activePatch.height_l0}
            fill="#38bdf8"
            fillOpacity={0.3}
            stroke="#38bdf8"
            strokeWidth={2.5}
            vectorEffect="non-scaling-stroke"
          />
        </g>
      )}
    </g>
  );
}
