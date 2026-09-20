import type { LayerShape } from "../features/annotations/ShapeLayer";
import type { GeometryAnnotation } from "../types/api";
import type { Point } from "./coordinates";
import { shapeBounds } from "./shapes";

/** The part of a patch that matters for placing things in it: its Level-0 origin and the size it is shown at. */
export interface PatchFrame {
  x: number;
  y: number;
  /** size in patch pixels (what is drawn) */
  width: number;
  height: number;
  /** footprint in Level-0 pixels */
  width_l0: number;
  height_l0: number;
}

/** local = (level0 - origin) / downsample -- the inverse of "global = origin + local * downsample". */
export function level0ToLocal(frame: PatchFrame, [x, y]: Point): Point {
  return [(x - frame.x) / (frame.width_l0 / frame.width), (y - frame.y) / (frame.height_l0 / frame.height)];
}

/**
 * Whole-slide annotations as they lie inside one patch, in that patch's pixels, for showing them there.
 * Nothing is clipped: the patch's drawing surface clips whatever sticks out. Shapes that cannot touch the
 * patch are left out, so a slide with many annotations stays cheap.
 */
export function projectSlideShapes(annotations: GeometryAnnotation[], frame: PatchFrame): LayerShape[] {
  const right = frame.x + frame.width_l0;
  const bottom = frame.y + frame.height_l0;
  const shapes: LayerShape[] = [];
  for (const a of annotations) {
    const points = a.coordinates_level0 as Point[];
    if (points.length === 0) continue;
    const b = shapeBounds(a.type, points);
    if (b.maxX < frame.x || b.minX > right || b.maxY < frame.y || b.minY > bottom) continue;
    shapes.push({ id: a.id, type: a.type, points: points.map((p) => level0ToLocal(frame, p)), class_id: a.class_id, unsure: a.unsure, excluded: a.excluded });
  }
  return shapes;
}

/** A slide-level annotation in the shape the drawing layer expects, still in Level-0 pixels. */
export function toLevel0Shape(a: GeometryAnnotation): LayerShape {
  return { id: a.id, type: a.type, points: a.coordinates_level0 as Point[], class_id: a.class_id, unsure: a.unsure, excluded: a.excluded };
}
