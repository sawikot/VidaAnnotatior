import type { GeometryType } from "../types/api";
import type { Point } from "./coordinates";
import { MIN_RADIUS, circleGeometry, clampTranslation, constrainCircleEdge, translatePoints } from "./shapes";

/**
 * Editing an existing shape: which handles it shows, what dragging one does, and adding or
 * removing vertices. All pure, so the canvas only has to turn pointer events into these calls.
 */
export type Handle =
  | { kind: "vertex"; index: number } // a point of the outline (for a rectangle: a corner)
  | { kind: "edge"; index: number } // a rectangle side: 0 top, 1 right, 2 bottom, 3 left
  | { kind: "center" } // circle: drags the whole circle
  | { kind: "radius" }; // circle: changes its size

export interface HandleSpot {
  handle: Handle;
  at: Point;
}

/** Fewest vertices a shape may keep, or null when its points can't be added or removed. */
export function minVertices(type: GeometryType): number | null {
  if (type === "polygon" || type === "freehand") return 3;
  if (type === "freehand_line") return 2;
  return null;
}

const MIN_SIDE = 2;
const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const clampPoint = ([x, y]: Point, width: number, height: number): Point => [clamp(x, 0, width), clamp(y, 0, height)];

/** True for the four corners of an upright box, in the order top-left, top-right, bottom-right, bottom-left. */
export function isAxisAlignedRect(points: Point[]): boolean {
  if (points.length !== 4) return false;
  const eq = (a: number, b: number) => Math.abs(a - b) < 1e-6;
  return eq(points[0][1], points[1][1]) && eq(points[1][0], points[2][0]) && eq(points[2][1], points[3][1]) && eq(points[3][0], points[0][0]);
}

export function handlesFor(type: GeometryType, points: Point[]): HandleSpot[] {
  if (type === "point") return [];
  if (type === "circle") {
    return points.length >= 2
      ? [
          { handle: { kind: "center" }, at: points[0] },
          { handle: { kind: "radius" }, at: points[1] },
        ]
      : [];
  }
  const vertices: HandleSpot[] = points.map((at, index) => ({ handle: { kind: "vertex", index }, at }));
  if (type === "rectangle" && isAxisAlignedRect(points)) {
    const [tl, tr, br, bl] = points;
    const mid = (a: Point, b: Point): Point => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    return [
      ...vertices,
      { handle: { kind: "edge", index: 0 }, at: mid(tl, tr) },
      { handle: { kind: "edge", index: 1 }, at: mid(tr, br) },
      { handle: { kind: "edge", index: 2 }, at: mid(br, bl) },
      { handle: { kind: "edge", index: 3 }, at: mid(bl, tl) },
    ];
  }
  return vertices;
}

/** An upright rectangle from two opposite corners, never thinner than MIN_SIDE and kept inside the patch. */
function rectFrom(a: Point, b: Point, width: number, height: number): Point[] {
  const fit = (fixed: number, moving: number, limit: number) => {
    let m = moving;
    if (Math.abs(m - fixed) < MIN_SIDE) m = fixed + (m >= fixed ? MIN_SIDE : -MIN_SIDE);
    if (m < 0 || m > limit) m = fixed + (m > limit ? -MIN_SIDE : MIN_SIDE); // no room on that side: flip
    return m;
  };
  const bx = fit(a[0], b[0], width);
  const by = fit(a[1], b[1], height);
  const [minX, maxX] = [Math.min(a[0], bx), Math.max(a[0], bx)];
  const [minY, maxY] = [Math.min(a[1], by), Math.max(a[1], by)];
  return [[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY]];
}

/** The shape's new points while `handle` is dragged to `pointer` (always computed from the untouched `original`). */
export function dragHandle(type: GeometryType, original: Point[], handle: Handle, pointer: Point, width: number, height: number): Point[] {
  const p = clampPoint(pointer, width, height);

  switch (handle.kind) {
    case "vertex":
      if (type === "rectangle" && isAxisAlignedRect(original)) {
        return rectFrom(original[(handle.index + 2) % 4], p, width, height); // the opposite corner stays put
      }
      return original.map((pt, i) => (i === handle.index ? p : pt));

    case "edge": {
      let [minX, minY] = original[0];
      let [maxX, maxY] = original[2];
      if (handle.index === 0) minY = p[1];
      if (handle.index === 1) maxX = p[0];
      if (handle.index === 2) maxY = p[1];
      if (handle.index === 3) minX = p[0];
      // Keep the side that isn't being dragged where it was.
      return rectFrom([handle.index === 3 ? maxX : minX, handle.index === 0 ? maxY : minY], [handle.index === 3 ? minX : maxX, handle.index === 0 ? minY : maxY], width, height);
    }

    case "radius": {
      const edge = constrainCircleEdge(original[0], p, width, height);
      return circleGeometry([original[0], edge]).r < MIN_RADIUS ? original : [original[0], edge];
    }

    case "center": {
      const [dx, dy] = clampTranslation("circle", original, p[0] - original[0][0], p[1] - original[0][1], width, height);
      return translatePoints(original, dx, dy);
    }
  }
}

/** Distance from `p` to segment a-b, and the closest point on it. */
export function projectOnSegment(p: Point, a: Point, b: Point): { point: Point; distance: number } {
  const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
  const lengthSq = dx * dx + dy * dy;
  const t = lengthSq === 0 ? 0 : clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lengthSq, 0, 1);
  const point: Point = [a[0] + t * dx, a[1] + t * dy];
  return { point, distance: Math.hypot(p[0] - point[0], p[1] - point[1]) };
}

/**
 * A new vertex on the outline nearest to `at`, or null when `at` isn't within `tolerance` of it
 * (or the shape's points can't be added to). Polygons close back to their first point; a freehand line doesn't.
 */
export function insertVertex(type: GeometryType, points: Point[], at: Point, tolerance: number): Point[] | null {
  if (minVertices(type) === null) return null;
  const closed = type !== "freehand_line";
  const segments = closed ? points.length : points.length - 1;
  let best: { index: number; point: Point; distance: number } | null = null;
  for (let i = 0; i < segments; i++) {
    const hit = projectOnSegment(at, points[i], points[(i + 1) % points.length]);
    if (!best || hit.distance < best.distance) best = { index: i, ...hit };
  }
  if (!best || best.distance > tolerance) return null;
  return [...points.slice(0, best.index + 1), best.point, ...points.slice(best.index + 1)];
}

/** The points without vertex `index`, or null when that would leave too few. */
export function removeVertex(type: GeometryType, points: Point[], index: number): Point[] | null {
  const min = minVertices(type);
  if (min === null || points.length <= min || index < 0 || index >= points.length) return null;
  return points.filter((_, i) => i !== index);
}

export function samePoints(a: Point[], b: Point[]): boolean {
  return a.length === b.length && a.every((p, i) => Math.abs(p[0] - b[i][0]) < 1e-9 && Math.abs(p[1] - b[i][1]) < 1e-9);
}
