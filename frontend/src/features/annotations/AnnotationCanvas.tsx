import { useEffect, useRef, useState } from "react";
import { screenToSvgPoint, type Point } from "../../utils/coordinates";
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
  onDeleteSelected: () => void;
}

const FREEHAND_MIN_DIST = 4;

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
  onDeleteSelected,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [drawPoints, setDrawPoints] = useState<Point[]>([]);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const dragStart = useRef<Point | null>(null);

  const classColor = (id: number | null) => classes.find((c) => c.id === id)?.color_hex ?? "#38bdf8";

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
    if (tool === "rectangle" || tool === "freehand") {
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

  function handlePointerMove(e: React.PointerEvent) {
    const pt = clampToPatch(localPoint(e));
    setCursor(pt);
    if (!isDragging) return;

    if (tool === "rectangle" && dragStart.current) {
      setDrawPoints([dragStart.current, pt]);
    } else if (tool === "freehand") {
      setDrawPoints((pts) => {
        const last = pts[pts.length - 1];
        if (last) {
          const dist = Math.hypot(pt[0] - last[0], pt[1] - last[1]);
          if (dist < FREEHAND_MIN_DIST) return pts;
        }
        return [...pts, pt];
      });
    }
  }

  function handlePointerUp() {
    if (!isDragging) return;
    setIsDragging(false);
    if (tool === "rectangle" && drawPoints.length === 2) {
      const [[x0, y0], [x1, y1]] = drawPoints;
      const rectPoints: Point[] = [
        [Math.min(x0, x1), Math.min(y0, y1)],
        [Math.max(x0, x1), Math.min(y0, y1)],
        [Math.max(x0, x1), Math.max(y0, y1)],
        [Math.min(x0, x1), Math.max(y0, y1)],
      ];
      if (Math.abs(x1 - x0) > 4 && Math.abs(y1 - y0) > 4) {
        onShapeComplete("rectangle", rectPoints);
      }
    } else if (tool === "freehand" && drawPoints.length >= 3) {
      onShapeComplete("freehand", drawPoints);
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
      const [px, py] = points[points.length - 2];
      if (Math.hypot(lx - px, ly - py) < 3) points = points.slice(0, -1);
    }
    if (points.length >= 3) {
      onShapeComplete("polygon", points);
    }
    setDrawPoints([]);
  }

  function handleDoubleClick() {
    if (tool === "polygon") finishPolygon();
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && tool === "polygon") finishPolygon();
    if (e.key === "Escape") setDrawPoints([]);
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
  }, [imageUrl]);

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
        draggable={false}
      />
      <svg
        ref={svgRef}
        viewBox={`0 0 ${patchWidth} ${patchHeight}`}
        width={displayWidth}
        height={displayHeight}
        className="absolute inset-0"
        style={{ cursor: tool === "select" ? "default" : "crosshair" }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onDoubleClick={handleDoubleClick}
      >
        {annotations.map((ann) => (
          <AnnotationShape
            key={ann.id}
            ann={ann}
            color={classColor(ann.class_id)}
            selected={ann.id === selectedId}
            onSelect={() => tool === "select" && onSelect(ann.id)}
          />
        ))}

        {drawPoints.length > 0 && tool === "polygon" && (
          <>
            <polyline
              points={[...drawPoints, cursor ?? drawPoints[drawPoints.length - 1]].map((p) => p.join(",")).join(" ")}
              fill="none"
              stroke="#38bdf8"
              strokeWidth={patchWidth * 0.004}
              strokeDasharray={`${patchWidth * 0.006} ${patchWidth * 0.004}`}
            />
            {drawPoints.map((p, i) => (
              <circle key={i} cx={p[0]} cy={p[1]} r={patchWidth * 0.008} fill="#38bdf8" />
            ))}
          </>
        )}

        {isDragging && tool === "rectangle" && drawPoints.length === 2 && (
          <rect
            x={Math.min(drawPoints[0][0], drawPoints[1][0])}
            y={Math.min(drawPoints[0][1], drawPoints[1][1])}
            width={Math.abs(drawPoints[1][0] - drawPoints[0][0])}
            height={Math.abs(drawPoints[1][1] - drawPoints[0][1])}
            fill="#38bdf8"
            fillOpacity={0.2}
            stroke="#38bdf8"
            strokeWidth={patchWidth * 0.004}
          />
        )}

        {isDragging && tool === "freehand" && drawPoints.length > 1 && (
          <polyline
            points={drawPoints.map((p) => p.join(",")).join(" ")}
            fill="none"
            stroke="#38bdf8"
            strokeWidth={patchWidth * 0.004}
          />
        )}
      </svg>
    </div>
  );
}

function AnnotationShape({
  ann,
  color,
  selected,
  onSelect,
}: {
  ann: GeometryAnnotation;
  color: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const pts = ann.coordinates_patch_local;
  const strokeWidth = selected ? 3 : 2;

  if (ann.type === "point") {
    const [x, y] = pts[0];
    return <circle cx={x} cy={y} r={6} fill={color} stroke="white" strokeWidth={1.5} onClick={onSelect} style={{ cursor: "pointer" }} />;
  }

  return (
    <g onClick={onSelect} style={{ cursor: "pointer" }}>
      <polygon
        points={pts.map((p) => p.join(",")).join(" ")}
        fill={color}
        fillOpacity={ann.excluded ? 0.06 : 0.25}
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={ann.type === "freehand" ? "4 2" : ann.unsure ? "3 3" : undefined}
      />
      {selected &&
        pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={4} fill="white" stroke={color} strokeWidth={1.5} />)}
    </g>
  );
}
