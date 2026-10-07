import ClipperLib from "clipper-lib";
import type { GeometryType } from "../types/api";
import type { Point } from "./coordinates";
import { circleGeometry, isAreaShape, shapeBounds } from "./shapes";

/**
 * The brush: a stroke is a path with a width, turned into an outline and then combined with the
 * area shapes it touches -- united with one (add) or cut out of them (erase).
 *
 * A shape is a single outline: it cannot have holes or be in several parts. So erasing through a
 * shape leaves several shapes, and a hole rubbed in the middle of one does not hold: the eraser
 * has to come in from the edge.
 */

/** A box in the layer's coordinates. */
export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface BrushStroke {
  /** The centre line of the stroke. */
  path: Point[];
  radius: number;
  /** Coordinate units per screen pixel: how fine the outline needs to be. */
  unit: number;
  /** Where paint may land (the patch, or the slide). */
  extent: Box;
  /** The paint as an outline of its own, when it is a drawn shape and not a brush stroke (see shapePaint). */
  area?: Point[];
}

export interface BrushTarget {
  type: GeometryType;
  points: Point[];
  /** Where this shape must stay, when that is not the whole extent (a shape of a neighbouring patch). */
  bounds?: Box;
}

/** What a brush stroke did to the shapes, for whoever stores them. */
export type BrushChange =
  // `like`: the shape this piece was cut off, which it takes after; null for a new shape of the active class
  | { kind: "create"; points: Point[]; like: number | null }
  | { kind: "edit"; id: number; type: GeometryType; points: Point[] }
  | { kind: "delete"; id: number };

// Clipper works in integers: coordinates are kept to a hundredth of a unit.
const K = 100;
const CIRCLE_SEGMENTS = 64; // as the backend approximates a circle (geometry.py)
/** Pieces smaller than this many square screen pixels are crumbs, not shapes. */
const MIN_PIECE_PX2 = 6;

const toInt = (points: Point[]): ClipperLib.Path => points.map(([x, y]) => ({ X: Math.round(x * K), Y: Math.round(y * K) }));
const fromInt = (path: ClipperLib.Path): Point[] => path.map((p) => [p.X / K, p.Y / K]);
const isOuter = (path: ClipperLib.Path) => ClipperLib.Clipper.Orientation(path); // holes run the other way round
const areaOf = (paths: ClipperLib.Paths) => paths.reduce((sum, p) => sum + Math.abs(ClipperLib.Clipper.Area(p)), 0) / (K * K);
const boxPath = (b: Box): ClipperLib.Path =>
  toInt([
    [b.x0, b.y0],
    [b.x1, b.y0],
    [b.x1, b.y1],
    [b.x0, b.y1],
  ]);

/** The outline of an area shape as a polygon (a circle is approximated). */
export function areaRing(type: GeometryType, points: Point[]): Point[] {
  if (type !== "circle") return points;
  const { cx, cy, r } = circleGeometry(points);
  return Array.from({ length: CIRCLE_SEGMENTS }, (_, i): Point => {
    const a = (2 * Math.PI * i) / CIRCLE_SEGMENTS;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  });
}

/** A shape the brush reworked is a free outline; a polygon stays a polygon. */
export const brushedType = (type: GeometryType): GeometryType => (type === "polygon" ? "polygon" : "freehand");

/** Whether a point lies inside an area shape (even-odd rule). */
export function containsPoint(type: GeometryType, points: Point[], [x, y]: Point): boolean {
  if (!isAreaShape(type)) return false;
  const ring = areaRing(type, points);
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function combine(subject: ClipperLib.Paths, clip: ClipperLib.Paths, how: ClipperLib.ClipType): ClipperLib.Paths {
  const clipper = new ClipperLib.Clipper();
  clipper.AddPaths(subject, ClipperLib.PolyType.ptSubject, true);
  clipper.AddPaths(clip, ClipperLib.PolyType.ptClip, true);
  const out: ClipperLib.Paths = [];
  clipper.Execute(how, out, ClipperLib.PolyFillType.pftNonZero, ClipperLib.PolyFillType.pftNonZero);
  return out;
}

/** The area the stroke covers: its path widened by the radius, with round ends and corners. */
function strokeArea(stroke: BrushStroke): ClipperLib.Paths {
  if (stroke.area) return combine([toInt(stroke.area)], [], ClipperLib.ClipType.ctUnion);
  const offset = new ClipperLib.ClipperOffset(2, 0.1 * stroke.unit * K); // arcs within a tenth of a screen pixel
  offset.AddPath(toInt(stroke.path), ClipperLib.JoinType.jtRound, ClipperLib.EndType.etOpenRound);
  const out: ClipperLib.Paths = [];
  offset.Execute(out, stroke.radius * K);
  return out;
}

/** The outer outlines of a result, largest first -- holes dropped, crumbs thrown away, needless points removed. */
function outlines(paths: ClipperLib.Paths, unit: number): Point[][] {
  const minArea = MIN_PIECE_PX2 * unit * unit * K * K;
  return paths
    .filter(isOuter)
    .map((p) => ClipperLib.Clipper.CleanPolygon(p, 0.15 * unit * K))
    .filter((p) => p.length >= 3 && Math.abs(ClipperLib.Clipper.Area(p)) >= minArea)
    .sort((a, b) => Math.abs(ClipperLib.Clipper.Area(b)) - Math.abs(ClipperLib.Clipper.Area(a)))
    .map(fromInt);
}

/** Whether the stroke comes anywhere near the shape -- cheap, to leave most shapes alone. */
function reaches(stroke: BrushStroke, shape: BrushTarget): boolean {
  const s = shapeBounds("freehand_line", stroke.path);
  const b = shapeBounds(shape.type, shape.points);
  const r = stroke.radius;
  return s.minX - r <= b.maxX && s.maxX + r >= b.minX && s.minY - r <= b.maxY && s.maxY + r >= b.minY;
}

/** A drawn area shape as paint, so it can be joined onto shapes the way a brush stroke is. */
export function shapePaint(type: GeometryType, points: Point[], unit: number, extent: Box): BrushStroke {
  const area = areaRing(type, points);
  return { path: area, radius: 0, unit, extent, area };
}

/** A stroke as a shape of its own: its outline, or null when nothing of it lies inside the extent. */
export function paintShape(stroke: BrushStroke): Point[] | null {
  if (stroke.path.length === 0) return null;
  const painted = combine(strokeArea(stroke), [boxPath(stroke.extent)], ClipperLib.ClipType.ctIntersection);
  return outlines(painted, stroke.unit)[0] ?? null;
}

/** Whether the stroke's paint lies on the shape anywhere. */
export function touchesShape(shape: BrushTarget, stroke: BrushStroke): boolean {
  if (!isAreaShape(shape.type) || stroke.path.length === 0 || !reaches(stroke, shape)) return false;
  const ring = [toInt(areaRing(shape.type, shape.points))];
  return areaOf(combine(ring, strokeArea(stroke), ClipperLib.ClipType.ctIntersection)) > 0;
}

/**
 * The shapes and the stroke as one outline: the stroke grows the first shape and takes in the others
 * it connects it to. Null when that changes nothing (one shape, and the stroke stays inside it).
 */
export function addToShapes(shapes: BrushTarget[], stroke: BrushStroke): Point[] | null {
  if (shapes.length === 0 || stroke.path.length === 0 || !shapes.every((s) => isAreaShape(s.type))) return null;
  const rings = shapes.map((s) => toInt(areaRing(s.type, s.points)));
  const paint = combine(strokeArea(stroke), [boxPath(shapes[0].bounds ?? stroke.extent)], ClipperLib.ClipType.ctIntersection);
  const grown = outlines(combine(rings, paint, ClipperLib.ClipType.ctUnion), stroke.unit)[0];
  if (!grown) return null;
  if (shapes.length > 1) return grown;
  const before = combine(rings, [], ClipperLib.ClipType.ctUnion).filter(isOuter);
  const gained = areaOf([toInt(grown)]) - areaOf(before);
  return gained >= MIN_PIECE_PX2 * stroke.unit * stroke.unit ? grown : null;
}

/** The shape with the stroke added to it, or null when the stroke adds nothing. */
export function addToShape(shape: BrushTarget, stroke: BrushStroke): Point[] | null {
  return reaches(stroke, shape) ? addToShapes([shape], stroke) : null;
}

/**
 * What is left of the shape once the stroke is cut out of it: null when the stroke takes nothing
 * off it, [] when nothing is left, otherwise its pieces, largest first.
 */
export function eraseFromShape(shape: BrushTarget, stroke: BrushStroke): Point[][] | null {
  if (!isAreaShape(shape.type) || stroke.path.length === 0 || !reaches(stroke, shape)) return null;
  const ring = [toInt(areaRing(shape.type, shape.points))];
  const cut = combine(ring, strokeArea(stroke), ClipperLib.ClipType.ctDifference);
  const before = combine(ring, [], ClipperLib.ClipType.ctUnion).filter(isOuter);
  const outer = cut.filter(isOuter);
  // Only a hole, or nothing at all: the outline is what it was.
  if (outer.length === before.length && areaOf(before) - areaOf(outer) < MIN_PIECE_PX2 * stroke.unit * stroke.unit) return null;
  return outlines(cut, stroke.unit);
}
