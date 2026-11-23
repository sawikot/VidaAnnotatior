import { useMemo, useState } from "react";
import type { AnnotationClass } from "../../types/api";
import { cullShapes, indexShapes, type Box, type CullableShape, type MarkerPath } from "../../utils/viewCulling";

/** How far past the screen shapes are prepared, as a share of its size, so a small pan needs no new work. */
const PADDING = 0.5;
const RESCALE_AT = 1.5;

const inside = (inner: Box, outer: Box) => inner.x0 >= outer.x0 && inner.y0 >= outer.y0 && inner.x1 <= outer.x1 && inner.y1 <= outer.y1;

/** The shapes to draw for the current view. The viewer reports every animation frame; this reworks them only
 * when the view leaves the padded area or the zoom changes noticeably. */
export function useViewCulling<T extends CullableShape>(shapes: T[], view: Box | null, scale: number, keepId: number | null) {
  const [region, setRegion] = useState<{ box: Box; scale: number } | null>(null);
  let current = region;
  if (view) {
    const ratio = region ? Math.max(scale / region.scale, region.scale / scale) : Infinity;
    if (!region || ratio > RESCALE_AT || !inside(view, region.box)) {
      const padX = (view.x1 - view.x0) * PADDING;
      const padY = (view.y1 - view.y0) * PADDING;
      current = { box: { x0: view.x0 - padX, y0: view.y0 - padY, x1: view.x1 + padX, y1: view.y1 + padY }, scale };
      setRegion(current); // adjusting state to a prop, during render
    }
  }
  const indexed = useMemo(() => indexShapes(shapes), [shapes]);
  return useMemo(() => cullShapes(indexed, current?.box ?? null, current?.scale ?? scale, keepId), [indexed, current, keepId]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** Dots standing in for shapes too numerous to draw in full at this zoom. */
export function ShapeMarkers({ markers, classes }: { markers: MarkerPath[]; classes: AnnotationClass[] }) {
  return (
    <g pointerEvents="none">
      {markers.map((m) => (
        <path key={String(m.classId)} d={m.d} fill={classes.find((c) => c.id === m.classId)?.color_hex ?? "#94a3b8"} />
      ))}
    </g>
  );
}
