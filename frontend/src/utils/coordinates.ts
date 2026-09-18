/**
 * Centralized Level-0 <-> patch coordinate transforms.
 *
 * Mirrors backend/app/services/coordinate_transform.py exactly (same fixture
 * values are tested on both sides in coordinates.test.ts / test_coordinate_transform.py)
 * so the two can never silently drift.
 *
 * Four coordinate spaces used across the app:
 * 1. Browser screen coordinates    -- never stored, converted immediately via
 *                                      getScreenCTM().inverse() in the annotation canvas.
 * 2. Patch-display coordinates     -- pixels of the image actually rendered (== what
 *                                      the dynamic /patch endpoint returns: width x height).
 * 3. Patch-source / Level-0 origin -- the (x, y) anchor of the patch in the slide's
 *                                      Level-0 (native) pixel grid.
 * 4. WSI Level-0 coordinates       -- the master, absolute coordinate system. Everything
 *                                      persisted and exported uses this space.
 *
 * Core rule: ANNOTATE LOCALLY (space 2), STORE GLOBALLY (space 4).
 */

export interface PatchOrigin {
  x: number; // Level-0 origin X
  y: number; // Level-0 origin Y
  level: number; // pyramid level the patch is read/displayed at
  downsample: number; // downsample factor of `level` relative to Level-0
}

export type Point = [number, number];

/** local(patch-display px) -> global(Level-0 px). global = origin + local * downsample */
export function patchLocalToLevel0(origin: PatchOrigin, localX: number, localY: number): Point {
  return [origin.x + localX * origin.downsample, origin.y + localY * origin.downsample];
}

/** Inverse of patchLocalToLevel0. */
export function level0ToPatchLocal(origin: PatchOrigin, globalX: number, globalY: number): Point {
  return [(globalX - origin.x) / origin.downsample, (globalY - origin.y) / origin.downsample];
}

export function polygonPatchLocalToLevel0(origin: PatchOrigin, points: Point[]): Point[] {
  return points.map(([x, y]) => patchLocalToLevel0(origin, x, y));
}

export function polygonLevel0ToPatchLocal(origin: PatchOrigin, points: Point[]): Point[] {
  return points.map(([x, y]) => level0ToPatchLocal(origin, x, y));
}

export function patchFootprintL0(width: number, height: number, downsample: number): [number, number] {
  return [Math.round(width * downsample), Math.round(height * downsample)];
}

export function level0BoundsOfPatch(
  origin: PatchOrigin,
  width: number,
  height: number,
): [number, number, number, number] {
  const [wL0, hL0] = patchFootprintL0(width, height, origin.downsample);
  return [origin.x, origin.y, origin.x + wL0, origin.y + hL0];
}

/**
 * Convert a browser mouse/pointer event position into an SVG element's own
 * user-space coordinates (== patch-display pixels, since the annotation
 * canvas SVG's viewBox is set to "0 0 patchWidth patchHeight"). Using the
 * element's screen CTM means no manual pan/zoom math is ever needed here --
 * the browser does the inversion for us.
 */
export function screenToSvgPoint(svg: SVGSVGElement, clientX: number, clientY: number): Point {
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return [clientX, clientY];
  const transformed = pt.matrixTransform(ctm.inverse());
  return [transformed.x, transformed.y];
}

export function bestLevelForMagnification(
  objectiveMagnification: number | null | undefined,
  levelDownsamples: number[],
  targetMagnification: number,
): number {
  const objMag = objectiveMagnification || 40.0;
  const targetDownsample = objMag / targetMagnification;
  let best = 0;
  let bestDiff = Infinity;
  levelDownsamples.forEach((ds, i) => {
    const diff = Math.abs(ds - targetDownsample);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  });
  return best;
}
