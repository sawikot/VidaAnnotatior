import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Card } from "../components/primitives";
import { WsiViewer, type ViewportBbox } from "../features/viewer/WsiViewer";
import { PatchGridOverlay } from "../features/viewer/PatchGridOverlay";
import { getConfig, getSlide, listSlideAnnotations, tissueMaskUrl } from "../services/api";
import type { ConfigVersion, GeometryAnnotation, Slide } from "../types/api";
import { circleGeometry, isLineShape, shapeArea } from "../utils/shapes";

import { useImageProjectRedirect } from "../features/images/useImageProjectRedirect";

export function FullOverviewPage() {
  const { projectId, slideId } = useParams();
  const sid = Number(slideId);
  useImageProjectRedirect(Number(projectId));

  const [slide, setSlide] = useState<Slide | null>(null);
  const [config, setConfig] = useState<ConfigVersion | null>(null);
  const [annotations, setAnnotations] = useState<GeometryAnnotation[]>([]);
  const [bbox, setBbox] = useState<ViewportBbox | null>(null);
  const [showAnnotations, setShowAnnotations] = useState(true);
  const [showGrid, setShowGrid] = useState(false);
  const [showMask, setShowMask] = useState(false);
  const [hiddenClasses, setHiddenClasses] = useState<Set<number>>(new Set());
  const [opacities, setOpacities] = useState<Record<number, number>>({});

  useEffect(() => {
    getSlide(sid).then((s) => {
      setSlide(s);
      if (s.active_config_version_id) getConfig(s.active_config_version_id).then(setConfig);
    });
    listSlideAnnotations(sid).then(setAnnotations);
  }, [sid]);

  const byClass = useMemo(() => {
    const map = new Map<number | null, { count: number; areaPx: number }>();
    for (const a of annotations) {
      const areaPx = shapeArea(a.type, a.coordinates_level0);
      const entry = map.get(a.class_id) ?? { count: 0, areaPx: 0 };
      entry.count += 1;
      entry.areaPx += areaPx;
      map.set(a.class_id, entry);
    }
    return map;
  }, [annotations]);

  const totalAreaPx = useMemo(() => [...byClass.values()].reduce((s, v) => s + v.areaPx, 0), [byClass]);
  const mppFactor = (slide?.mpp_x ?? 0.25) * (slide?.mpp_y ?? 0.25);

  if (!slide) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;

  const classes = config?.annotation_classes ?? [];

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] bg-surface">
      <div className="bg-tertiary-container/15 px-space-md py-space-sm flex items-center gap-space-sm text-body-sm border-b border-outline-variant">
        <MaterialIcon name="check_circle" className="text-tertiary !text-[16px]" />
        <span>
          <strong>{annotations.length}</strong> annotations stitched from patch-local coordinates into Level-0
          absolute space -- viewable seamlessly across the whole slide.
        </span>
      </div>

      <div className="bg-surface-container-low px-space-md py-space-sm flex items-center gap-space-md flex-wrap border-b border-outline-variant">
        <Link to={`/projects/${projectId}`} className="text-label-md text-primary hover:underline">
          &larr; {slide.filename}
        </Link>
        <ToggleChip label="Patch Grid" checked={showGrid} onChange={setShowGrid} />
        <ToggleChip label="Tissue Mask" checked={showMask} onChange={setShowMask} disabled={!slide.tissue_mask_path} />
        <ToggleChip label={`Annotations (${annotations.length})`} checked={showAnnotations} onChange={setShowAnnotations} />
      </div>

      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1 relative">
          <WsiViewer slideId={sid} className="w-full h-full" onViewportChange={setBbox}>
            {showMask && slide.tissue_mask_path && (
              <image
                href={tissueMaskUrl(sid)}
                x={0}
                y={0}
                width={slide.width_l0 ?? 0}
                height={slide.height_l0 ?? 0}
                opacity={0.4}
                style={{ mixBlendMode: "screen" }}
                preserveAspectRatio="none"
              />
            )}
            {showGrid && <PatchGridOverlay slideId={sid} bbox={bbox} />}
            {showAnnotations &&
              annotations
                .filter((a) => !hiddenClasses.has(a.class_id ?? -1))
                .map((a) => {
                  const cls = classes.find((c) => c.id === a.class_id);
                  const color = cls?.color_hex ?? "#94a3b8";
                  const opacity = opacities[a.class_id ?? -1] ?? 0.55;
                  if (a.type === "point") {
                    const [x, y] = a.coordinates_level0[0];
                    const r = (slide.width_l0 ?? 10000) * 0.002;
                    return <circle key={a.id} cx={x} cy={y} r={r} fill={color} fillOpacity={opacity} stroke={color} />;
                  }
                  const strokeWidth = (slide.width_l0 ?? 10000) * 0.0006;
                  if (isLineShape(a.type)) {
                    return (
                      <polyline
                        key={a.id}
                        points={a.coordinates_level0.map((p) => p.join(",")).join(" ")}
                        fill="none"
                        stroke={color}
                        strokeOpacity={Math.max(opacity, 0.7)}
                        strokeWidth={strokeWidth * 1.5}
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    );
                  }
                  if (a.type === "circle") {
                    const { cx, cy, r } = circleGeometry(a.coordinates_level0);
                    return <circle key={a.id} cx={cx} cy={cy} r={r} fill={color} fillOpacity={opacity * 0.5} stroke={color} strokeWidth={strokeWidth} />;
                  }
                  return (
                    <polygon
                      key={a.id}
                      points={a.coordinates_level0.map((p) => p.join(",")).join(" ")}
                      fill={color}
                      fillOpacity={opacity * 0.5}
                      stroke={color}
                      strokeWidth={(slide.width_l0 ?? 10000) * 0.0006}
                    />
                  );
                })}
          </WsiViewer>
        </div>

        <div className="w-96 bg-surface-container-lowest border-l border-outline-variant p-space-md flex flex-col gap-space-md overflow-y-auto">
          <div className="flex items-center justify-between">
            <h2 className="font-headline-sm text-headline-sm">Global Slide Metrics</h2>
            <span className="font-mono text-label-sm text-secondary">{slide.filename}</span>
          </div>

          <div className="grid grid-cols-2 gap-space-sm">
            <MetricTile label="Slide Area" value={slideAreaMm2(slide)} />
            <MetricTile label="Tissue Area" value={slide.tissue_area_mm2 != null ? `${slide.tissue_area_mm2} mm²` : "--"} sub={slide.tissue_coverage_pct != null ? `${slide.tissue_coverage_pct}% occupancy` : undefined} />
          </div>

          <Card className="p-space-sm bg-surface-container-low">
            <div className="text-label-md text-on-surface-variant">Object Stitching Total</div>
            <div className="font-headline-lg text-headline-lg">{annotations.length} Objects</div>
            <div className="text-body-sm text-on-surface-variant">from Level-0-anchored patches</div>
            <div className="h-2 rounded-full overflow-hidden flex mt-space-sm bg-surface-container-high">
              {[...byClass.entries()].map(([classId, v]) => {
                const cls = classes.find((c) => c.id === classId);
                const pct = totalAreaPx ? (v.areaPx / totalAreaPx) * 100 : 0;
                return <div key={String(classId)} style={{ width: `${pct}%`, backgroundColor: cls?.color_hex ?? "#94a3b8" }} />;
              })}
            </div>
          </Card>

          <div>
            <div className="text-label-md text-on-surface-variant mb-space-sm">Diagnostic Composition</div>
            <div className="flex flex-col gap-space-sm">
              {classes.map((c) => {
                const entry = byClass.get(c.id) ?? { count: 0, areaPx: 0 };
                const areaMm2 = (entry.areaPx * mppFactor) / 1_000_000;
                const pct = totalAreaPx ? (entry.areaPx / totalAreaPx) * 100 : 0;
                const hidden = hiddenClasses.has(c.id);
                return (
                  <div key={c.id} className="flex flex-col gap-1">
                    <div className="flex items-center justify-between text-label-md">
                      <span className="flex items-center gap-1.5">
                        <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: c.color_hex }} />
                        {c.name}
                      </span>
                      <span className="font-mono text-on-surface-variant">
                        {areaMm2.toFixed(3)} mm² ({pct.toFixed(1)}%)
                      </span>
                      <button
                        onClick={() =>
                          setHiddenClasses((s) => {
                            const next = new Set(s);
                            next.has(c.id) ? next.delete(c.id) : next.add(c.id);
                            return next;
                          })
                        }
                      >
                        <MaterialIcon name={hidden ? "visibility_off" : "visibility"} className="!text-[16px]" />
                      </button>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={opacities[c.id] ?? 0.55}
                      onChange={(e) => setOpacities((o) => ({ ...o, [c.id]: Number(e.target.value) }))}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function slideAreaMm2(slide: Slide) {
  if (!slide.width_l0 || !slide.height_l0 || !slide.mpp_x || !slide.mpp_y) return "--";
  const areaMm2 = (slide.width_l0 * slide.height_l0 * slide.mpp_x * slide.mpp_y) / 1_000_000;
  return `${areaMm2.toFixed(2)} mm²`;
}

function MetricTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-surface-container-low rounded p-space-sm">
      <div className="text-label-sm text-on-surface-variant">{label}</div>
      <div className="font-headline-sm text-headline-sm">{value}</div>
      {sub && <div className="text-body-sm text-on-surface-variant">{sub}</div>}
    </div>
  );
}

function ToggleChip({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className={`flex items-center gap-1.5 text-label-md ${disabled ? "opacity-40" : "cursor-pointer"}`}>
      <input type="checkbox" disabled={disabled} checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}
