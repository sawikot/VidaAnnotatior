import type { GeometryType } from "../types/api";
import type { Point } from "./coordinates";
import { circleGeometry } from "./shapes";

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface CullableShape {
  id: number;
  type: GeometryType;
  points: Point[];
  class_id: number | null;
}

export interface Indexed<T> {
  shape: T;
  box: Box;
}

/** Dots for one class, as a single SVG path: thousands of them cost the browser one element. */
export interface MarkerPath {
  classId: number | null;
  d: string;
}

export interface Culled<T> {
  detailed: T[];
  markers: MarkerPath[];
  markerCount: number;
}

/** The most shapes drawn in full at once; beyond this the smallest in view become dots. */
export const MAX_DETAILED = 1000;
const MARKER_PX = 4;
/** A shape smaller than this on screen is a dot whatever the count: its outline would be a smudge anyway,
 * and each one drawn in full costs the browser as much as a large one. */
export const MIN_DETAILED_PX = 6;

export function shapeBox(type: GeometryType, points: Point[]): Box {
  if (type === "circle" && points.length >= 2) {
    const { cx, cy, r } = circleGeometry(points);
    return { x0: cx - r, y0: cy - r, x1: cx + r, y1: cy + r };
  }
  // A loop, not Math.min(...xs): a long freehand outline can exceed the argument limit.
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of points) {
    if (x < x0) x0 = x;
    if (x > x1) x1 = x;
    if (y < y0) y0 = y;
    if (y > y1) y1 = y;
  }
  return { x0, y0, x1, y1 };
}

export function indexShapes<T extends CullableShape>(shapes: T[]): Indexed<T>[] {
  return shapes.map((shape) => ({ shape, box: shapeBox(shape.type, shape.points) }));
}

const overlaps = (a: Box, b: Box) => a.x0 <= b.x1 && a.x1 >= b.x0 && a.y0 <= b.y1 && a.y1 >= b.y0;
const boxArea = (b: Box) => (b.x1 - b.x0) * (b.y1 - b.y0);

/**
 * What to draw for a view: shapes outside it are skipped; if more than `maxDetailed` remain, the largest
 * are drawn in full and the others as dots, so nothing in view disappears. `keepId` (the selected shape)
 * is always drawn in full, wherever it is, so it can still be edited while the view moves.
 */
export function cullShapes<T extends CullableShape>(
  items: Indexed<T>[],
  view: Box | null,
  scale: number,
  keepId: number | null,
  maxDetailed = MAX_DETAILED,
): Culled<T> {
  const inView = view ? items.filter((it) => overlaps(it.box, view)) : items;
  const kept = keepId == null ? undefined : items.find((it) => it.shape.id === keepId);
  const keptOutside = kept && !inView.includes(kept) ? [kept.shape] : [];
  const minSide = MIN_DETAILED_PX / scale;
  const tiny = (b: Box) => b.x1 - b.x0 < minSide && b.y1 - b.y0 < minSide;
  if (inView.length <= maxDetailed && !inView.some((it) => tiny(it.box))) {
    return { detailed: [...keptOutside, ...inView.map((it) => it.shape)], markers: [], markerCount: 0 };
  }

  const areas = inView.filter((it) => !tiny(it.box)).map((it) => boxArea(it.box)).sort((a, b) => b - a);
  const threshold = areas.length > maxDetailed ? areas[maxDetailed - 1] : 0;
  const size = +(MARKER_PX / scale).toFixed(2);
  const paths = new Map<number | null, string[]>();
  const detailed: T[] = [...keptOutside];
  let taken = 0;
  let markerCount = 0;
  for (const { shape, box } of inView) {
    if (shape.id === keepId) {
      detailed.push(shape);
    } else if (taken < maxDetailed && !tiny(box) && boxArea(box) >= threshold) {
      detailed.push(shape);
      taken += 1;
    } else {
      const x = +((box.x0 + box.x1 - size) / 2).toFixed(1);
      const y = +((box.y0 + box.y1 - size) / 2).toFixed(1);
      const list = paths.get(shape.class_id) ?? [];
      list.push(`M${x} ${y}h${size}v${size}h-${size}z`);
      paths.set(shape.class_id, list);
      markerCount += 1;
    }
  }
  return { detailed, markers: [...paths].map(([classId, parts]) => ({ classId, d: parts.join("") })), markerCount };
}
