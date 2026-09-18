import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { IconButton } from "../components/primitives";
import { AnnotationCanvas } from "../features/annotations/AnnotationCanvas";
import { useAnnotationHistory } from "../features/annotations/useAnnotationHistory";
import { PatchGridOverlay, PATCH_STATUS_COLORS } from "../features/viewer/PatchGridOverlay";
import { WsiViewer, type ViewportBbox } from "../features/viewer/WsiViewer";
import {
  createAnnotation,
  deleteAnnotation,
  dynamicPatchUrl,
  getConfig,
  getSlide,
  listPatchAnnotations,
  listPatches,
  nextPatch,
  updatePatch,
} from "../services/api";
import { useAnnotationStore, type AnnotationTool } from "../stores/annotationStore";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";
import type { ConfigVersion, GeometryAnnotation, GeometryType, Patch, Slide } from "../types/api";
import type { Point } from "../utils/coordinates";

const TOOLS: { id: AnnotationTool; icon: string; key: string; label: string }[] = [
  { id: "select", icon: "pan_tool", key: "V", label: "Select / Move" },
  { id: "polygon", icon: "polyline", key: "P", label: "Polygon" },
  { id: "rectangle", icon: "crop_square", key: "R", label: "Rectangle" },
  { id: "point", icon: "grain", key: "N", label: "Point Nuclei" },
  { id: "freehand", icon: "gesture", key: "F", label: "Freehand" },
];

export function MainWorkspacePage() {
  const { projectId, slideId } = useParams();
  const pid = Number(projectId);
  const sid = Number(slideId);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const annotatorName = useUiStore((s) => s.annotatorName);
  const setActiveSlide = useContextStore((s) => s.setActiveSlide);

  const tool = useAnnotationStore((s) => s.tool);
  const setTool = useAnnotationStore((s) => s.setTool);
  const activeClassId = useAnnotationStore((s) => s.activeClassId);
  const setActiveClassId = useAnnotationStore((s) => s.setActiveClassId);
  const saveState = useAnnotationStore((s) => s.saveState);
  const setSaveState = useAnnotationStore((s) => s.setSaveState);

  const [slide, setSlide] = useState<Slide | null>(null);
  const [config, setConfig] = useState<ConfigVersion | null>(null);
  const [patch, setPatch] = useState<Patch | null>(null);
  const [annotations, setAnnotations] = useState<GeometryAnnotation[]>([]);
  const [selectedAnnId, setSelectedAnnId] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [totals, setTotals] = useState({ total: 0, done: 0 });
  const [bbox, setBbox] = useState<ViewportBbox | null>(null);
  const [gridRefresh, setGridRefresh] = useState(0);
  const [notes, setNotes] = useState("");

  const history = useAnnotationHistory();

  useEffect(() => {
    getSlide(sid).then((s) => {
      setSlide(s);
      setActiveSlide(s);
      if (s.active_config_version_id) getConfig(s.active_config_version_id).then(setConfig);
    });
    return () => setActiveSlide(null);
  }, [sid]);

  // Initial patch load: from ?patch=<id> or the first patch in the slide.
  useEffect(() => {
    const paramId = searchParams.get("patch");
    (async () => {
      if (paramId) {
        const list = await listPatches(sid, { limit: 5000 });
        const found = list.items.find((p) => p.id === Number(paramId));
        setPatch(found ?? list.items[0] ?? null);
      } else {
        const list = await listPatches(sid, { limit: 1 });
        setPatch(list.items[0] ?? null);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid]);

  useEffect(() => {
    if (!patch) return;
    listPatchAnnotations(patch.id).then(setAnnotations);
    setNotes(patch.notes ?? "");
    history.reset();
    setSelectedAnnId(null);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("patch", String(patch.id));
      return next;
    });
    refreshTotals();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patch?.id]);

  function refreshTotals() {
    listPatches(sid, { limit: 1 }).then((r) => setTotals((t) => ({ ...t, total: r.total })));
    listPatches(sid, { status: "annotated,reviewed", limit: 1 }).then((r) => setTotals((t) => ({ ...t, done: r.total })));
  }

  const classes = config?.annotation_classes ?? [];

  useEffect(() => {
    if (!activeClassId && classes.length) setActiveClassId(classes[0].id);
  }, [classes.length]);

  const patchOrigin = useMemo(() => {
    if (!patch) return null;
    return { x: patch.x, y: patch.y, level: patch.level, downsample: patch.width_l0 / patch.width };
  }, [patch]);

  async function handleShapeComplete(type: GeometryType, points: Point[]) {
    if (!patch) return;
    if (!activeClassId) {
      pushToast("Select a diagnostic class first", "error");
      return;
    }
    setSaveState("saving");
    try {
      const created = await createAnnotation(patch.id, {
        type,
        class_id: activeClassId,
        coordinates_patch_local: points,
        created_by: annotatorName,
      });
      setAnnotations((prev) => [...prev, created]);
      setSaveState("saved");
      history.push({
        label: `create ${type}`,
        do: async () => {
          const recreated = await createAnnotation(patch.id, {
            type,
            class_id: activeClassId,
            coordinates_patch_local: points,
            created_by: annotatorName,
          });
          setAnnotations((prev) => [...prev.filter((a) => a.id !== created.id), recreated]);
        },
        undo: async () => {
          await deleteAnnotation(created.id);
          setAnnotations((prev) => prev.filter((a) => a.id !== created.id));
        },
      });
      setPatch((p) => (p && p.status === "unannotated" ? { ...p, status: "annotated" } : p));
      setGridRefresh((n) => n + 1);
    } catch (e) {
      setSaveState("error");
      pushToast(e instanceof Error ? e.message : "Failed to save annotation", "error");
    }
  }

  async function handleDeleteSelected() {
    if (!selectedAnnId) return;
    const target = annotations.find((a) => a.id === selectedAnnId);
    if (!target) return;
    setSaveState("saving");
    try {
      await deleteAnnotation(target.id);
      setAnnotations((prev) => prev.filter((a) => a.id !== target.id));
      setSelectedAnnId(null);
      setSaveState("saved");
      history.push({
        label: "delete annotation",
        do: async () => {
          await deleteAnnotation(target.id);
          setAnnotations((prev) => prev.filter((a) => a.id !== target.id));
        },
        undo: async () => {
          const recreated = await createAnnotation(target.patch_id, {
            type: target.type,
            class_id: target.class_id,
            coordinates_patch_local: target.coordinates_patch_local,
            created_by: target.created_by ?? annotatorName,
          });
          setAnnotations((prev) => [...prev, recreated]);
        },
      });
      setGridRefresh((n) => n + 1);
    } catch (e) {
      setSaveState("error");
      pushToast(e instanceof Error ? e.message : "Failed to delete annotation", "error");
    }
  }

  async function goToPatch(direction: "next" | "prev", filter: "any" | "unannotated" | "flagged" = "any") {
    if (!patch) return;
    const target = await nextPatch(sid, patch.patch_index, direction, filter);
    if (!target) {
      pushToast(`No more ${filter === "any" ? "" : filter + " "}patches in that direction`, "info");
      return;
    }
    setPatch(target);
  }

  async function persistPatchFields(fields: Partial<Patch>) {
    if (!patch) return;
    setSaveState("saving");
    try {
      const updated = await updatePatch(patch.id, fields as never);
      setPatch(updated);
      setSaveState("saved");
      setGridRefresh((n) => n + 1);
    } catch {
      setSaveState("error");
    }
  }

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      const toolMap: Record<string, AnnotationTool> = { v: "select", p: "polygon", r: "rectangle", n: "point", f: "freehand" };
      if (toolMap[e.key.toLowerCase()]) setTool(toolMap[e.key.toLowerCase()]);

      const classIdx = Number(e.key) - 1;
      if (!Number.isNaN(classIdx) && classes[classIdx]) setActiveClassId(classes[classIdx].id);

      if (e.key === "a" || e.key === "ArrowLeft") goToPatch("prev");
      if (e.key === "d" || e.key === "ArrowRight") goToPatch("next");
      if (e.key === " ") {
        e.preventDefault();
        goToPatch("next", "unannotated");
      }
      if ((e.ctrlKey || e.metaKey) && e.key === "z") {
        e.preventDefault();
        e.shiftKey ? history.redo() : history.undo();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patch, classes]);

  if (!slide || !config || !patch || !patchOrigin) {
    return <div className="p-space-xl text-center text-slate-400 bg-[#0a0f1d] h-[calc(100vh-3.5rem)]">Loading workspace...</div>;
  }

  const imageUrl = dynamicPatchUrl(sid, patch.x, patch.y, patch.width, patch.height, patch.level);
  const saveLabel = { idle: "", saving: "Saving...", saved: "Saved", error: "Error saving" }[saveState];

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] bg-[#0a0f1d] text-white overflow-hidden">
      {/* Precision strip */}
      <div className="bg-[#0b1329] border-b border-[#1e293b] px-space-md py-1.5 flex items-center gap-space-md text-body-sm flex-wrap">
        <span className="font-headline-sm">{slide.filename}</span>
        <span className="text-slate-500">Patch #{patch.patch_index} &middot; id {patch.id}</span>
        <span className="font-mono text-label-sm text-cyan-300">
          L0: X={patch.x.toLocaleString()} Y={patch.y.toLocaleString()}
        </span>
        <div className="flex-1" />
        <span className="text-slate-400">
          Slide Progress: {totals.total ? Math.round((totals.done / totals.total) * 100) : 0}% ({totals.done}/{totals.total})
        </span>
        <IconButton icon="undo" onClick={() => history.undo()} disabled={!history.canUndo} title="Undo (Ctrl+Z)" />
        <IconButton icon="redo" onClick={() => history.redo()} disabled={!history.canRedo} title="Redo (Ctrl+Shift+Z)" />
        <span
          className={`px-space-sm py-0.5 rounded-full text-label-sm ${
            saveState === "error" ? "bg-red-950 text-red-300" : saveState === "saving" ? "bg-slate-800 text-slate-300" : "bg-emerald-950 text-emerald-300"
          }`}
        >
          {saveLabel}
        </span>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Left: navigator */}
        <div className="w-64 bg-[#0f172a] border-r border-[#1e293b] flex flex-col overflow-hidden">
          <div className="h-44 relative border-b border-[#1e293b]">
            <WsiViewer slideId={sid} className="w-full h-full" showNavigator={false} onViewportChange={setBbox}>
              <PatchGridOverlay slideId={sid} bbox={bbox} activePatchId={patch.id} refreshKey={gridRefresh} onPatchClick={(p) => setPatch(p)} />
            </WsiViewer>
          </div>
          <div className="p-space-sm flex flex-col gap-space-sm overflow-y-auto text-body-sm">
            <div className="text-label-sm text-slate-400">Patch Status Legend</div>
            {Object.entries(PATCH_STATUS_COLORS).map(([status, color]) => (
              <div key={status} className="flex items-center gap-2 text-label-sm capitalize text-slate-300">
                <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: color }} />
                {status}
              </div>
            ))}
          </div>
        </div>

        {/* Center: canvas */}
        <div className="flex-1 relative overflow-hidden bg-[#070d1e]">
          <div className="absolute top-space-md left-1/2 -translate-x-1/2 z-20 bg-[#0f172a]/95 backdrop-blur-md rounded-xl shadow-2xl flex items-center gap-1 p-1">
            {TOOLS.map((t) => (
              <IconButton key={t.id} icon={t.icon} active={tool === t.id} onClick={() => setTool(t.id)} title={`${t.label} (${t.key})`} />
            ))}
            <div className="w-px h-6 bg-slate-700 mx-1" />
            {classes.map((c) => (
              <button
                key={c.id}
                onClick={() => setActiveClassId(c.id)}
                className="px-space-sm h-8 rounded text-label-md flex items-center gap-1.5"
                style={{
                  backgroundColor: activeClassId === c.id ? `${c.color_hex}33` : "transparent",
                  color: activeClassId === c.id ? c.color_hex : "#94a3b8",
                  boxShadow: activeClassId === c.id ? `inset 0 0 0 1px ${c.color_hex}80` : undefined,
                }}
              >
                {c.hotkey && <span className="font-mono">{c.hotkey}</span>}
                {c.name}
              </button>
            ))}
            <div className="w-px h-6 bg-slate-700 mx-1" />
            <IconButton icon="zoom_in" onClick={() => setZoom((z) => Math.min(4, z * 1.25))} />
            <IconButton icon="zoom_out" onClick={() => setZoom((z) => Math.max(0.5, z / 1.25))} />
            <button onClick={() => setZoom(1)} className="text-label-md text-slate-300 px-space-sm">
              Fit
            </button>
          </div>

          <div className="absolute inset-0 overflow-auto flex items-center justify-center p-space-lg pt-16">
            <AnnotationCanvas
              imageUrl={imageUrl}
              patchWidth={patch.width}
              patchHeight={patch.height}
              tool={tool}
              zoom={zoom}
              annotations={annotations}
              classes={classes}
              selectedId={selectedAnnId}
              onSelect={setSelectedAnnId}
              onShapeComplete={handleShapeComplete}
              onDeleteSelected={handleDeleteSelected}
            />
          </div>
        </div>

        {/* Right: QC panel */}
        <div className="w-80 bg-surface border-l border-[#1e293b] text-on-surface p-space-md flex flex-col gap-space-md overflow-y-auto">
          <h2 className="font-headline-sm text-headline-sm">Annotation & QC</h2>

          <div>
            <div className="text-label-md text-on-surface-variant mb-1">Patch Label</div>
            <select
              className="input"
              value={patch.patch_label ?? ""}
              onChange={(e) => persistPatchFields({ patch_label: e.target.value || null })}
            >
              <option value="">-- unset --</option>
              {classes.map((c) => (
                <option key={c.id} value={c.name}>
                  {c.name}
                </option>
              ))}
              <option value="Mixed">Mixed</option>
              <option value="Artifact / Background">Artifact / Background</option>
            </select>
          </div>

          <div>
            <div className="text-label-md text-on-surface-variant mb-1">Objects in Patch ({annotations.length})</div>
            <div className="flex flex-col gap-1">
              {annotations.map((a) => {
                const cls = classes.find((c) => c.id === a.class_id);
                return (
                  <div
                    key={a.id}
                    onClick={() => setSelectedAnnId(a.id)}
                    className={`flex items-center justify-between px-space-sm py-1.5 rounded bg-surface-container-lowest shadow-sm cursor-pointer ${
                      selectedAnnId === a.id ? "ring-1 ring-primary" : ""
                    }`}
                  >
                    <span className="flex items-center gap-1.5 text-label-md">
                      <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: cls?.color_hex ?? "#94a3b8" }} />
                      {cls?.name ?? "Unclassed"} #{a.id}
                    </span>
                    <span className="text-label-sm text-on-surface-variant capitalize">{a.type}</span>
                  </div>
                );
              })}
              {annotations.length === 0 && <div className="text-body-sm text-on-surface-variant">No objects yet -- draw one with the tools above.</div>}
            </div>
          </div>

          <div>
            <div className="text-label-md text-on-surface-variant mb-1">Pathologist Field Notes</div>
            <textarea
              className="input h-20 resize-none"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              onBlur={() => persistPatchFields({ notes })}
              placeholder="Clinical notes..."
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <div className="text-label-md text-on-surface-variant">Quality Assurance Flags</div>
            <Checkbox label="Mark as Ambiguous / Unsure" checked={patch.unsure} onChange={(v) => persistPatchFields({ unsure: v })} />
            <Checkbox label="Flag for Second Pathologist Review" checked={patch.flagged} onChange={(v) => persistPatchFields({ flagged: v })} />
            <Checkbox label="Exclude from Model Training (Blur/Fold)" checked={patch.excluded} onChange={(v) => persistPatchFields({ excluded: v })} />
          </div>

          <div className="flex items-center gap-space-sm mt-auto pt-space-sm border-t border-outline-variant">
            <button
              className="flex-1 h-8 rounded bg-surface-container-high text-label-md"
              onClick={() => persistPatchFields({ status: "skipped" })}
            >
              Skip
            </button>
            <button
              className="flex-1 h-8 rounded bg-primary-container text-on-primary-container text-label-md"
              onClick={() => persistPatchFields({ status: "reviewed", reviewed_by: annotatorName })}
            >
              Validate (V)
            </button>
          </div>
        </div>
      </div>

      {/* Bottom footer */}
      <div className="bg-[#0b1329] border-t border-[#1e293b] px-space-md py-space-sm flex items-center justify-between flex-wrap gap-space-sm">
        <Link to={`/projects/${pid}/slides/${sid}/gallery`} className="text-label-md text-slate-400 hover:text-white flex items-center gap-1">
          <MaterialIcon name="grid_on" className="!text-[16px]" />
          Gallery
        </Link>
        <div className="flex items-center gap-space-sm">
          <button onClick={() => goToPatch("prev")} className="px-space-md h-8 rounded bg-surface-container-high text-on-surface text-label-md">
            &larr; Prev (A)
          </button>
          <button onClick={() => goToPatch("next")} className="px-space-md h-8 rounded bg-[#0284c7] text-white text-label-md">
            Next (D) &rarr;
          </button>
          <button onClick={() => goToPatch("next", "unannotated")} className="px-space-md h-8 rounded bg-surface-container-high text-on-surface text-label-md">
            Next Unannotated (Space)
          </button>
          <button onClick={() => goToPatch("next", "flagged")} className="px-space-md h-8 rounded bg-amber-900/60 text-amber-300 text-label-md">
            Next Flagged
          </button>
        </div>
        <button
          onClick={() => navigate(`/projects/${pid}/slides/${sid}/export`)}
          className="text-label-md text-slate-400 hover:text-white flex items-center gap-1"
        >
          <MaterialIcon name="code" className="!text-[16px]" />
          Export
        </button>
      </div>
    </div>
  );
}

function Checkbox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-space-sm text-body-md cursor-pointer">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="w-4 h-4" />
      {label}
    </label>
  );
}
