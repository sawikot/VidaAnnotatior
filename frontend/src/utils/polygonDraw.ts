import type { Point } from "./coordinates";

/** Screen pixels within which a click counts as "on" a vertex (to close the polygon, or as a double-click). */
export const CLOSE_TOLERANCE_PX = 8;
/** Longest gap between two clicks that still counts as a double-click (ms). */
export const DOUBLE_CLICK_MS = 450;

export interface LastClick {
  time: number;
  at: Point;
}

/**
 * What a click does while a polygon is being drawn:
 * - "close": finish it -- a click on the first point, or the second click of a double-click, once there
 *   are at least 3 points;
 * - "ignore": the second click of a double-click that cannot finish yet (fewer than 3 points), which
 *   must not add a stray point on top of the last one;
 * - "add": a new vertex.
 * `unit` is coordinate units per screen pixel, so the tolerances are the same on screen at any zoom.
 */
export function polygonClick(points: Point[], at: Point, unit: number, time: number, last: LastClick | null): "add" | "close" | "ignore" {
  const tol = CLOSE_TOLERANCE_PX * unit;
  const near = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]) <= tol;
  if (points.length >= 3 && near(at, points[0])) return "close";
  const lastPoint = points[points.length - 1];
  const doubleClick = !!last && time - last.time <= DOUBLE_CLICK_MS && !!lastPoint && near(at, lastPoint);
  if (doubleClick) return points.length >= 3 ? "close" : "ignore";
  return "add";
}

/** The polygon to save: consecutive points closer than a couple of screen pixels merged into one. */
export function cleanPolygon(points: Point[], unit: number): Point[] {
  const tol = 2 * unit;
  const out: Point[] = [];
  for (const p of points) {
    const prev = out[out.length - 1];
    if (!prev || Math.hypot(p[0] - prev[0], p[1] - prev[1]) > tol) out.push(p);
  }
  if (out.length > 1) {
    const first = out[0];
    const last = out[out.length - 1];
    if (Math.hypot(first[0] - last[0], first[1] - last[1]) <= tol) out.pop(); // closing onto the start
  }
  return out;
}
