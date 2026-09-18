import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card } from "../components/primitives";
import { WsiViewer, type ViewportBbox } from "../features/viewer/WsiViewer";
import { PatchGridOverlay } from "../features/viewer/PatchGridOverlay";
import { detectTissue, generatePatches, getConfig, getSlide, tissueMaskUrl } from "../services/api";
import type { ConfigVersion, Slide } from "../types/api";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";

type ViewMode = "wsi" | "mask" | "grid";

export function SlideProcessingPage() {
  const { projectId, slideId } = useParams();
  const pid = Number(projectId);
  const sid = Number(slideId);
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const setActiveSlide = useContextStore((s) => s.setActiveSlide);

  const [slide, setSlide] = useState<Slide | null>(null);
  const [config, setConfig] = useState<ConfigVersion | null>(null);
  const [mode, setMode] = useState<ViewMode>("wsi");
  const [bbox, setBbox] = useState<ViewportBbox | null>(null);
  const [busy, setBusy] = useState(false);
  const [gridRefresh, setGridRefresh] = useState(0);

  const [otsuSensitivity, setOtsuSensitivity] = useState(0.65);
  const [tissueFracPct, setTissueFracPct] = useState(60);
  const [morphOpen, setMorphOpen] = useState(3);
  const [morphClose, setMorphClose] = useState(5);
  const [maskCacheBust, setMaskCacheBust] = useState(0);

  useEffect(() => {
    refresh();
    return () => setActiveSlide(null);
  }, [sid]);

  function refresh() {
    getSlide(sid)
      .then(async (s) => {
        setSlide(s);
        setActiveSlide(s);
        if (s.active_config_version_id) {
          const c = await getConfig(s.active_config_version_id);
          setConfig(c);
          setTissueFracPct(Math.round(c.min_tissue_fraction * 100));
        }
      })
      .catch(() => pushToast("Failed to load slide", "error"));
  }

  async function handleDetectTissue() {
    setBusy(true);
    try {
      const res = await detectTissue(sid, {
        method: "hsv_otsu",
        otsu_sensitivity: otsuSensitivity,
        morph_open_px: morphOpen,
        morph_close_px: morphClose,
      });
      pushToast(`Tissue detected: ${res.tissue_coverage_pct}% coverage`, "success");
      setMaskCacheBust((n) => n + 1);
      setMode("mask");
      refresh();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Tissue detection failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleGeneratePatches() {
    if (!slide?.active_config_version_id) {
      pushToast("No config version assigned to this slide", "error");
      return;
    }
    setBusy(true);
    try {
      const res = await generatePatches(sid, slide.active_config_version_id);
      pushToast(`Generated ${res.kept} patches (${res.excluded} excluded by tissue threshold)`, "success");
      setMode("grid");
      setGridRefresh((n) => n + 1);
      refresh();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Patch generation failed", "error");
    } finally {
      setBusy(false);
    }
  }

  if (!slide) return <div className="p-space-xl text-center text-slate-400">Loading...</div>;

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] bg-[#0a0f1d] text-white">
      <div className="bg-[#0b1329] border-b border-[#1e293b] px-space-md py-space-sm flex items-center justify-between flex-wrap gap-space-sm">
        <div className="flex items-center gap-space-sm text-body-sm text-slate-400">
          <Link to="/projects" className="hover:underline">
            Projects
          </Link>{" "}
          / <Link to={`/projects/${pid}`} className="hover:underline">{projectId}</Link> / Slides /
          <span className="text-white font-headline-sm">{slide.filename}</span>
          {slide.tissue_coverage_pct != null && (
            <span className="ml-space-sm px-space-sm py-0.5 rounded-full bg-emerald-950/60 text-emerald-300 text-label-sm">
              Tissue Segmented
            </span>
          )}
        </div>

        <div className="flex bg-[#070d1e] rounded-lg p-0.5">
          {(
            [
              ["wsi", "Original WSI", "crop_original"],
              ["mask", "Tissue Mask", "contrast"],
              ["grid", "Patch Grid", "grid_4x4"],
            ] as const
          ).map(([m, label, icon]) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex items-center gap-1.5 px-space-md py-1.5 rounded-lg text-label-md ${
                mode === m ? "bg-[#007bb9] text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              <MaterialIcon name={icon} className="!text-[16px]" />
              {label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-space-sm">
          <Button variant="secondary" icon="autorenew" onClick={handleDetectTissue} disabled={busy}>
            {busy ? "Working..." : "Re-run Detection"}
          </Button>
          <Button variant="secondary" icon="tune" onClick={handleGeneratePatches} disabled={busy || !slide.tissue_mask_path}>
            Generate Coords
          </Button>
          <Button
            variant="primary"
            icon="open_in_new"
            onClick={() => navigate(`/projects/${pid}/slides/${sid}/workspace`)}
            disabled={slide.status !== "patches_generated" && slide.status !== "annotating" && slide.status !== "reviewed"}
          >
            Open Annotation Workspace
          </Button>
        </div>
      </div>

      <div className="flex-1 flex overflow-hidden">
        <div className="w-56 bg-[#0f172a] border-r border-[#1e293b] p-space-sm flex flex-col gap-space-md overflow-y-auto text-body-sm">
          <div>
            <div className="text-label-sm text-slate-400 mb-1">Physical Scale</div>
            <div className="font-mono text-label-md">{slide.mpp_x ? `${slide.mpp_x.toFixed(4)} µm/px` : "--"}</div>
            <div className="font-mono text-label-sm text-slate-500">Nominal {slide.magnification ?? "?"}x Scan</div>
          </div>
          <div>
            <div className="text-label-sm text-slate-400 mb-1">Level-0 Dimensions</div>
            <div className="font-mono text-label-md">
              {slide.width_l0?.toLocaleString()} x {slide.height_l0?.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-label-sm text-slate-400 mb-1">Pyramid Levels</div>
            <div className="font-mono text-label-md">{slide.level_count}</div>
          </div>
          {slide.tissue_area_mm2 != null && (
            <div>
              <div className="text-label-sm text-slate-400 mb-1">Segmented Tissue Area</div>
              <div className="font-mono text-label-md">{slide.tissue_area_mm2} mm²</div>
              <div className="font-mono text-label-sm text-slate-500">{slide.tissue_coverage_pct}% of scan</div>
            </div>
          )}
        </div>

        <div className="flex-1 relative">
          <WsiViewer slideId={sid} className="w-full h-full" onViewportChange={setBbox}>
            {mode === "mask" && slide.tissue_mask_path && (
              <image
                href={`${tissueMaskUrl(sid)}?v=${maskCacheBust}`}
                x={0}
                y={0}
                width={slide.width_l0 ?? 0}
                height={slide.height_l0 ?? 0}
                opacity={0.55}
                style={{ mixBlendMode: "screen" }}
                preserveAspectRatio="none"
              />
            )}
            {mode === "grid" && <PatchGridOverlay slideId={sid} bbox={bbox} refreshKey={gridRefresh} />}
          </WsiViewer>
          {!slide.tissue_mask_path && (
            <div className="absolute bottom-4 left-4 bg-[#0f172a]/90 backdrop-blur px-space-md py-space-sm rounded-lg text-body-sm text-amber-300 flex items-center gap-2">
              <MaterialIcon name="info" className="!text-[16px]" />
              Run tissue detection before generating patch coordinates.
            </div>
          )}
        </div>

        <div className="w-96 bg-[#0f172a] border-l border-[#1e293b] p-space-md flex flex-col gap-space-md overflow-y-auto">
          <div className="flex items-center justify-between">
            <h2 className="font-headline-sm text-headline-sm">Segmentation Pipeline</h2>
            <span className="text-label-sm text-slate-500">HSV + Otsu</span>
          </div>
          <SliderField
            label="Tissue Threshold (Otsu Sens.)"
            value={otsuSensitivity}
            min={0.1}
            max={0.95}
            step={0.01}
            onChange={setOtsuSensitivity}
            display={otsuSensitivity.toFixed(2)}
          />
          <SliderField
            label="Min. Patch Tissue Fraction"
            value={tissueFracPct}
            min={10}
            max={90}
            step={5}
            onChange={setTissueFracPct}
            display={`${tissueFracPct}%`}
          />
          <SliderField label="Morph Open (px)" value={morphOpen} min={0} max={15} step={1} onChange={setMorphOpen} display={String(morphOpen)} />
          <SliderField label="Morph Close (px)" value={morphClose} min={0} max={15} step={1} onChange={setMorphClose} display={String(morphClose)} />

          {config && (
            <div className="grid grid-cols-3 gap-space-sm bg-[#0b1329] rounded p-space-sm text-center">
              <div>
                <div className="font-mono text-label-lg">{config.patch_width}</div>
                <div className="text-label-sm text-slate-500">Width</div>
              </div>
              <div>
                <div className="font-mono text-label-lg">{config.patch_height}</div>
                <div className="text-label-sm text-slate-500">Height</div>
              </div>
              <div>
                <div className="font-mono text-label-lg">{config.stride_x}</div>
                <div className="text-label-sm text-slate-500">Stride</div>
              </div>
            </div>
          )}

          <Card className="p-space-sm bg-gradient-to-br from-emerald-950 to-[#0b1329] text-emerald-300">
            <MaterialIcon name="savings" className="!text-[18px]" />
            <div className="text-label-md mt-1">Storage Spared</div>
            <div className="text-body-sm text-emerald-400">
              No patch image files are extracted -- coordinates only.
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function SliderField({
  label,
  value,
  min,
  max,
  step,
  onChange,
  display,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  display: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between text-label-md text-slate-300 mb-1">
        <span>{label}</span>
        <span className="font-mono text-slate-400">{display}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
    </div>
  );
}
