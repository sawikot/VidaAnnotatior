import { memo, useEffect, useMemo, useRef, useState } from "react";
import { regionPreviewUrl } from "../../services/api";
import type { AnnotationClass, GeometryAnnotation } from "../../types/api";
import { previewFrame, thinPoints, visibleRange } from "../../utils/annotationPreview";
import type { Point } from "../../utils/coordinates";
import { circleGeometry, isLineShape } from "../../utils/shapes";

const ROW_HEIGHT = 60;
const MAX_LIST_HEIGHT = 420;
const PREVIEW_PX = 96; // sharp on high-density screens at the 48 px it is shown
const LOAD_DELAY_MS = 120; // a row scrolled straight past never asks the server for its picture

interface Props {
  annotations: GeometryAnnotation[];
  classes: AnnotationClass[];
  slideId: number;
  slideWidth: number;
  slideHeight: number;
  imageVersion?: string;
  selectedId: number | null;
  onPick: (annotation: GeometryAnnotation) => void;
}

/** A list of annotations with a picture of each. Only the rows on screen exist, whatever the length of the list. */
export const AnnotationList = memo(function AnnotationList({ annotations, classes, slideId, slideWidth, slideHeight, imageVersion, selectedId, onPick }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const height = Math.min(annotations.length * ROW_HEIGHT, MAX_LIST_HEIGHT);
  const { start, end } = visibleRange(scrollTop, height, ROW_HEIGHT, annotations.length);
  const selectedIndex = useMemo(() => (selectedId == null ? -1 : annotations.findIndex((a) => a.id === selectedId)), [annotations, selectedId]);

  // A shape picked on the slide is brought into view here.
  useEffect(() => {
    const el = scroller.current;
    if (!el || selectedIndex < 0) return;
    const top = selectedIndex * ROW_HEIGHT;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (top + ROW_HEIGHT > el.scrollTop + el.clientHeight) el.scrollTop = top + ROW_HEIGHT - el.clientHeight;
  }, [selectedIndex]);

  return (
    <div ref={scroller} className="overflow-y-auto" style={{ height }} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}>
      <div className="relative" style={{ height: annotations.length * ROW_HEIGHT }}>
        {annotations.slice(start, end).map((a, i) => (
          <Row
            key={a.id}
            annotation={a}
            top={(start + i) * ROW_HEIGHT}
            cls={classes.find((c) => c.id === a.class_id)}
            slideId={slideId}
            slideWidth={slideWidth}
            slideHeight={slideHeight}
            imageVersion={imageVersion}
            selected={a.id === selectedId}
            onPick={onPick}
          />
        ))}
      </div>
    </div>
  );
});

const Row = memo(function Row({
  annotation: a,
  top,
  cls,
  slideId,
  slideWidth,
  slideHeight,
  imageVersion,
  selected,
  onPick,
}: {
  annotation: GeometryAnnotation;
  top: number;
  cls?: AnnotationClass;
  slideId: number;
  slideWidth: number;
  slideHeight: number;
  imageVersion?: string;
  selected: boolean;
  onPick: (annotation: GeometryAnnotation) => void;
}) {
  const color = cls?.color_hex ?? "#94a3b8";
  const points = a.coordinates_level0 as Point[];
  const frame = useMemo(() => previewFrame(a.type, points, slideWidth, slideHeight), [a.type, points, slideWidth, slideHeight]);
  const url = regionPreviewUrl(slideId, frame.x, frame.y, frame.side, PREVIEW_PX, imageVersion);
  const [loadUrl, setLoadUrl] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setLoadUrl(url), LOAD_DELAY_MS);
    return () => clearTimeout(timer);
  }, [url]);

  return (
    <div className="absolute left-0 right-1 pb-1" style={{ top, height: ROW_HEIGHT }}>
      <button
        type="button"
        onClick={() => onPick(a)}
        className={`w-full h-full flex items-center gap-space-sm px-1 rounded bg-surface-container-lowest shadow-sm text-left ${selected ? "ring-1 ring-primary" : ""}`}
      >
        <span className="relative w-12 h-12 shrink-0 rounded overflow-hidden bg-slate-800">
          {loadUrl && <img src={loadUrl} alt="" width={48} height={48} decoding="async" className="absolute inset-0 w-full h-full object-cover" />}
          <svg viewBox={`${frame.x} ${frame.y} ${frame.side} ${frame.side}`} className="absolute inset-0 w-full h-full" aria-hidden>
            <Outline annotation={a} color={color} side={frame.side} />
          </svg>
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5 text-label-md">
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: color }} />
            <span className="truncate">{cls?.name ?? "Unclassed"} #{a.id}</span>
          </span>
          <span className="block text-label-sm text-on-surface-variant capitalize">{a.type.replace("_", " ")}</span>
        </span>
      </button>
    </div>
  );
});

function Outline({ annotation: a, color, side }: { annotation: GeometryAnnotation; color: string; side: number }) {
  const points = a.coordinates_level0 as Point[];
  const line = { stroke: color, strokeWidth: 1.5, vectorEffect: "non-scaling-stroke" as const };
  if (a.type === "point") return <circle cx={points[0][0]} cy={points[0][1]} r={side * 0.07} fill={color} stroke="white" strokeWidth={1} vectorEffect="non-scaling-stroke" />;
  if (a.type === "circle") {
    const { cx, cy, r } = circleGeometry(points);
    return <circle cx={cx} cy={cy} r={r} fill={color} fillOpacity={0.15} {...line} />;
  }
  const path = thinPoints(points).map((p) => p.join(",")).join(" ");
  if (isLineShape(a.type)) return <polyline points={path} fill="none" {...line} />;
  return <polygon points={path} fill={color} fillOpacity={0.15} {...line} />;
}
