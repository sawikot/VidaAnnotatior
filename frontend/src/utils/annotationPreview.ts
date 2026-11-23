import type { GeometryType } from "../types/api";
import type { Point } from "./coordinates";
import { shapeBox } from "./viewCulling";

export interface PreviewFrame {
  x: number;
  y: number;
  side: number;
}

const MARGIN = 1.3; // the square is this much wider than the shape, so some tissue around it shows
const MIN_SIDE = 64; // Level-0 px: a point or a tiny shape still gets some surroundings

/** The square of the slide to picture a shape in: around the shape, moved inside the slide where it fits. */
export function previewFrame(type: GeometryType, points: Point[], slideWidth: number, slideHeight: number): PreviewFrame {
  const b = shapeBox(type, points);
  const side = Math.max(Math.ceil(Math.max(b.x1 - b.x0, b.y1 - b.y0) * MARGIN), MIN_SIDE);
  const place = (center: number, extent: number) => {
    const start = Math.round(center - side / 2);
    return side >= extent ? Math.round((extent - side) / 2) : Math.max(0, Math.min(extent - side, start));
  };
  return { x: place((b.x0 + b.x1) / 2, slideWidth), y: place((b.y0 + b.y1) / 2, slideHeight), side };
}

/** At most `max` of the points, evenly spread: a long outline needs no more at thumbnail size. */
export function thinPoints(points: Point[], max = 48): Point[] {
  if (points.length <= max) return points;
  const step = (points.length - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => points[Math.round(i * step)]);
}

/** The rows to render for a scroll position, with a few extra either side so scrolling shows no gaps. */
export function visibleRange(scrollTop: number, viewHeight: number, rowHeight: number, count: number, overscan = 4) {
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const end = Math.min(count, Math.ceil((scrollTop + viewHeight) / rowHeight) + overscan);
  return { start, end };
}
