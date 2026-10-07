import { useEffect, useRef, useState } from "react";
import { screenToSvgPoint, type Point } from "../../utils/coordinates";
import {
  circleGeometry,
  clampTranslation,
  constrainCircleEdge,
  isAreaShape,
  isDrawnEnough,
  isLineShape,
  translatePoints,
} from "../../utils/shapes";
import { dragHandle, handlesFor, insertVertex, removeVertex, type Handle, type HandleSpot } from "../../utils/shapeEdit";
import { CLOSE_TOLERANCE_PX, cleanPolygon, polygonClick, type LastClick } from "../../utils/polygonDraw";
import {
  addToShapes,
  brushedType,
  containsPoint,
  eraseFromShape,
  paintShape,
  shapePaint,
  touchesShape,
  type BrushChange,
  type BrushStroke,
} from "../../utils/brush";
import { useAnnotationStore, type AnnotationTool } from "../../stores/annotationStore";
import type { AnnotationClass, GeometryType } from "../../types/api";

/** What the layer needs to know about a shape to draw it. */
export interface LayerShape {
  id: number;
  type: GeometryType;
  points: Point[];
  class_id: number | null;
  unsure: boolean;
  excluded: boolean;
  /** The fill of a patch label: it stands for the whole patch, so the brush leaves it alone. */
  label?: boolean;
  /** Where moves and reshapes must keep it, in this layer's units. Default: the whole layer. A shape that
   * belongs to an overlapping patch is kept inside that patch, which can reach past this one's edge. */
  bounds?: { x0: number; y0: number; x1: number; y1: number };
}

interface Props {
  /** The extent of the coordinate space the shapes live in: a patch's pixels, or the slide's Level-0 pixels. */
  width: number;
  height: number;
  /** Screen pixels per coordinate unit -- keeps handles, strokes and minimum sizes constant on screen at any zoom. */
  scale: number;
  tool: AnnotationTool;
  /** The shapes that can be drawn, selected and edited here. */
  shapes: LayerShape[];
  /** Shapes from the other view, shown faintly for context; pressing one calls `onBackgroundPress`. */
  background?: LayerShape[];
  onBackgroundPress?: (id: number) => void;
  classes: AnnotationClass[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onShapeComplete: (type: GeometryType, points: Point[]) => void;
  /** A shape's new points once the Select tool finishes moving or reshaping it. */
  onShapeEdit: (id: number, points: Point[]) => void;
  onDeleteSelected: () => void;
  /** What a brush stroke did: shapes reshaped, cut into pieces, removed or painted new. Without it there is no brush. */
  onBrush?: (changes: BrushChange[]) => void;
  /** The class the brush paints with, where that is not the annotation class picked in the workspace. */
  brushClassId?: number | null;
  /** True while a gesture that started on this layer is in progress, so a viewer underneath can hold still. */
  gestureRef?: React.MutableRefObject<boolean>;
  /** Anything that changes when in-progress drawing should be abandoned (another image, another patch). */
  resetKey?: unknown;
  /** How solid area shapes are filled (0 = outline only, 1 = solid). Default 0.25. */
  fillOpacity?: number;
}

const FREEHAND_MIN_DIST = 4; // screen px
const PREVIEW = "#38bdf8";
const DRAG_TOOLS: AnnotationTool[] = ["rectangle", "line", "circle"];
const PATH_TOOLS: AnnotationTool[] = ["freehand", "freehand_line"];
const ERASER = "#f87171";
const AREA_TOOLS: AnnotationTool[] = ["rectangle", "circle", "polygon", "freehand"];

/** A shape being moved (no handle) or reshaped (a handle), previewed before it is saved. */
interface EditState {
  id: number;
  type: GeometryType;
  handle: Handle | null;
  start: Point;
  original: Point[];
  preview: Point[];
  /** The box the shape must stay in: edits are worked out relative to its corner. */
  frame: { x0: number; y0: number; w: number; h: number };
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

/**
 * The drawing and editing surface, as SVG content: draws shapes, turns pointer input into new shapes
 * (per tool) and into moves, reshapes and point insertions/removals (Select tool). It knows nothing
 * about what it sits on -- a patch image or a whole-slide viewer -- only the extent of its space.
 */
export function ShapeLayer({
  width,
  height,
  scale,
  tool,
  shapes,
  background = [],
  onBackgroundPress,
  classes,
  selectedId,
  onSelect,
  onShapeComplete,
  onShapeEdit,
  onDeleteSelected,
  onBrush,
  brushClassId,
  gestureRef,
  resetKey,
  fillOpacity,
}: Props) {
  const brush = useAnnotationStore((s) => s.brush);
  const setBrush = useAnnotationStore((s) => s.setBrush);
  const shapeMode = useAnnotationStore((s) => s.shapeMode);
  const workspaceClassId = useAnnotationStore((s) => s.activeClassId);
  const activeClassId = brushClassId !== undefined ? brushClassId : workspaceClassId;
  const rootRef = useRef<SVGGElement>(null);
  const [drawPoints, setDrawPoints] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [edit, setEdit] = useState<EditState | null>(null);
  const dragStart = useRef<Point | null>(null);
  const lastClick = useRef<LastClick | null>(null);
  /** The brush stroke being painted: whether it erases, and (adding) the shape it started on, if any. */
  const [painting, setPainting] = useState<{ erase: boolean; targetId: number | null } | null>(null);

  const active = tool !== "pan"; // "pan" leaves the pointer to whatever is underneath
  // The drawing tool in use, which Pan does not replace: panning (holding Space, or the Pan tool) in the middle
  // of a polygon or a drag must leave the shape as it was, to be carried on when the pointer comes back.
  const [drawTool, setDrawTool] = useState<AnnotationTool>(tool);
  if (tool !== "pan" && tool !== drawTool) setDrawTool(tool); // adjusting state to a prop, during render
  const px = (n: number) => n / scale;
  const unit = 1 / scale;
  const classColor = (id: number | null) => classes.find((c) => c.id === id)?.color_hex ?? PREVIEW;
  const setGesture = (on: boolean) => {
    if (gestureRef) gestureRef.current = on;
  };

  function localPoint(e: { clientX: number; clientY: number }): Point {
    const svg = rootRef.current?.ownerSVGElement;
    return svg ? screenToSvgPoint(svg, e.clientX, e.clientY) : [0, 0];
  }
  const clampToSpace = ([x, y]: Point): Point => [Math.max(0, Math.min(width, x)), Math.max(0, Math.min(height, y))];
  const brushRadius = px(brush.size / 2);
  const brushable = (shape: LayerShape) => isAreaShape(shape.type) && !shape.label;
  const extent = { x0: 0, y0: 0, x1: width, y1: height };

  /** The paint joined onto `first` and the shapes of `touched` kept where it is kept, as changes; none if it adds nothing. */
  function joinChanges(first: LayerShape, touched: LayerShape[], paint: BrushStroke, type: GeometryType): BrushChange[] {
    // Shapes kept in different places (this patch, a neighbour, the slide) are not joined to each other.
    const home = JSON.stringify(first.bounds ?? null);
    const group = [first, ...touched.filter((s) => s.id !== first.id && JSON.stringify(s.bounds ?? null) === home)];
    const points = addToShapes(group, paint);
    if (!points) return [];
    return [{ kind: "edit", id: first.id, type, points }, ...group.slice(1).map((taken): BrushChange => ({ kind: "delete", id: taken.id }))];
  }

  /** The paint cut out of every shape it crosses (of the active class only, if erasing is limited to it), as changes. */
  function eraseChanges(paint: BrushStroke): BrushChange[] {
    const changes: BrushChange[] = [];
    for (const shape of shapes) {
      if (!brushable(shape) || (brush.eraseScope === "class" && shape.class_id !== activeClassId)) continue;
      const pieces = eraseFromShape(shape, paint);
      if (!pieces) continue;
      if (pieces.length === 0) {
        changes.push({ kind: "delete", id: shape.id });
        continue;
      }
      // The largest piece stays the shape it was; the others become shapes like it.
      changes.push({ kind: "edit", id: shape.id, type: brushedType(shape.type), points: pieces[0] });
      for (const points of pieces.slice(1)) changes.push({ kind: "create", points, like: shape.id });
    }
    return changes;
  }

  /**
   * A shape has been drawn. In the "add" mode an area shape joins the shapes of the active class it
   * touches (several become one) instead of being a shape of its own; drawn inside one, it adds nothing.
   * In the "erase" mode it is not kept at all: it is cut out of the shapes it crosses.
   */
  function completeShape(type: GeometryType, points: Point[]) {
    if (shapeMode === "erase" && onBrush && isAreaShape(type)) {
      const changes = eraseChanges(shapePaint(type, points, unit, extent));
      if (changes.length > 0) onBrush(changes);
      return;
    }
    if (shapeMode === "add" && onBrush && isAreaShape(type)) {
      const paint = shapePaint(type, points, unit, extent);
      const touched = shapes.filter((s) => brushable(s) && s.class_id === activeClassId && touchesShape(s, paint));
      if (touched.length > 0) {
        // Straight-edged shapes make a polygon; anything joined onto a freehand outline stays one.
        const changes = joinChanges(touched[0], touched, paint, touched[0].type === "freehand" || type === "freehand" ? "freehand" : "polygon");
        if (changes.length > 0) onBrush(changes);
        return;
      }
    }
    onShapeComplete(type, points);
  }

  // A gesture (drawing a shape, dragging a handle) is followed on the window, not through pointer capture:
  // a viewer underneath (OpenSeadragon) captures the pointer for itself, and its move/up events would
  // never reach this layer. The handlers are read from a ref so they always see the latest state.
  const latest = useRef({ move: (_e: PointerEvent) => {}, up: () => {}, key: (_e: KeyboardEvent) => {} });
  const tracking = useRef(false);
  function trackPointer() {
    if (tracking.current) return;
    tracking.current = true;
    const onMove = (e: PointerEvent) => latest.current.move(e);
    const onEnd = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
      window.removeEventListener("pointercancel", onEnd);
      tracking.current = false;
      latest.current.up();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd);
    window.addEventListener("pointercancel", onEnd);
  }

  function handlePointerDown(e: React.PointerEvent) {
    const pt = clampToSpace(localPoint(e));

    if (tool === "select") {
      onSelect(null);
      return;
    }
    if (tool === "point") {
      onShapeComplete("point", [pt]);
      return;
    }
    if (tool === "brush") {
      // Not held to the space: the stroke may run over the edge, where its paint is cut off.
      const at = localPoint(e);
      const erase = brush.mode === "erase" || e.shiftKey;
      // Adding grows the shape the stroke starts on (the selected one if it is there, else the one on top)
      // and, wherever it starts, the shapes of that class it touches: see finishBrush.
      const under = shapes.filter((s) => brushable(s) && containsPoint(s.type, s.points, at));
      const target = under.find((s) => s.id === selectedId) ?? under[under.length - 1];
      setPainting({ erase, targetId: !erase && brush.mode === "add" ? (target?.id ?? null) : null });
      setGesture(true);
      setIsDragging(true);
      dragStart.current = at;
      setDrawPoints([at]);
      setCursor(at);
      trackPointer();
      return;
    }
    if (DRAG_TOOLS.includes(tool) || PATH_TOOLS.includes(tool)) {
      setGesture(true);
      setIsDragging(true);
      dragStart.current = pt;
      setDrawPoints([pt]);
      trackPointer();
      return;
    }
    if (tool === "polygon") {
      // Close on the first point or on a double-click; never leave a stray point from a double-click.
      const decision = polygonClick(drawPoints, pt, unit, e.timeStamp, lastClick.current);
      lastClick.current = { time: e.timeStamp, at: pt };
      if (decision === "close") finishPolygon();
      else if (decision === "add") setDrawPoints((pts) => [...pts, pt]);
    }
  }

  function beginEdit(e: React.PointerEvent, shape: LayerShape, handle: Handle | null) {
    e.stopPropagation(); // a press on a shape or its handle is not a press on the background
    onSelect(shape.id);
    setGesture(true);
    const b = shape.bounds ?? { x0: 0, y0: 0, x1: width, y1: height };
    const frame = { x0: b.x0, y0: b.y0, w: b.x1 - b.x0, h: b.y1 - b.y0 };
    setEdit({ id: shape.id, type: shape.type, handle, start: localPoint(e), original: shape.points, preview: shape.points, frame });
    trackPointer();
  }

  function handlePointerMove(e: { clientX: number; clientY: number }) {
    if (edit) {
      const raw = localPoint(e);
      let preview: Point[];
      const { x0, y0, w, h } = edit.frame;
      const into = ([x, y]: Point): Point => [x - x0, y - y0];
      const back = ([x, y]: Point): Point => [x + x0, y + y0];
      if (edit.handle) {
        preview = dragHandle(edit.type, edit.original.map(into), edit.handle, into(raw), w, h).map(back);
      } else {
        const [dx, dy] = clampTranslation(edit.type, edit.original.map(into), raw[0] - edit.start[0], raw[1] - edit.start[1], w, h);
        preview = translatePoints(edit.original, dx, dy);
      }
      setEdit({ ...edit, preview });
      return;
    }

    if (drawTool === "polygon" && drawPoints.length > 0) setCursor(clampToSpace(localPoint(e)));
    if (drawTool === "brush") {
      const at = localPoint(e);
      setCursor(at); // the ring that shows the brush's size
      if (!isDragging) return;
      setDrawPoints((pts) => {
        const last = pts[pts.length - 1];
        const step = Math.max(1.5, brush.size / 10) * unit; // finer than the brush is wide
        return last && Math.hypot(at[0] - last[0], at[1] - last[1]) < step ? pts : [...pts, at];
      });
      return;
    }
    if (!isDragging || !dragStart.current) return;
    const pt = clampToSpace(localPoint(e));

    if (drawTool === "rectangle" || drawTool === "line") {
      setDrawPoints([dragStart.current, pt]);
    } else if (drawTool === "circle") {
      setDrawPoints([dragStart.current, constrainCircleEdge(dragStart.current, pt, width, height)]);
    } else if (PATH_TOOLS.includes(drawTool)) {
      setDrawPoints((pts) => {
        const last = pts[pts.length - 1];
        if (last && Math.hypot(pt[0] - last[0], pt[1] - last[1]) < FREEHAND_MIN_DIST * unit) return pts;
        return [...pts, pt];
      });
    }
  }

  function handlePointerUp() {
    setGesture(false);
    if (edit) {
      // A click (or a jitter of a pixel or two) must not save an edit.
      const changed =
        edit.preview.length !== edit.original.length ||
        edit.preview.some((p, i) => Math.hypot(p[0] - edit.original[i][0], p[1] - edit.original[i][1]) > px(1.5));
      if (changed) onShapeEdit(edit.id, edit.preview);
      setEdit(null);
      return;
    }
    if (!isDragging) return;
    setIsDragging(false);

    if (drawTool === "rectangle" && drawPoints.length === 2) {
      const [[x0, y0], [x1, y1]] = drawPoints;
      const corners: Point[] = [
        [Math.min(x0, x1), Math.min(y0, y1)],
        [Math.max(x0, x1), Math.min(y0, y1)],
        [Math.max(x0, x1), Math.max(y0, y1)],
        [Math.min(x0, x1), Math.max(y0, y1)],
      ];
      if (isDrawnEnough("rectangle", corners, unit)) completeShape("rectangle", corners);
    } else if ((drawTool === "line" || drawTool === "circle") && drawPoints.length === 2) {
      if (isDrawnEnough(drawTool, drawPoints, unit)) completeShape(drawTool, drawPoints);
    } else if ((drawTool === "freehand" || drawTool === "freehand_line") && isDrawnEnough(drawTool, drawPoints, unit)) {
      completeShape(drawTool, drawPoints);
    } else if (drawTool === "brush") {
      finishBrush();
    }
    setDrawPoints([]);
    dragStart.current = null;
  }

  /** Works out what the stroke did to the shapes and hands that over to be saved. */
  function finishBrush() {
    const done = painting;
    setPainting(null);
    if (!done || !onBrush) return;
    const painted: BrushStroke = { path: drawPoints, radius: brushRadius, unit, extent };
    const changes: BrushChange[] = [];
    if (done.erase) {
      changes.push(...eraseChanges(painted));
    } else {
      // Adding: the stroke grows the shape it starts on and every shape of that class it touches (of the
      // active class, when it starts on empty space); shapes it connects become one. Touching nothing, or
      // in the "new" mode, it is a shape of its own.
      const start = shapes.find((s) => s.id === done.targetId);
      const classId = start ? start.class_id : activeClassId;
      const touched =
        brush.mode === "add" ? shapes.filter((s) => s.id !== start?.id && brushable(s) && s.class_id === classId && touchesShape(s, painted)) : [];
      const first = start ?? touched[0];
      if (first) {
        changes.push(...joinChanges(first, touched, painted, brushedType(first.type)));
      } else {
        const points = paintShape(painted);
        if (points) changes.push({ kind: "create", points, like: null });
      }
    }
    if (changes.length > 0) onBrush(changes);
  }

  /** Save the polygon if it has at least 3 distinct points; otherwise keep drawing (nothing is thrown away). */
  function finishPolygon() {
    const points = cleanPolygon(drawPoints, unit);
    if (!isDrawnEnough("polygon", points, unit)) return;
    completeShape("polygon", points);
    setDrawPoints([]);
    setCursor(null);
    lastClick.current = null;
  }

  function handleDoubleClick(e: React.MouseEvent) {
    if (tool === "polygon") {
      finishPolygon(); // usually already closed by the second click (see polygonClick); a no-op then
      return;
    }
    if (tool !== "select") return;

    // Pressing a shape captures the pointer on this layer, so the double-click arrives here rather than on the
    // shape: work out what was hit. On a point of the selected shape it removes that point, on its outline it adds one.
    const shape = shapes.find((s) => s.id === selectedId);
    if (!shape) return;
    const at = localPoint(e);
    const vertex = handlesFor(shape.type, shape.points).find(
      (spot) => spot.handle.kind === "vertex" && Math.hypot(spot.at[0] - at[0], spot.at[1] - at[1]) <= px(11),
    );
    if (vertex && vertex.handle.kind === "vertex") {
      const next = removeVertex(shape.type, shape.points, vertex.handle.index);
      if (next) onShapeEdit(shape.id, next);
      return;
    }
    const next = insertVertex(shape.type, shape.points, at, px(10));
    if (next) onShapeEdit(shape.id, next);
  }

  latest.current = { move: handlePointerMove, up: handlePointerUp, key: handleKey };

  function cancelInProgress() {
    setDrawPoints([]);
    setCursor(null);
    setIsDragging(false);
    setEdit(null);
    setPainting(null);
    setGesture(false);
    lastClick.current = null;
  }

  // Keys: Enter finishes a polygon, Escape abandons whatever is half done, Delete removes the selected shape.
  // The handler is read from `latest` when a key is pressed, not captured when the listener was added:
  // it must see the current shapes and class, or a Delete would act on how things were before the last edit.
  function handleKey(e: KeyboardEvent) {
    if (isTyping(e.target)) return;
    if (e.key === "Enter" && drawTool === "polygon") finishPolygon();
    if (e.key === "Escape") cancelInProgress();
    if (drawTool === "brush" && (e.key === "[" || e.key === "]")) {
      const step = Math.max(2, Math.round(brush.size * 0.2));
      setBrush({ size: brush.size + (e.key === "]" ? step : -step) });
    }
    // While drawing a polygon, Backspace/Delete takes back the last point placed.
    if ((e.key === "Delete" || e.key === "Backspace") && drawTool === "polygon" && drawPoints.length > 0) {
      e.preventDefault();
      setDrawPoints((pts) => pts.slice(0, -1));
      lastClick.current = null;
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && selectedId != null && tool === "select") {
      e.preventDefault();
      onDeleteSelected();
    }
  }

  useEffect(() => {
    if (!active) return;
    const onKeyDown = (e: KeyboardEvent) => latest.current.key(e);
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active]);

  // Abandon in-progress drawing when the surface changes (another patch/image) or another drawing tool is
  // picked -- but not for panning, which only moves the view.
  useEffect(cancelInProgress, [resetKey, drawTool]);

  const stroke = px(2);
  const dash = `${px(6)} ${px(4)}`;
  // What is being drawn is shown in red while it is going to be cut out of the shapes, not kept.
  const tint = shapeMode === "erase" && !!onBrush && AREA_TOOLS.includes(drawTool) ? ERASER : PREVIEW;
  const preview = { stroke: tint, strokeWidth: stroke, fill: "none" } as const;

  return (
    <g
      ref={rootRef}
      onPointerMove={active ? (e) => !tracking.current && handlePointerMove(e) : undefined} // hovering (a polygon's rubber band)
      onPointerLeave={active && tool === "brush" ? () => !tracking.current && setCursor(null) : undefined}
      onDoubleClick={active ? handleDoubleClick : undefined}
      style={{ touchAction: "none", pointerEvents: active ? "auto" : "none" }}
    >
      {/* Empty space: where a drawing tool starts a shape and the Select tool deselects. */}
      {active && (
        <rect
          x={0}
          y={0}
          width={width}
          height={height}
          fill="transparent"
          style={{ cursor: tool === "select" ? "default" : "crosshair" }}
          onPointerDown={handlePointerDown}
        />
      )}

      {background.map((shape) => (
        <ShapeView
          key={`bg-${shape.id}`}
          shape={shape}
          color={classColor(shape.class_id)}
          selected={false}
          px={px}
          muted
          interactive={tool === "select" && !!onBackgroundPress}
          onPress={() => onBackgroundPress?.(shape.id)}
        />
      ))}

      {shapes.map((shape) => (
        <ShapeView
          key={shape.id}
          shape={edit?.id === shape.id ? { ...shape, points: edit.preview } : shape}
          color={classColor(shape.class_id)}
          selected={shape.id === selectedId}
          interactive={tool === "select"}
          px={px}
          fill={fillOpacity}
          onBeginEdit={(e, handle) => beginEdit(e, shape, handle)}
        />
      ))}

      {drawPoints.length > 0 && drawTool === "polygon" && (
        <g pointerEvents="none">
          <polyline points={[...drawPoints, cursor ?? drawPoints[drawPoints.length - 1]].map((p) => p.join(",")).join(" ")} {...preview} strokeDasharray={dash} />
          {drawPoints.map((p, i) => (
            <circle key={i} cx={p[0]} cy={p[1]} r={px(4)} fill={tint} />
          ))}
          {drawPoints.length >= 3 && (
            // Click here (or double-click, or Enter) to close the polygon.
            <circle cx={drawPoints[0][0]} cy={drawPoints[0][1]} r={px(CLOSE_TOLERANCE_PX)} fill="white" fillOpacity={0.35} stroke={tint} strokeWidth={stroke} />
          )}
        </g>
      )}

      {isDragging && drawTool === "rectangle" && drawPoints.length === 2 && (
        <rect
          x={Math.min(drawPoints[0][0], drawPoints[1][0])}
          y={Math.min(drawPoints[0][1], drawPoints[1][1])}
          width={Math.abs(drawPoints[1][0] - drawPoints[0][0])}
          height={Math.abs(drawPoints[1][1] - drawPoints[0][1])}
          fill={tint}
          fillOpacity={0.2}
          stroke={tint}
          strokeWidth={stroke}
          pointerEvents="none"
        />
      )}

      {isDragging && drawTool === "line" && drawPoints.length === 2 && (
        <line x1={drawPoints[0][0]} y1={drawPoints[0][1]} x2={drawPoints[1][0]} y2={drawPoints[1][1]} {...preview} strokeLinecap="round" pointerEvents="none" />
      )}

      {isDragging && drawTool === "circle" && drawPoints.length === 2 && (
        <g pointerEvents="none">
          <circle
            cx={circleGeometry(drawPoints).cx}
            cy={circleGeometry(drawPoints).cy}
            r={circleGeometry(drawPoints).r}
            fill={tint}
            fillOpacity={0.2}
            stroke={tint}
            strokeWidth={stroke}
          />
          <circle cx={drawPoints[0][0]} cy={drawPoints[0][1]} r={px(3)} fill={tint} />
        </g>
      )}

      {isDragging && drawTool === "freehand" && drawPoints.length > 1 && (
        <polyline points={[...drawPoints, drawPoints[0]].map((p) => p.join(",")).join(" ")} {...preview} pointerEvents="none" />
      )}

      {isDragging && drawTool === "freehand_line" && drawPoints.length > 1 && (
        <polyline points={drawPoints.map((p) => p.join(",")).join(" ")} {...preview} strokeLinecap="round" strokeLinejoin="round" pointerEvents="none" />
      )}

      {active && drawTool === "brush" && (
        <BrushPreview
          path={isDragging ? drawPoints : []}
          cursor={cursor}
          radius={brushRadius}
          color={painting?.erase || brush.mode === "erase" ? ERASER : classColor(shapes.find((s) => s.id === painting?.targetId)?.class_id ?? activeClassId)}
          px={px}
        />
      )}
    </g>
  );
}

/** The paint of the stroke so far, and a ring the size of the brush under the pointer. */
function BrushPreview({ path, cursor, radius, color, px }: { path: Point[]; cursor: Point | null; radius: number; color: string; px: (n: number) => number }) {
  return (
    <g pointerEvents="none">
      {path.length === 1 && <circle cx={path[0][0]} cy={path[0][1]} r={radius} fill={color} fillOpacity={0.35} />}
      {path.length > 1 && (
        <polyline
          points={path.map((p) => p.join(",")).join(" ")}
          fill="none"
          stroke={color}
          strokeOpacity={0.35}
          strokeWidth={radius * 2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
      {cursor && (
        <>
          <circle cx={cursor[0]} cy={cursor[1]} r={radius} fill="none" stroke="black" strokeOpacity={0.5} strokeWidth={px(3)} />
          <circle cx={cursor[0]} cy={cursor[1]} r={radius} fill="none" stroke={color} strokeWidth={px(1.5)} />
        </>
      )}
    </g>
  );
}

function ShapeView({
  shape,
  color,
  selected,
  interactive,
  muted = false,
  fill,
  px,
  onBeginEdit,
  onPress,
}: {
  shape: LayerShape;
  color: string;
  selected: boolean;
  /** Only the Select tool can pick shapes up; with a drawing tool active they must not intercept the pointer. */
  interactive: boolean;
  /** Context from the other view: faint, dashed, never edited here. */
  muted?: boolean;
  /** Fill opacity of an ordinary shape; default 0.25. */
  fill?: number;
  px: (n: number) => number;
  onBeginEdit?: (e: React.PointerEvent, handle: Handle | null) => void;
  onPress?: () => void;
}) {
  const { type, points } = shape;
  const strokeWidth = px(selected ? 3 : 2);
  const dashed = muted ? `${px(5)} ${px(4)}` : type === "freehand" || type === "freehand_line" ? `${px(4)} ${px(2)}` : shape.unsure ? `${px(3)} ${px(3)}` : undefined;
  const fillOpacity = muted ? 0.08 : shape.excluded ? 0.06 : (fill ?? 0.25);
  const opacity = muted ? 0.75 : shape.excluded ? 0.4 : 1;
  const wrapper = {
    "data-shape": shape.id,
    onPointerDown: interactive ? (e: React.PointerEvent) => (muted ? (e.stopPropagation(), onPress?.()) : onBeginEdit?.(e, null)) : undefined,
    style: { cursor: interactive ? (muted ? "pointer" : "move") : "default", pointerEvents: interactive ? "auto" : "none" },
  } as const;

  // The handles of the selected shape: drag one to reshape (double-clicking a point removes it; see the layer).
  const handles =
    selected && interactive && !muted
      ? handlesFor(type, points).map((spot: HandleSpot, i) => {
          const [x, y] = spot.at;
          const isEdge = spot.handle.kind === "edge";
          const size = px(isEdge ? 8 : 9);
          const radius = spot.handle.kind === "radius";
          return (
            <g key={i} style={{ cursor: "grab" }} onPointerDown={(e) => onBeginEdit?.(e, spot.handle)}>
              <circle cx={x} cy={y} r={px(11)} fill="transparent" />
              {isEdge ? (
                <rect x={x - size / 2} y={y - size / 2} width={size} height={size} fill={color} stroke="white" strokeWidth={px(1.5)} />
              ) : (
                <circle cx={x} cy={y} r={size / 2} fill={radius ? color : "white"} stroke={radius ? "white" : color} strokeWidth={px(1.5)} />
              )}
            </g>
          );
        })
      : null;

  if (type === "point") {
    const [x, y] = points[0];
    return (
      <g {...wrapper}>
        <circle cx={x} cy={y} r={px(selected ? 8 : 6)} fill={color} fillOpacity={muted ? 0.5 : 1} stroke="white" strokeWidth={px(1.5)} />
      </g>
    );
  }

  if (isLineShape(type)) {
    const path = points.map((p) => p.join(",")).join(" ");
    return (
      <g {...wrapper}>
        {/* a wide invisible stroke: a 2 px line is otherwise nearly impossible to click */}
        <polyline points={path} fill="none" stroke="transparent" strokeWidth={px(14)} strokeLinecap="round" />
        <polyline points={path} fill="none" stroke={color} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={dashed} opacity={opacity} />
        {handles}
      </g>
    );
  }

  if (type === "circle") {
    const { cx, cy, r } = circleGeometry(points);
    return (
      <g {...wrapper}>
        <circle cx={cx} cy={cy} r={r} fill={color} fillOpacity={fillOpacity} stroke={color} strokeWidth={strokeWidth} strokeDasharray={dashed} opacity={opacity} />
        {handles}
      </g>
    );
  }

  return (
    <g {...wrapper}>
      <polygon
        points={points.map((p) => p.join(",")).join(" ")}
        fill={color}
        fillOpacity={fillOpacity}
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={dashed}
        opacity={opacity}
      />
      {handles}
    </g>
  );
}
