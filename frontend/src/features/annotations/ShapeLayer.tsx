import { useEffect, useRef, useState } from "react";
import { screenToSvgPoint, type Point } from "../../utils/coordinates";
import {
  circleGeometry,
  clampTranslation,
  constrainCircleEdge,
  isDrawnEnough,
  isLineShape,
  translatePoints,
} from "../../utils/shapes";
import { dragHandle, handlesFor, insertVertex, removeVertex, type Handle, type HandleSpot } from "../../utils/shapeEdit";
import type { AnnotationTool } from "../../stores/annotationStore";
import type { AnnotationClass, GeometryType } from "../../types/api";

/** What the layer needs to know about a shape to draw it. */
export interface LayerShape {
  id: number;
  type: GeometryType;
  points: Point[];
  class_id: number | null;
  unsure: boolean;
  excluded: boolean;
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
  /** True while a gesture that started on this layer is in progress, so a viewer underneath can hold still. */
  gestureRef?: React.MutableRefObject<boolean>;
  /** Anything that changes when in-progress drawing should be abandoned (another image, another patch). */
  resetKey?: unknown;
}

const FREEHAND_MIN_DIST = 4; // screen px
const PREVIEW = "#38bdf8";
const DRAG_TOOLS: AnnotationTool[] = ["rectangle", "line", "circle"];
const PATH_TOOLS: AnnotationTool[] = ["freehand", "freehand_line"];

/** A shape being moved (no handle) or reshaped (a handle), previewed before it is saved. */
interface EditState {
  id: number;
  type: GeometryType;
  handle: Handle | null;
  start: Point;
  original: Point[];
  preview: Point[];
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
  gestureRef,
  resetKey,
}: Props) {
  const rootRef = useRef<SVGGElement>(null);
  const [drawPoints, setDrawPoints] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [edit, setEdit] = useState<EditState | null>(null);
  const dragStart = useRef<Point | null>(null);

  const active = tool !== "pan"; // "pan" leaves the pointer to whatever is underneath
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
    if (DRAG_TOOLS.includes(tool) || PATH_TOOLS.includes(tool)) {
      setGesture(true);
      setIsDragging(true);
      dragStart.current = pt;
      setDrawPoints([pt]);
      trackPointer();
      return;
    }
    if (tool === "polygon") {
      setDrawPoints((pts) => [...pts, pt]);
    }
  }

  function beginEdit(e: React.PointerEvent, shape: LayerShape, handle: Handle | null) {
    e.stopPropagation(); // a press on a shape or its handle is not a press on the background
    onSelect(shape.id);
    setGesture(true);
    setEdit({ id: shape.id, type: shape.type, handle, start: localPoint(e), original: shape.points, preview: shape.points });
    trackPointer();
  }

  function handlePointerMove(e: { clientX: number; clientY: number }) {
    if (edit) {
      const raw = localPoint(e);
      let preview: Point[];
      if (edit.handle) {
        preview = dragHandle(edit.type, edit.original, edit.handle, raw, width, height);
      } else {
        const [dx, dy] = clampTranslation(edit.type, edit.original, raw[0] - edit.start[0], raw[1] - edit.start[1], width, height);
        preview = translatePoints(edit.original, dx, dy);
      }
      setEdit({ ...edit, preview });
      return;
    }

    if (tool === "polygon" && drawPoints.length > 0) setCursor(clampToSpace(localPoint(e)));
    if (!isDragging || !dragStart.current) return;
    const pt = clampToSpace(localPoint(e));

    if (tool === "rectangle" || tool === "line") {
      setDrawPoints([dragStart.current, pt]);
    } else if (tool === "circle") {
      setDrawPoints([dragStart.current, constrainCircleEdge(dragStart.current, pt, width, height)]);
    } else if (PATH_TOOLS.includes(tool)) {
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

    if (tool === "rectangle" && drawPoints.length === 2) {
      const [[x0, y0], [x1, y1]] = drawPoints;
      const corners: Point[] = [
        [Math.min(x0, x1), Math.min(y0, y1)],
        [Math.max(x0, x1), Math.min(y0, y1)],
        [Math.max(x0, x1), Math.max(y0, y1)],
        [Math.min(x0, x1), Math.max(y0, y1)],
      ];
      if (isDrawnEnough("rectangle", corners, unit)) onShapeComplete("rectangle", corners);
    } else if ((tool === "line" || tool === "circle") && drawPoints.length === 2) {
      if (isDrawnEnough(tool, drawPoints, unit)) onShapeComplete(tool, drawPoints);
    } else if ((tool === "freehand" || tool === "freehand_line") && isDrawnEnough(tool, drawPoints, unit)) {
      onShapeComplete(tool, drawPoints);
    }
    setDrawPoints([]);
    dragStart.current = null;
  }

  function finishPolygon() {
    // A double-click to finish fires two click events (each adding a point via handlePointerDown)
    // before the dblclick handler runs, leaving a near-duplicate final vertex at the same
    // location -- drop it before committing the shape.
    let points = drawPoints;
    if (points.length >= 2) {
      const [lx, ly] = points[points.length - 1];
      const [ax, ay] = points[points.length - 2];
      if (Math.hypot(lx - ax, ly - ay) < 3 * unit) points = points.slice(0, -1);
    }
    if (isDrawnEnough("polygon", points, unit)) onShapeComplete("polygon", points);
    setDrawPoints([]);
  }

  function handleDoubleClick(e: React.MouseEvent) {
    if (tool === "polygon") {
      finishPolygon();
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
    setIsDragging(false);
    setEdit(null);
    setGesture(false);
  }

  // Keys: Enter finishes a polygon, Escape abandons whatever is half done, Delete removes the selected shape.
  // The handler is read from `latest` when a key is pressed, not captured when the listener was added:
  // it must see the current shapes and class, or a Delete would act on how things were before the last edit.
  function handleKey(e: KeyboardEvent) {
    if (isTyping(e.target)) return;
    if (e.key === "Enter" && tool === "polygon") finishPolygon();
    if (e.key === "Escape") cancelInProgress();
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

  // Abandon in-progress drawing when the surface changes (another patch/image) or the tool does.
  useEffect(cancelInProgress, [resetKey, tool]);

  const stroke = px(2);
  const dash = `${px(6)} ${px(4)}`;
  const preview = { stroke: PREVIEW, strokeWidth: stroke, fill: "none" } as const;

  return (
    <g
      ref={rootRef}
      onPointerMove={active ? (e) => !tracking.current && handlePointerMove(e) : undefined} // hovering (a polygon's rubber band)
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
          onBeginEdit={(e, handle) => beginEdit(e, shape, handle)}
        />
      ))}

      {drawPoints.length > 0 && tool === "polygon" && (
        <>
          <polyline points={[...drawPoints, cursor ?? drawPoints[drawPoints.length - 1]].map((p) => p.join(",")).join(" ")} {...preview} strokeDasharray={dash} />
          {drawPoints.map((p, i) => (
            <circle key={i} cx={p[0]} cy={p[1]} r={px(4)} fill={PREVIEW} />
          ))}
        </>
      )}

      {isDragging && tool === "rectangle" && drawPoints.length === 2 && (
        <rect
          x={Math.min(drawPoints[0][0], drawPoints[1][0])}
          y={Math.min(drawPoints[0][1], drawPoints[1][1])}
          width={Math.abs(drawPoints[1][0] - drawPoints[0][0])}
          height={Math.abs(drawPoints[1][1] - drawPoints[0][1])}
          fill={PREVIEW}
          fillOpacity={0.2}
          stroke={PREVIEW}
          strokeWidth={stroke}
          pointerEvents="none"
        />
      )}

      {isDragging && tool === "line" && drawPoints.length === 2 && (
        <line x1={drawPoints[0][0]} y1={drawPoints[0][1]} x2={drawPoints[1][0]} y2={drawPoints[1][1]} {...preview} strokeLinecap="round" pointerEvents="none" />
      )}

      {isDragging && tool === "circle" && drawPoints.length === 2 && (
        <g pointerEvents="none">
          <circle
            cx={circleGeometry(drawPoints).cx}
            cy={circleGeometry(drawPoints).cy}
            r={circleGeometry(drawPoints).r}
            fill={PREVIEW}
            fillOpacity={0.2}
            stroke={PREVIEW}
            strokeWidth={stroke}
          />
          <circle cx={drawPoints[0][0]} cy={drawPoints[0][1]} r={px(3)} fill={PREVIEW} />
        </g>
      )}

      {isDragging && tool === "freehand" && drawPoints.length > 1 && (
        <polyline points={[...drawPoints, drawPoints[0]].map((p) => p.join(",")).join(" ")} {...preview} pointerEvents="none" />
      )}

      {isDragging && tool === "freehand_line" && drawPoints.length > 1 && (
        <polyline points={drawPoints.map((p) => p.join(",")).join(" ")} {...preview} strokeLinecap="round" strokeLinejoin="round" pointerEvents="none" />
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
  px: (n: number) => number;
  onBeginEdit?: (e: React.PointerEvent, handle: Handle | null) => void;
  onPress?: () => void;
}) {
  const { type, points } = shape;
  const strokeWidth = px(selected ? 3 : 2);
  const dashed = muted ? `${px(5)} ${px(4)}` : type === "freehand" || type === "freehand_line" ? `${px(4)} ${px(2)}` : shape.unsure ? `${px(3)} ${px(3)}` : undefined;
  const fillOpacity = muted ? 0.08 : shape.excluded ? 0.06 : 0.25;
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
