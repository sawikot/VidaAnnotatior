import type { GeometryType } from "../types/api";
import type { Point } from "./coordinates";
import { polygonArea } from "./geometry";

/**
 * Every annotation is a type plus a list of [x, y] points (mirrors backend/app/services/geometry.py):
 *
 *   point          1 point
 *   line           2 points: start, end
 *   freehand_line  >= 2 points: an open path drawn by hand
 *   rectangle      4 corners
 *   circle         2 points: the centre, then a point on the circle
 *   polygon        >= 3 vertices, clicked one by one
 *   freehand       >= 3 vertices: a closed outline drawn by hand ("freehand polygon")
 */
export const AREA_TYPES: GeometryType[] = ["polygon", "freehand", "rectangle", "circle"];
export const LINE_TYPES: GeometryType[] = ["line", "freehand_line"];

export const isAreaShape = (type: GeometryType) => AREA_TYPES.includes(type);
export const isLineShape = (type: GeometryType) => LINE_TYPES.includes(type);

/** Smallest shapes worth keeping, in patch pixels; anything smaller is treated as a stray click or drag. */
export const MIN_DRAG = 4;
export const MIN_RADIUS = 3;
export const MIN_FREEHAND_LINE = 6;

export interface Circle {
  cx: number;
  cy: number;
  r: number;
}

/** points[0] is the centre, points[1] a point on the edge. */
export function circleGeometry(points: Point[]): Circle {
  const [[cx, cy], [ex, ey]] = points;
  return { cx, cy, r: Math.hypot(ex - cx, ey - cy) };
}

export function lineLength(points: Point[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  return total;
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** The box a shape occupies -- for a circle, the box around the whole circle, not just its two points. */
export function shapeBounds(type: GeometryType, points: Point[]): Bounds {
  if (type === "circle" && points.length >= 2) {
    const { cx, cy, r } = circleGeometry(points);
    return { minX: cx - r, minY: cy - r, maxX: cx + r, maxY: cy + r };
  }
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  return { minX: Math.min(...xs), minY: Math.min(...ys), maxX: Math.max(...xs), maxY: Math.max(...ys) };
}

export function translatePoints(points: Point[], dx: number, dy: number): Point[] {
  return points.map(([x, y]) => [x + dx, y + dy]);
}

/** Limits a drag so the whole shape stays inside the patch (a shape wider than the patch doesn't move). */
export function clampTranslation(type: GeometryType, points: Point[], dx: number, dy: number, width: number, height: number): [number, number] {
  const b = shapeBounds(type, points);
  const limit = (delta: number, lo: number, hi: number) => (lo > hi ? 0 : Math.min(Math.max(delta, lo), hi));
  return [limit(dx, -b.minX, width - b.maxX), limit(dy, -b.minY, height - b.maxY)];
}

/** Pulls the edge point in so a circle dragged from `center` never leaves the patch. */
export function constrainCircleEdge(center: Point, edge: Point, width: number, height: number): Point {
  const [cx, cy] = center;
  const maxR = Math.min(cx, cy, width - cx, height - cy);
  const r = Math.hypot(edge[0] - cx, edge[1] - cy);
  if (r <= maxR || r === 0) return edge;
  const k = Math.max(maxR, 0) / r;
  return [cx + (edge[0] - cx) * k, cy + (edge[1] - cy) * k];
}

/** Whether a finished gesture is big enough to keep. */
export function isDrawnEnough(type: GeometryType, points: Point[]): boolean {
  switch (type) {
    case "point":
      return points.length === 1;
    case "line":
      return points.length === 2 && lineLength(points) > MIN_DRAG;
    case "circle":
      return points.length === 2 && circleGeometry(points).r > MIN_RADIUS;
    case "rectangle": {
      const b = shapeBounds(type, points);
      return b.maxX - b.minX > MIN_DRAG && b.maxY - b.minY > MIN_DRAG;
    }
    case "freehand_line":
      return points.length >= 2 && lineLength(points) > MIN_FREEHAND_LINE;
    case "polygon":
    case "freehand":
      return points.length >= 3;
  }
}

/** Area in squared pixel units: exact for a circle, 0 for points and lines. */
export function shapeArea(type: GeometryType, points: Point[]): number {
  if (type === "circle") return points.length >= 2 ? Math.PI * circleGeometry(points).r ** 2 : 0;
  return isAreaShape(type) ? polygonArea(points) : 0;
}
