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
import type { AnnotationClass, GeometryAnnotation, GeometryType } from "../../types/api";

interface Props {
  imageUrl: string;
  patchWidth: number;
  patchHeight: number;
  tool: AnnotationTool;
  zoom: number;
  annotations: GeometryAnnotation[];
  classes: AnnotationClass[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onShapeComplete: (type: GeometryType, points: Point[]) => void;
  /** Called with a shape's new points when the Select tool finishes moving or reshaping it. */
  onShapeEdit: (id: number, points: Point[]) => void;
  onDeleteSelected: () => void;
}

const FREEHAND_MIN_DIST = 4;
const PREVIEW = "#38bdf8";

/** Tools drawn by pressing at the first point and dragging to the second. */
const DRAG_TOOLS: AnnotationTool[] = ["rectangle", "line", "circle"];
/** Tools drawn by dragging along a path. */
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

export function AnnotationCanvas({
  imageUrl,
  patchWidth,
  patchHeight,
  tool,
  zoom,
  annotations,
  classes,
  selectedId,
  onSelect,
  onShapeComplete,
  onShapeEdit,
  onDeleteSelected,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [drawPoints, setDrawPoints] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [edit, setEdit] = useState<EditState | null>(null);
  const dragStart = useRef<Point | null>(null);

  // Handles, strokes and dashes are sized in *screen* pixels: the SVG is scaled with the image, so a
  // fixed size in patch pixels would balloon when a small image is enlarged to fit the window.
  const px = (n: number) => n / zoom;

  const classColor = (id: number | null) => classes.find((c) => c.id === id)?.color_hex ?? PREVIEW;

  function localPoint(e: React.PointerEvent | React.MouseEvent): Point {
    if (!svgRef.current) return [0, 0];
    return screenToSvgPoint(svgRef.current, e.clientX, e.clientY);
  }

  function clampToPatch([x, y]: Point): Point {
    return [Math.max(0, Math.min(patchWidth, x)), Math.max(0, Math.min(patchHeight, y))];
  }

  function handlePointerDown(e: React.PointerEvent) {
    const pt = clampToPatch(localPoint(e));

    if (tool === "select") {
      onSelect(null);
      return;
    }
    if (tool === "point") {
      onShapeComplete("point", [pt]);
      return;
    }
    if (DRAG_TOOLS.includes(tool) || PATH_TOOLS.includes(tool)) {
      setIsDragging(true);
      dragStart.current = pt;
      setDrawPoints([pt]);
      (e.target as Element).setPointerCapture?.(e.pointerId);
      return;
    }
    if (tool === "polygon") {
      setDrawPoints((pts) => [...pts, pt]);
    }
  }

  function beginEdit(e: React.PointerEvent, ann: GeometryAnnotation, handle: Handle | null) {
    e.stopPropagation(); // a press on a shape or its handle is not a press on the background
    onSelect(ann.id);
    const original = ann.coordinates_patch_local as Point[];
    setEdit({ id: ann.id, type: ann.type, handle, start: localPoint(e), original, preview: original });
    svgRef.current?.setPointerCapture?.(e.pointerId);
  }

  /** Double-click on a point removes it (never below the shape's minimum). */
  function removeVertexAt(ann: GeometryAnnotation, index: number) {
    const next = removeVertex(ann.type, ann.coordinates_patch_local as Point[], index);
    if (next) onShapeEdit(ann.id, next);
  }

  function handlePointerMove(e: React.PointerEvent) {
    if (edit) {
      const raw = localPoint(e);
      let preview: Point[];
      if (edit.handle) {
        preview = dragHandle(edit.type, edit.original, edit.handle, raw, patchWidth, patchHeight);
      } else {
        const [dx, dy] = clampTranslation(edit.type, edit.original, raw[0] - edit.start[0], raw[1] - edit.start[1], patchWidth, patchHeight);
        preview = translatePoints(edit.original, dx, dy);
      }
      setEdit({ ...edit, preview });
      return;
    }

    const pt = clampToPatch(localPoint(e));
    setCursor(pt);
    if (!isDragging || !dragStart.current) return;

    if (tool === "rectangle" || tool === "line") {
      setDrawPoints([dragStart.current, pt]);
    } else if (tool === "circle") {
      setDrawPoints([dragStart.current, constrainCircleEdge(dragStart.current, pt, patchWidth, patchHeight)]);
    } else if (PATH_TOOLS.includes(tool)) {
      setDrawPoints((pts) => {
        const last = pts[pts.length - 1];
        if (last && Math.hypot(pt[0] - last[0], pt[1] - last[1]) < FREEHAND_MIN_DIST) return pts;
        return [...pts, pt];
      });
    }
  }

  function handlePointerUp() {
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
      if (isDrawnEnough("rectangle", corners)) onShapeComplete("rectangle", corners);
    } else if ((tool === "line" || tool === "circle") && drawPoints.length === 2) {
      if (isDrawnEnough(tool, drawPoints)) onShapeComplete(tool, drawPoints);
    } else if ((tool === "freehand" || tool === "freehand_line") && isDrawnEnough(tool, drawPoints)) {
      onShapeComplete(tool, drawPoints);
    }
    setDrawPoints([]);
    dragStart.current = null;
  }

  function finishPolygon() {
    // A double-click to finish fires two click events (each adding a point via
    // handlePointerDown) before the dblclick handler runs, leaving a near-duplicate
    // final vertex at the same location -- drop it before committing the shape.
    let points = drawPoints;
    if (points.length >= 2) {
      const [lx, ly] = points[points.length - 1];
      const [px_, py_] = points[points.length - 2];
      if (Math.hypot(lx - px_, ly - py_) < 3) points = points.slice(0, -1);
    }
    if (isDrawnEnough("polygon", points)) {
      onShapeComplete("polygon", points);
    }
    setDrawPoints([]);
  }

  function handleDoubleClick(e: React.MouseEvent) {
    if (tool === "polygon") {
      finishPolygon();
      return;
    }
    if (tool !== "select") return;

    // Pressing a shape captures the pointer on the SVG, so the double-click arrives here rather than on the
    // shape: work out what was hit. On a point of the selected shape it removes that point, on its outline it adds one.
    const ann = annotations.find((a) => a.id === selectedId);
    if (!ann) return;
    const at = localPoint(e);
    const points = ann.coordinates_patch_local as Point[];
    const vertex = handlesFor(ann.type, points).find(
      (spot) => spot.handle.kind === "vertex" && Math.hypot(spot.at[0] - at[0], spot.at[1] - at[1]) <= px(11),
    );
    if (vertex && vertex.handle.kind === "vertex") {
      removeVertexAt(ann, vertex.handle.index);
      return;
    }
    const next = insertVertex(ann.type, points, at, px(10));
    if (next) onShapeEdit(ann.id, next);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && tool === "polygon") finishPolygon();
    if (e.key === "Escape") {
      setDrawPoints([]);
      setIsDragging(false);
      setEdit(null);
    }
    if ((e.key === "Delete" || e.key === "Backspace") && selectedId != null && tool === "select") {
      onDeleteSelected();
    }
  }

  const displayWidth = patchWidth * zoom;
  const displayHeight = patchHeight * zoom;
  const wrapperRef = useRef<HTMLDivElement>(null);

  // The wrapper must hold keyboard focus for Enter/Escape/Delete to reach
  // handleKeyDown -- clicking an SVG child does not move focus to an ancestor
  // on its own, so we claim it explicitly on mount and on every pointer down.
  useEffect(() => {
    wrapperRef.current?.focus();
  }, []);

  // Discard any in-progress (unfinished) shape when the underlying patch image
  // changes -- e.g. the user navigated away mid-draw.
  useEffect(() => {
    setDrawPoints([]);
    setIsDragging(false);
    setEdit(null);
  }, [imageUrl]);

  // Switching tool abandons whatever was half drawn with the previous one.
  useEffect(() => {
    setDrawPoints([]);
    setIsDragging(false);
    setEdit(null);
  }, [tool]);

  const stroke = px(2);
  const dash = `${px(6)} ${px(4)}`;
  const preview = { stroke: PREVIEW, strokeWidth: stroke, fill: "none" } as const;

  return (
    <div
      ref={wrapperRef}
      className="relative inline-block outline-none"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      onPointerDownCapture={() => wrapperRef.current?.focus()}
    >
      <img
        src={imageUrl}
        alt="Patch"
        width={displayWidth}
        height={displayHeight}
        className="block select-none"
        style={{ imageRendering: zoom >= 3 ? "pixelated" : "auto" }}
        draggable={false}
      />
      <svg
        ref={svgRef}
        viewBox={`0 0 ${patchWidth} ${patchHeight}`}
        width={displayWidth}
        height={displayHeight}
        className="absolute inset-0"
        style={{ cursor: tool === "select" ? "default" : "crosshair", touchAction: "none" }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onDoubleClick={handleDoubleClick}
      >
        {annotations.map((ann) => (
          <AnnotationShape
            key={ann.id}
            ann={ann}
            points={edit?.id === ann.id ? edit.preview : (ann.coordinates_patch_local as Point[])}
            color={classColor(ann.class_id)}
            selected={ann.id === selectedId}
            interactive={tool === "select"}
            px={px}
            onBeginEdit={(e, handle) => beginEdit(e, ann, handle)}
          />
        ))}

        {drawPoints.length > 0 && tool === "polygon" && (
          <>
            <polyline
              points={[...drawPoints, cursor ?? drawPoints[drawPoints.length - 1]].map((p) => p.join(",")).join(" ")}
              {...preview}
              strokeDasharray={dash}
            />
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
          />
        )}

        {isDragging && tool === "line" && drawPoints.length === 2 && (
          <line x1={drawPoints[0][0]} y1={drawPoints[0][1]} x2={drawPoints[1][0]} y2={drawPoints[1][1]} {...preview} strokeLinecap="round" />
        )}

        {isDragging && tool === "circle" && drawPoints.length === 2 && (
          <>
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
          </>
        )}

        {isDragging && tool === "freehand" && drawPoints.length > 1 && (
          <polyline points={[...drawPoints, drawPoints[0]].map((p) => p.join(",")).join(" ")} {...preview} />
        )}

        {isDragging && tool === "freehand_line" && drawPoints.length > 1 && (
          <polyline points={drawPoints.map((p) => p.join(",")).join(" ")} {...preview} strokeLinecap="round" strokeLinejoin="round" />
        )}
      </svg>
    </div>
  );
}

function AnnotationShape({
  ann,
  points,
  color,
  selected,
  interactive,
  px,
  onBeginEdit,
}: {
  ann: GeometryAnnotation;
  points: Point[];
  color: string;
  selected: boolean;
  /** Only the Select tool can pick shapes up; with a drawing tool active they must not intercept the pointer. */
  interactive: boolean;
  px: (n: number) => number;
  onBeginEdit: (e: React.PointerEvent, handle: Handle | null) => void;
}) {
  const strokeWidth = px(selected ? 3 : 2);
  const dashed = ann.type === "freehand" || ann.type === "freehand_line" ? `${px(4)} ${px(2)}` : ann.unsure ? `${px(3)} ${px(3)}` : undefined;
  const wrapper = {
    onPointerDown: interactive ? (e: React.PointerEvent) => onBeginEdit(e, null) : undefined,
    style: { cursor: interactive ? "move" : "default", pointerEvents: interactive ? "auto" : "none" },
  } as const;

  // The handles of the selected shape: drag one to reshape (double-clicking a point removes it; see the canvas).
  const handles =
    selected && interactive
      ? handlesFor(ann.type, points).map((spot: HandleSpot, i) => {
          const [x, y] = spot.at;
          const isEdge = spot.handle.kind === "edge";
          const size = px(isEdge ? 8 : 9);
          return (
            <g
              key={i}
              style={{ cursor: "grab" }}
              onPointerDown={(e) => onBeginEdit(e, spot.handle)}
            >
              <circle cx={x} cy={y} r={px(11)} fill="transparent" />
              {isEdge ? (
                <rect x={x - size / 2} y={y - size / 2} width={size} height={size} fill={color} stroke="white" strokeWidth={px(1.5)} />
              ) : (
                <circle cx={x} cy={y} r={size / 2} fill={spot.handle.kind === "radius" ? color : "white"} stroke={spot.handle.kind === "radius" ? "white" : color} strokeWidth={px(1.5)} />
              )}
            </g>
          );
        })
      : null;

  if (ann.type === "point") {
    const [x, y] = points[0];
    return (
      <g {...wrapper}>
        <circle cx={x} cy={y} r={px(selected ? 8 : 6)} fill={color} stroke="white" strokeWidth={px(1.5)} />
      </g>
    );
  }

  if (isLineShape(ann.type)) {
    const path = points.map((p) => p.join(",")).join(" ");
    return (
      <g {...wrapper}>
        {/* a wide invisible stroke: a 2 px line is otherwise nearly impossible to click */}
        <polyline points={path} fill="none" stroke="transparent" strokeWidth={px(14)} strokeLinecap="round" />
        <polyline
          points={path}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={dashed}
          opacity={ann.excluded ? 0.4 : 1}
        />
        {handles}
      </g>
    );
  }

  if (ann.type === "circle") {
    const { cx, cy, r } = circleGeometry(points);
    return (
      <g {...wrapper}>
        <circle cx={cx} cy={cy} r={r} fill={color} fillOpacity={ann.excluded ? 0.06 : 0.25} stroke={color} strokeWidth={strokeWidth} strokeDasharray={dashed} />
        {handles}
      </g>
    );
  }

  return (
    <g {...wrapper}>
      <polygon
        points={points.map((p) => p.join(",")).join(" ")}
        fill={color}
        fillOpacity={ann.excluded ? 0.06 : 0.25}
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={dashed}
      />
      {handles}
    </g>
  );
}
