import type { Point } from "./coordinates";

/** The scale rounded to steps of about 9%: zooming changes it only now and then, so shapes drawn for one
 * step can be reused until the next, instead of being reworked on every frame of the zoom animation. */
export function scaleStep(scale: number): number {
  return 2 ** (Math.round(Math.log2(scale) * 8) / 8);
}

/** The points of an outline less than `tolerance` apart from the last one kept are dropped: zoomed out, a
 * brushed outline of thousands of points looks the same with a few dozen. Always keeps the first and last. */
export function simplifyPoints(points: Point[], tolerance: number): Point[] {
  if (points.length <= 4 || tolerance <= 0) return points;
  const t2 = tolerance * tolerance;
  const kept: Point[] = [points[0]];
  let [lx, ly] = points[0];
  for (let i = 1; i < points.length - 1; i++) {
    const [x, y] = points[i];
    if ((x - lx) * (x - lx) + (y - ly) * (y - ly) >= t2) {
      kept.push(points[i]);
      lx = x;
      ly = y;
    }
  }
  kept.push(points[points.length - 1]);
  return kept.length >= 3 ? kept : points;
}

const cache = new WeakMap<Point[], Map<number, string>>();

/** An SVG `points` attribute for an outline drawn at `scale` (screen px per unit), with detail under about
 * a screen pixel left out. Remembered per outline and scale step, so panning never rebuilds it. */
export function pointsAttr(points: Point[], scale: number): string {
  let byScale = cache.get(points);
  if (!byScale) {
    byScale = new Map();
    cache.set(points, byScale);
  }
  let attr = byScale.get(scale);
  if (attr === undefined) {
    attr = simplifyPoints(points, 0.75 / scale)
      .map((p) => p.join(","))
      .join(" ");
    byScale.set(scale, attr);
  }
  return attr;
}
