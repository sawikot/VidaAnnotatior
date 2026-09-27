import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card, Modal } from "../components/primitives";
import { WsiViewer, type ViewportBbox } from "../features/viewer/WsiViewer";
import { PatchGridOverlay } from "../features/viewer/PatchGridOverlay";
import { ImportAnnotationsModal } from "../features/annotations/ImportAnnotationsModal";
import { ShapeLayer, type LayerShape } from "../features/annotations/ShapeLayer";
import { HOTKEYS } from "../features/annotations/tools";
import { TissueRegionPanel } from "../features/tissue/TissueRegionPanel";
import { REGION_CLASSES, REGION_TOOLS, classIdOf } from "../features/tissue/regionTools";
import { useTissueRegions } from "../features/tissue/useTissueRegions";
import type { AnnotationTool } from "../stores/annotationStore";
import { detectTissue, generatePatches, getConfig, getSlide } from "../services/api";
import { TissueMaskOutline } from "../features/tissue/TissueMaskOutline";
import { parseGridKey } from "../utils/gridKey";
import { GridSwitcher } from "../features/grids/GridSwitcher";
import { tissueParamsOf } from "../features/projects/configDraft";
import type { ConfigVersion, Slide, TissueRegionMode, TissueRegionType } from "../types/api";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";

type ViewMode = "wsi" | "mask" | "grid";

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

import { useImageProjectRedirect } from "../features/images/useImageProjectRedirect";

export function SlideProcessingPage() {
  const { projectId, slideId } = useParams();
  const pid = Number(projectId);
  const sid = Number(slideId);
  useImageProjectRedirect(pid);
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const setActiveSlide = useContextStore((s) => s.setActiveSlide);

  const [slide, setSlide] = useState<Slide | null>(null);
  const [config, setConfig] = useState<ConfigVersion | null>(null);
  const [mode, setMode] = useState<ViewMode>("wsi");
  const [bbox, setBbox] = useState<ViewportBbox | null>(null);
  const [busy, setBusy] = useState(false);
  const [gridRefresh, setGridRefresh] = useState(0);
  const [importOpen, setImportOpen] = useState(false);
  // Which part of the slide Generate Coords cuts: the tissue (by the configuration's threshold) or all of it.
  const [patchArea, setPatchArea] = useState<"tissue" | "whole">("tissue");
  // Start from what the slide's current patches cover (a whole-slide grid has no tissue threshold: "_t0").
  useEffect(() => {
    if (slide?.active_grid_key) setPatchArea(/_t0(_|$)/.test(slide.active_grid_key) ? "whole" : "tissue");
  }, [slide?.active_grid_key]);
  const [confirmReplace, setConfirmReplace] = useState(false);
  // Whole slide: tissue plays no part, so its outline, its drawing tools and its settings are put away
  // (kept as they are, for when Tissue only is picked again).
  const wholeSlide = patchArea === "whole";
  const wholeSlideRef = useRef(wholeSlide);
  wholeSlideRef.current = wholeSlide;
  useEffect(() => {
    if (wholeSlide) {
      setMode((m) => (m === "mask" ? "wsi" : m));
      setSelectedRegion(null);
    }
  }, [wholeSlide]);

  const [otsuSensitivity, setOtsuSensitivity] = useState(0.65);
  const [morphOpen, setMorphOpen] = useState(3);
  const [morphClose, setMorphClose] = useState(5);
  const [maskCacheBust, setMaskCacheBust] = useState(0);
  // How solid the mask and the hand-drawn regions are drawn (fill only; their borders always show).
  const [maskOpacity, setMaskOpacity] = useStoredNumber("vp.maskOpacity", 0.2);
  const [regionOpacity, setRegionOpacity] = useStoredNumber("vp.regionOpacity", 0.25);

  // Hand-drawn tissue regions (Add / Remove), drawn on the slide in Level-0 pixels.
  const [tool, setTool] = useState<AnnotationTool>("pan");
  const [drawMode, setDrawMode] = useState<TissueRegionMode>("add");
  const [selectedRegion, setSelectedRegion] = useState<number | null>(null);
  const [scale, setScale] = useState(0.05);
  const [spaceHeld, setSpaceHeld] = useState(false);
  const gestureRef = useRef(false);
  const tissue = useTissueRegions(sid, () => {
    setMaskCacheBust((n) => n + 1);
    setMode((m) => (m === "wsi" ? "mask" : m)); // show what the change did to the mask
    refresh();
  });
  const regionShapes = useMemo<LayerShape[]>(
    () =>
      (tissue.data?.regions ?? []).map((r) => ({
        id: r.id,
        type: r.type,
        points: r.coordinates,
        class_id: classIdOf(r.mode),
        unsure: false,
        excluded: false,
      })),
    [tissue.data],
  );
  const effectiveTool = spaceHeld || wholeSlide ? "pan" : tool;
  const effectiveToolRef = useRef(effectiveTool);
  effectiveToolRef.current = effectiveTool;
  const blockPan = () => {
    const t = effectiveToolRef.current;
    return t !== "pan" && (t !== "select" || gestureRef.current);
  };

  // Keys: hold Space to pan, tool hotkeys, Ctrl+Z / Ctrl+Shift+Z for the regions.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (isTyping(e.target)) return;
      if (e.key === " ") {
        e.preventDefault();
        setSpaceHeld(true);
        return;
      }
      if (wholeSlideRef.current) return; // no tissue regions to draw or undo
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) tissue.redo();
        else tissue.undo();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const picked = HOTKEYS[e.key.toLowerCase()];
      if (picked && REGION_TOOLS.some((t) => t.id === picked)) setTool(picked);
    }
    const onKeyUp = (e: KeyboardEvent) => e.key === " " && setSpaceHeld(false);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [tissue]);

  useEffect(() => {
    refresh();
    return () => setActiveSlide(null);
  }, [sid]);

  // Start the detection sliders from the active version's saved tissue
  // defaults whenever the version changes (not on every refresh, which would
  // discard adjustments made since the last detection run).
  useEffect(() => {
    if (!config) return;
    const t = tissueParamsOf(config);
    setOtsuSensitivity(t.otsu_sensitivity);
    setMorphOpen(t.morph_open_px);
    setMorphClose(t.morph_close_px);
  }, [config?.id]);

  function refresh() {
    getSlide(sid)
      .then(async (s) => {
        setSlide(s);
        setActiveSlide(s);
        setConfig(s.active_config_version_id ? await getConfig(s.active_config_version_id) : null);
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
      tissue.reload(); // detection makes the mask start from its result again
    } catch (e) {
      pushToast(e instanceof Error ? e.message : "Tissue detection failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleGeneratePatches(confirmed = false) {
    if (!slide?.active_config_version_id) {
      pushToast("This slide has no configuration", "error");
      return;
    }
    if (!confirmed && slide.active_grid_key) {
      setConfirmReplace(true); // the slide has patches: generating replaces them
      return;
    }
    setConfirmReplace(false);
    setBusy(true);
    try {
      const res = await generatePatches(sid, slide.active_config_version_id, undefined, patchArea === "whole");
      pushToast(
        (patchArea === "whole"
          ? `Generated ${res.kept} patches over the whole slide`
          : `Generated ${res.kept} patches (${res.excluded} excluded by tissue threshold)`) +
          (res.replaced_grids ? `; replaced ${res.replaced_patches} earlier patches` : "") +
          (res.annotations_moved_to_slide ? `, ${res.annotations_moved_to_slide} annotations kept on the whole slide` : ""),
        "success",
      );
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
          ).filter(([m]) => !(wholeSlide && m === "mask")).map(([m, label, icon]) => (
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
          {!wholeSlide && (
            <Button variant="secondary" icon="autorenew" onClick={handleDetectTissue} disabled={busy}>
              {busy ? "Working..." : "Re-run Detection"}
            </Button>
          )}
          <select
            className="h-8 rounded bg-[#1e293b] text-white text-label-md px-space-sm border border-[#334155]"
            value={patchArea}
            onChange={(e) => setPatchArea(e.target.value as "tissue" | "whole")}
            aria-label="Patch area"
            title="Which part of the slide Generate Coords cuts into patches"
          >
            <option value="tissue">Tissue only</option>
            <option value="whole">Whole slide</option>
          </select>
          <Button
            variant="secondary"
            icon="tune"
            onClick={() => void handleGeneratePatches()}
            disabled={busy || (patchArea === "tissue" && !slide.tissue_mask_path)}
            title={
              patchArea === "whole"
                ? "Cut the entire slide into patches, glass included (no tissue detection needed)"
                : "Cut the detected tissue into patches"
            }
          >
            Generate Coords
          </Button>
          <Button
            variant="secondary"
            icon="upload_file"
            onClick={() => setImportOpen(true)}
            disabled={slide.status === "imported" || slide.status === "tissue_detected"}
            title="Import annotations from WSI JSON, GeoJSON (QuPath), COCO, ASAP/Aperio XML or CSV"
          >
            Import Annotations
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
          <WsiViewer
            slideId={sid}
            version={slide.image_version}
            className="w-full h-full"
            keyboardNav={false}
            blockPan={blockPan}
            onViewportChange={(b, _zoom, s) => {
              setBbox(b);
              setScale(s);
            }}
          >
            {mode === "mask" && slide.tissue_mask_path && !wholeSlide && (
              <TissueMaskOutline slideId={sid} refreshKey={maskCacheBust} opacity={maskOpacity} />
            )}
            {mode === "grid" && <PatchGridOverlay slideId={sid} bbox={bbox} refreshKey={gridRefresh} />}
            {slide.width_l0 && slide.height_l0 && !wholeSlide && (
              <ShapeLayer
                width={slide.width_l0}
                height={slide.height_l0}
                scale={scale}
                tool={effectiveTool}
                shapes={regionShapes}
                classes={REGION_CLASSES}
                selectedId={selectedRegion}
                onSelect={setSelectedRegion}
                onShapeComplete={(type, points) => tissue.add({ mode: drawMode, type: type as TissueRegionType, coordinates: points })}
                onShapeEdit={(id, points) => tissue.update(id, { coordinates: points })}
                onDeleteSelected={() => {
                  if (selectedRegion == null) return;
                  tissue.remove(selectedRegion);
                  setSelectedRegion(null);
                }}
                gestureRef={gestureRef}
                resetKey={sid}
                fillOpacity={regionOpacity}
              />
            )}
          </WsiViewer>
          <div className="absolute top-3 left-3 px-space-sm py-1 rounded bg-black/50 text-label-sm text-slate-300 pointer-events-none">
            {effectiveTool === "pan" ? "Drag to move around \u00b7 scroll to zoom" : "Hold Space to move around \u00b7 scroll to zoom"}
          </div>
          {!slide.tissue_mask_path && !wholeSlide && (
            <div className="absolute bottom-4 left-4 bg-[#0f172a]/90 backdrop-blur px-space-md py-space-sm rounded-lg text-body-sm text-amber-300 flex items-center gap-2">
              <MaterialIcon name="info" className="!text-[16px]" />
              {slide.tissue_source === "manual"
                ? "Draw the tissue areas (Add tissue) before generating patch coordinates."
                : "Run tissue detection, or draw tissue areas, before generating patch coordinates."}
            </div>
          )}
        </div>

        <div className="w-96 bg-[#0f172a] border-l border-[#1e293b] p-space-md flex flex-col gap-space-md overflow-y-auto">
          {wholeSlide ? (
            <div className="bg-[#0b1329] rounded p-space-md flex flex-col gap-space-sm">
              <div className="flex items-center gap-space-sm text-label-lg">
                <MaterialIcon name="select_all" className="!text-[18px] text-sky-400" />
                Whole slide
              </div>
              <p className="text-body-sm text-slate-400">
                Patches cover the entire slide, glass included, so tissue detection and tissue areas are not used. Your tissue
                settings and drawn areas are kept; pick <strong className="text-slate-200">Tissue only</strong> to see and edit them.
              </p>
            </div>
          ) : (
          <>
          <div className="flex items-center justify-between">
            <h2 className="font-headline-sm text-headline-sm">Segmentation Pipeline</h2>
            <span className="text-label-sm text-slate-500">HSV + Otsu</span>
          </div>
          <div className={`flex flex-col gap-space-md ${tissue.data?.source === "manual" ? "opacity-50" : ""}`}>
          <div className="text-label-sm text-slate-500">
            {tissue.data?.source === "manual" ? "Automatic detection (not used in Manual only mode)" : "Automatic detection"}
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
          <SliderField label="Morph Open (px)" value={morphOpen} min={0} max={15} step={1} onChange={setMorphOpen} display={String(morphOpen)} />
          <SliderField label="Morph Close (px)" value={morphClose} min={0} max={15} step={1} onChange={setMorphClose} display={String(morphClose)} />
          </div>

          <div className="bg-[#0b1329] rounded p-space-sm flex flex-col gap-space-sm">
            <div className="text-label-md text-slate-300">Mask display</div>
            <SliderField
              label="Tissue mask fill"
              value={maskOpacity}
              min={0}
              max={1}
              step={0.05}
              onChange={(v) => {
                setMaskOpacity(v);
                setMode((m) => (m === "wsi" ? "mask" : m)); // the mask only shows in the Tissue Mask view
              }}
              display={`${Math.round(maskOpacity * 100)}%`}
            />
            <SliderField
              label="Manual regions fill"
              value={regionOpacity}
              min={0}
              max={1}
              step={0.05}
              onChange={setRegionOpacity}
              display={`${Math.round(regionOpacity * 100)}%`}
            />
            <span className="text-label-sm text-slate-500">0% shows only the outlines; 100% is solid.</span>
          </div>

          {tissue.data && (
            <TissueRegionPanel
              source={tissue.data.source}
              onSourceChange={tissue.setSource}
              regions={tissue.data.regions}
              drawMode={drawMode}
              onDrawModeChange={setDrawMode}
              tool={tool}
              onToolChange={setTool}
              selectedId={selectedRegion}
              onSelect={(id) => {
                setSelectedRegion(id);
                setTool("select");
              }}
              onFlip={(id) => {
                const r = tissue.data?.regions.find((x) => x.id === id);
                if (r) tissue.update(id, { mode: r.mode === "add" ? "remove" : "add" });
              }}
              onDelete={(id) => {
                tissue.remove(id);
                setSelectedRegion(null);
              }}
              onClear={() => {
                tissue.clear();
                setSelectedRegion(null);
              }}
              canUndo={tissue.canUndo}
              canRedo={tissue.canRedo}
              onUndo={tissue.undo}
              onRedo={tissue.redo}
              saving={tissue.saving}
              disabled={busy}
            />
          )}
          </>
          )}

          {config && (
            <div className="bg-[#0b1329] rounded p-space-sm flex flex-col gap-space-sm">
              <div className="flex items-center justify-between">
                <span className="text-label-md text-slate-300">
                  Patch grid
                </span>
                <Link to={`/projects/${pid}/settings`} className="text-label-sm text-sky-400 hover:underline flex items-center gap-1">
                  <MaterialIcon name="edit" className="!text-[14px]" />
                  Edit configuration
                </Link>
              </div>
              {(() => {
                // The slide's own patches when it has them; the project's default (what it will get) until then.
                const current = slide.active_grid_key ? parseGridKey(slide.active_grid_key) : null;
                const shown = current ?? config;
                const whole = shown.min_tissue_fraction <= 0;
                const stride = shown.stride_x === shown.stride_y ? shown.stride_x : `${shown.stride_x}×${shown.stride_y}`;
                return (
                  <>
                    <div className="grid grid-cols-4 gap-space-sm text-center">
                      <GridStat label="Width" value={shown.patch_width} />
                      <GridStat label="Height" value={shown.patch_height} />
                      <GridStat label="Stride" value={stride} />
                      <GridStat label={whole ? "Area" : "Min tissue"} value={whole ? "Whole" : `${Math.round(shown.min_tissue_fraction * 100)}%`} />
                    </div>
                    <span className="text-label-sm text-slate-500">
                      {current ? (
                        <>
                          This slide's patches. Generate Coords re-cuts at this size; pick another size below to replace them.
                          Project default: {config.patch_width} px
                          {config.min_tissue_fraction <= 0 ? ", whole slide" : `, tissue ≥ ${Math.round(config.min_tissue_fraction * 100)}%`}.
                        </>
                      ) : (
                        "The project's default size: no patches yet, Generate Coords cuts this size."
                      )}
                    </span>
                  </>
                );
              })()}
              <GridSwitcher
                slideId={sid}
                configVersionId={slide.active_config_version_id}
                refreshKey={`${gridRefresh}:${slide.active_grid_key}`}
                disabled={busy}
                onChanged={() => {
                  setGridRefresh((n) => n + 1);
                  setMode("grid");
                  refresh();
                }}
              />
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

      <Modal open={confirmReplace} onClose={() => setConfirmReplace(false)} widthClass="max-w-lg">
        <div className="p-space-lg flex flex-col gap-space-md">
          <h2 className="font-headline-md text-headline-md">Replace this slide's patches?</h2>
          <p className="text-body-md">
            Generate Coords cuts the slide again ({patchArea === "whole" ? "the whole slide" : "the tissue"}, at its current
            patch size) and <strong>replaces</strong> the patches it has now.
          </p>
          <ul className="text-body-md list-disc pl-space-lg flex flex-col gap-1">
            <li>
              Annotations drawn in the current patches are <strong>kept</strong>, on the whole slide at the same place.
            </li>
            <li>The current patches' status, labels and notes are removed.</li>
            <li>Regenerating the same size and area just updates it; nothing is removed.</li>
          </ul>
          <div className="flex justify-end gap-space-sm">
            <Button variant="ghost" onClick={() => setConfirmReplace(false)}>
              Cancel
            </Button>
            <Button variant="primary" icon="tune" onClick={() => void handleGeneratePatches(true)}>
              Generate and replace
            </Button>
          </div>
        </div>
      </Modal>

      <ImportAnnotationsModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        slideId={sid}
        onImported={() => {
          // Straight to the whole-slide view, where the imported shapes can be checked in place.
          setImportOpen(false);
          navigate(`/projects/${pid}/slides/${sid}/workspace?mode=wsi`);
        }}
      />
    </div>
  );
}

/** A number kept in this browser (a display preference); falls back to `initial` if storage is unavailable. */
function useStoredNumber(key: string, initial: number): [number, (v: number) => void] {
  const [value, setValue] = useState(() => {
    try {
      const saved = Number(localStorage.getItem(key));
      return localStorage.getItem(key) !== null && Number.isFinite(saved) ? saved : initial;
    } catch {
      return initial;
    }
  });
  const set = (v: number) => {
    setValue(v);
    try {
      localStorage.setItem(key, String(v));
    } catch {
      // private mode / storage blocked: the setting just isn't remembered
    }
  };
  return [value, set];
}

function GridStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="font-mono text-label-lg">{value}</div>
      <div className="text-label-sm text-slate-500">{label}</div>
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
