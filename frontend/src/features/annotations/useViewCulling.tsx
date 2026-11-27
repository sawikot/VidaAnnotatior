import { useEffect, useMemo, useState } from "react";
import type { AnnotationClass } from "../../types/api";
import { cullShapes, indexShapes, type Box, type CullableShape, type MarkerSet } from "../../utils/viewCulling";

/** How far past the screen shapes are prepared, as a share of its size, so a small pan needs no new work. */
const PADDING = 0.5;
const RESCALE_AT = 1.5;

const inside = (inner: Box, outer: Box) => inner.x0 >= outer.x0 && inner.y0 >= outer.y0 && inner.x1 <= outer.x1 && inner.y1 <= outer.y1;

/** The shapes to draw for the current view. The viewer reports every animation frame; this reworks them only
 * when the view leaves the padded area or the zoom changes noticeably. */
export function useViewCulling<T extends CullableShape>(shapes: T[], view: Box | null, scale: number, keepId: number | null) {
  const [region, setRegion] = useState<{ box: Box; scale: number } | null>(null);
  let current = region;
  if (view) {
    const ratio = region ? Math.max(scale / region.scale, region.scale / scale) : Infinity;
    if (!region || ratio > RESCALE_AT || !inside(view, region.box)) {
      const padX = (view.x1 - view.x0) * PADDING;
      const padY = (view.y1 - view.y0) * PADDING;
      current = { box: { x0: view.x0 - padX, y0: view.y0 - padY, x1: view.x1 + padX, y1: view.y1 + padY }, scale };
      setRegion(current); // adjusting state to a prop, during render
    }
  }
  const indexed = useMemo(() => indexShapes(shapes), [shapes]);
  return useMemo(() => cullShapes(indexed, current?.box ?? null, current?.scale ?? scale, keepId), [indexed, current, keepId]); // eslint-disable-line react-hooks/exhaustive-deps
}

/** The most canvas pixels on a side for the dots' picture; past it they are drawn coarser, never skipped. */
const MAX_SIDE = 4096;

/** Dots standing in for shapes too numerous to draw in full at this zoom. They are painted once into a
 * picture that the viewer then moves and scales like the slide: tens of thousands of dots as SVG would be
 * repainted on every frame of a pan (seconds each), a picture costs next to nothing. */
export function ShapeMarkers({ markers, classes, size }: { markers: MarkerSet[]; classes: AnnotationClass[]; size: number }) {
  const picture = useMemo(() => paintMarkers(markers, classes, size), [markers, classes, size]);
  // The old picture stays up until the new one is ready, so the dots do not blink when the zoom changes.
  const [shown, setShown] = useState<{ url: string; x0: number; y0: number; w: number; h: number } | null>(null);
  useEffect(() => {
    let live = true;
    if (!picture) setShown(null);
    picture?.blob.then((b) => {
      if (!live || !b) return;
      const { x0, y0, w, h } = picture;
      const url = URL.createObjectURL(b);
      setShown((prev) => {
        // The picture it replaces is let go once the new one is up (never on unmount: React's development
        // double-run of effects would release the one still on screen).
        if (prev) setTimeout(() => URL.revokeObjectURL(prev.url), 1000);
        return { url, x0, y0, w, h };
      });
    });
    return () => {
      live = false;
    };
  }, [picture]);
  if (!shown) return null;
  const { url, x0, y0, w, h } = shown;
  return <image href={url} x={x0} y={y0} width={w} height={h} preserveAspectRatio="none" pointerEvents="none" style={{ imageRendering: "pixelated" }} />;
}

function paintMarkers(markers: MarkerSet[], classes: AnnotationClass[], size: number) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const m of markers) {
    for (let i = 0; i < m.points.length; i += 2) {
      const x = m.points[i], y = m.points[i + 1];
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
    }
  }
  if (x0 === Infinity) return null;
  x0 -= size; y0 -= size; x1 += size; y1 += size;
  const w = x1 - x0, h = y1 - y0;
  const ppu = Math.min(MARKER_PX_PER_SIZE / size, MAX_SIDE / w, MAX_SIDE / h); // canvas px per unit
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.ceil(w * ppu));
  canvas.height = Math.max(1, Math.ceil(h * ppu));
  const g = canvas.getContext("2d");
  if (!g) return null;
  const side = Math.max(1, size * ppu);
  for (const m of markers) {
    g.fillStyle = classes.find((c) => c.id === m.classId)?.color_hex ?? "#94a3b8";
    for (let i = 0; i < m.points.length; i += 2) g.fillRect((m.points[i] - x0) * ppu - side / 2, (m.points[i + 1] - y0) * ppu - side / 2, side, side);
  }
  const blob = new Promise<Blob | null>((resolve) => canvas.toBlob(resolve));
  return { x0, y0, w, h, blob };
}

const MARKER_PX_PER_SIZE = 4; // a dot is 4 screen px (viewCulling's MARKER_PX): one canvas px per screen px
