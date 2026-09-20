import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { IconButton } from "../components/primitives";
import { AnnotationCanvas } from "../features/annotations/AnnotationCanvas";
import { useAnnotationHistory } from "../features/annotations/useAnnotationHistory";
import { neighbour, progress, type ImageFilter } from "../features/images/imageNav";
import { PatchGridOverlay, PATCH_STATUS_COLORS } from "../features/viewer/PatchGridOverlay";
import { WsiViewer, type ViewportBbox } from "../features/viewer/WsiViewer";
import {
  createAnnotation,
  deleteAnnotation,
  dynamicPatchUrl,
  getConfig,
  getProject,
  getSlide,
  listImages,
  listPatchAnnotations,
  listPatches,
  nextPatch,
  thumbnailUrl,
  updateAnnotation,
  updatePatch,
} from "../services/api";
import { useAnnotationStore, type AnnotationTool } from "../stores/annotationStore";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";
import type { ConfigVersion, GeometryAnnotation, GeometryType, ImageSummary, Patch, Slide } from "../types/api";
import type { Point } from "../utils/coordinates";

const TOOLS: { id: AnnotationTool; icon: string; key: string; label: string }[] = [
  { id: "select", icon: "near_me", key: "V", label: "Select / Move" },
  { id: "point", icon: "control_point", key: "N", label: "Point" },
  { id: "line", icon: "horizontal_rule", key: "L", label: "Line (drag)" },
  { id: "freehand_line", icon: "gesture", key: "G", label: "Freehand line (drag)" },
  { id: "rectangle", icon: "crop_square", key: "R", label: "Rectangle (drag)" },
  { id: "circle", icon: "radio_button_unchecked", key: "C", label: "Circle (drag from the centre)" },
  { id: "polygon", icon: "pentagon", key: "P", label: "Polygon (click points, double-click or Enter to finish)" },
  { id: "freehand", icon: "draw", key: "F", label: "Freehand polygon (drag)" },
];

const HOTKEYS: Record<string, AnnotationTool> = Object.fromEntries(TOOLS.map((t) => [t.key.toLowerCase(), t.id]));

type AnnotationFields = Partial<Pick<GeometryAnnotation, "class_id" | "unsure" | "flagged" | "notes" | "coordinates_patch_local">>;

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

  // Image projects: every image is a slide with one patch, so moving on means moving to
  // another slide. The whole (light) image list is held here to make that instant.
  const [isImage, setIsImage] = useState<boolean | null>(null);
  const [images, setImages] = useState<ImageSummary[]>([]);
  const [overrides, setOverrides] = useState<Record<number, Partial<ImageSummary>>>({});
  const [listFilter, setListFilter] = useState<ImageFilter>("any");
  const [areaEl, setAreaEl] = useState<HTMLDivElement | null>(null);
  const [area, setArea] = useState({ w: 0, h: 0 });

  const history = useAnnotationHistory();

  useEffect(() => {
    getProject(pid)
      .then((p) => {
        setIsImage(p.project_type === "image");
        if (p.project_type === "image") {
          listImages(pid).then((r) => {
            setImages(r.items);
            setOverrides({});
          });
        }
      })
      .catch(() => setIsImage(false));
  }, [pid]);

  // The row of the image being edited always reflects the live local state; rows of images
  // already left keep what was last true, so "next unannotated" never returns to one just done.
  useEffect(() => {
    if (!isImage || !patch || patch.slide_id !== sid) return;
    setOverrides((o) => ({
      ...o,
      [sid]: { status: patch.status, flagged: patch.flagged, unsure: patch.unsure, excluded: patch.excluded, annotation_count: annotations.length },
    }));
  }, [isImage, patch, annotations.length, sid]);

  const imagesLive = useMemo(() => images.map((i) => (overrides[i.slide_id] ? { ...i, ...overrides[i.slide_id] } : i)), [images, overrides]);

  useEffect(() => {
    if (!areaEl) return;
    const observer = new ResizeObserver(() => setArea({ w: areaEl.clientWidth, h: areaEl.clientHeight }));
    observer.observe(areaEl);
    setArea({ w: areaEl.clientWidth, h: areaEl.clientHeight });
    return () => observer.disconnect();
  }, [areaEl]);

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
    if (isImage !== true) {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("patch", String(patch.id));
        return next;
      });
      refreshTotals();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patch?.id]);

  function refreshTotals() {
    listPatches(sid, { limit: 1 }).then((r) => setTotals((t) => ({ ...t, total: r.total })));
    listPatches(sid, { status: "annotated,reviewed", limit: 1 }).then((r) => setTotals((t) => ({ ...t, done: r.total })));
  }

  const classes = config?.annotation_classes ?? [];
  const selectedAnn = annotations.find((a) => a.id === selectedAnnId) ?? null;

  // The project's configuration decides which drawing tools are offered; Select is always there.
  const enabledTools = config?.enabled_tools ?? [];
  const visibleTools = TOOLS.filter((t) => t.id === "select" || enabledTools.length === 0 || enabledTools.includes(t.id));
  useEffect(() => {
    if (config && !visibleTools.some((t) => t.id === tool)) setTool("select"); // the remembered tool is switched off here
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.id, tool]);

  useEffect(() => {
    if (!activeClassId && classes.length) setActiveClassId(classes[0].id);
  }, [classes.length]);

  // Images are shown fitted to the window (small crops are enlarged); `zoom` is a multiplier on that.
  const fitZoom =
    isImage && patch && area.w > 0 ? Math.max(0.05, Math.min(8, (area.w - 48) / patch.width, (area.h - 48) / patch.height)) : 1;
  const effectiveZoom = fitZoom * zoom;
  const maxZoom = isImage ? 8 : 4;

  const patchOrigin = useMemo(() => {
    if (!patch) return null;
    return { x: patch.x, y: patch.y, level: patch.level, downsample: patch.width_l0 / patch.width };
  }, [patch]);

  async function handleShapeComplete(type: GeometryType, points: Point[]) {
    if (!patch || patch.slide_id !== sid) return;
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

  /**
   * Changes an existing annotation -- its shape, class, flags or notes. The screen updates at once,
   * the save follows, and the change goes on the undo stack (undo puts the previous values back).
   */
  async function editAnnotation(id: number, fields: AnnotationFields, label: string) {
    const target = annotations.find((a) => a.id === id);
    if (!target || patch?.slide_id !== sid) return;
    const before = Object.fromEntries(Object.keys(fields).map((key) => [key, target[key as keyof AnnotationFields]])) as AnnotationFields;
    const replace = (updated: GeometryAnnotation) => setAnnotations((prev) => prev.map((a) => (a.id === id ? updated : a)));
    const send = async (f: AnnotationFields) => replace(await updateAnnotation(id, f));

    setSaveState("saving");
    replace({ ...target, ...fields });
    try {
      await send(fields);
      setSaveState("saved");
      history.push({ label, do: () => send(fields), undo: () => send(before) });
    } catch (e) {
      replace(target); // the save failed: put it back to what the server still has
      setSaveState("error");
      pushToast(e instanceof Error ? e.message : "Failed to save the change", "error");
    }
  }

  const handleShapeEdit = (id: number, points: Point[]) => editAnnotation(id, { coordinates_patch_local: points }, "edit shape");

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
    if (isImage) {
      const target = neighbour(imagesLive, sid, direction, filter);
      if (!target) {
        pushToast(`No more ${filter === "any" ? "" : filter + " "}images in that direction`, "info");
        return;
      }
      navigate(`/projects/${pid}/slides/${target.slide_id}/workspace`);
      return;
    }
    const target = await nextPatch(sid, patch.patch_index, direction, filter);
    if (!target) {
      pushToast(`No more ${filter === "any" ? "" : filter + " "}patches in that direction`, "info");
      return;
    }
    setPatch(target);
  }

  async function persistPatchFields(fields: Partial<Patch>, advance = false) {
    if (!patch || patch.slide_id !== sid) return;
    setSaveState("saving");
    try {
      const updated = await updatePatch(patch.id, fields as never);
      setPatch(updated);
      setSaveState("saved");
      setGridRefresh((n) => n + 1);
      if (advance && isImage) {
        const target = neighbour(imagesLive, sid, "next");
        if (target) navigate(`/projects/${pid}/slides/${target.slide_id}/workspace`);
      }
    } catch {
      setSaveState("error");
    }
  }

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      const picked = !e.ctrlKey && !e.metaKey && !e.altKey ? HOTKEYS[e.key.toLowerCase()] : undefined;
      if (picked && visibleTools.some((t) => t.id === picked)) setTool(picked);

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
  }, [patch, classes, imagesLive, isImage, sid]);

  if (!slide || !config || !patch || !patchOrigin || isImage === null) {
    return <div className="p-space-xl text-center text-slate-400 bg-[#0a0f1d] h-[calc(100vh-3.5rem)]">Loading workspace...</div>;
  }

  const imageUrl = dynamicPatchUrl(sid, patch.x, patch.y, patch.width, patch.height, patch.level);
  const switching = patch.slide_id !== sid; // the next image is still loading
  const prog = isImage ? progress(imagesLive) : totals;
  const noun = isImage ? "Image" : "Patch";
  const saveLabel = { idle: "", saving: "Saving...", saved: "Saved", error: "Error saving" }[saveState];

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] bg-[#0a0f1d] text-white overflow-hidden">
      {/* Precision strip */}
      <div className="bg-[#0b1329] border-b border-[#1e293b] px-space-md py-1.5 flex items-center gap-space-md text-body-sm flex-wrap">
        <span className="font-headline-sm">{switching ? "Loading..." : slide.filename}</span>
        {isImage ? (
          <span className="font-mono text-label-sm text-cyan-300">
            {patch.width.toLocaleString()} × {patch.height.toLocaleString()} px
          </span>
        ) : (
          <>
            <span className="text-slate-500">Patch #{patch.patch_index} &middot; id {patch.id}</span>
            <span className="font-mono text-label-sm text-cyan-300">
              L0: X={patch.x.toLocaleString()} Y={patch.y.toLocaleString()}
            </span>
          </>
        )}
        <div className="flex-1" />
        <span className="text-slate-400">
          {isImage ? "Progress" : "Slide Progress"}: {prog.total ? Math.round((prog.done / prog.total) * 100) : 0}% ({prog.done}/{prog.total})
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
          {isImage ? (
            <ImageList images={imagesLive} currentId={sid} filter={listFilter} onFilter={setListFilter} onOpen={(id) => navigate(`/projects/${pid}/slides/${id}/workspace`)} />
          ) : (
            <>
              <div className="h-44 relative border-b border-[#1e293b]">
                <WsiViewer slideId={sid} className="w-full h-full" showNavigator={false} showControls={false} onViewportChange={setBbox}>
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
            </>
          )}
        </div>

        {/* Center: canvas */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden bg-[#070d1e]">
          <div className="mx-auto mt-space-sm max-w-[calc(100%-1rem)] shrink-0 z-20 bg-[#0f172a]/95 rounded-xl shadow-2xl flex flex-wrap items-center justify-center gap-1 p-1">
            {visibleTools.map((t) => (
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
            <IconButton icon="zoom_in" onClick={() => setZoom((z) => Math.min(maxZoom, z * 1.25))} />
            <IconButton icon="zoom_out" onClick={() => setZoom((z) => Math.max(0.5, z / 1.25))} />
            <button onClick={() => setZoom(1)} className="text-label-md text-slate-300 px-space-sm">
              Fit
            </button>
          </div>

          <div ref={setAreaEl} className="relative flex-1 min-h-0 overflow-auto flex p-space-lg">
            <div className="m-auto">
            {switching ? (
              <span className="text-slate-500">Loading image...</span>
            ) : (
            <AnnotationCanvas
              key={patch.id}
              imageUrl={imageUrl}
              patchWidth={patch.width}
              patchHeight={patch.height}
              tool={tool}
              zoom={effectiveZoom}
              annotations={annotations}
              classes={classes}
              selectedId={selectedAnnId}
              onSelect={setSelectedAnnId}
              onShapeComplete={handleShapeComplete}
              onShapeEdit={handleShapeEdit}
              onDeleteSelected={handleDeleteSelected}
            />
            )}
            </div>
          </div>
        </div>

        {/* Right: QC panel */}
        <div className="w-80 bg-surface border-l border-[#1e293b] text-on-surface p-space-md flex flex-col gap-space-md overflow-y-auto">
          <h2 className="font-headline-sm text-headline-sm">Annotation & QC</h2>

          <div>
            <div className="text-label-md text-on-surface-variant mb-1">{noun} Label</div>
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
            <div className="text-label-md text-on-surface-variant mb-1">Objects in {noun} ({annotations.length})</div>
            <div className="flex flex-col gap-1">
              {annotations.map((a) => {
                const cls = classes.find((c) => c.id === a.class_id);
                return (
                  <div
                    key={a.id}
                    onClick={() => {
                      setTool("select"); // objects can only be edited with the Select tool
                      setSelectedAnnId(a.id);
                    }}
                    className={`flex items-center justify-between px-space-sm py-1.5 rounded bg-surface-container-lowest shadow-sm cursor-pointer ${
                      selectedAnnId === a.id ? "ring-1 ring-primary" : ""
                    }`}
                  >
                    <span className="flex items-center gap-1.5 text-label-md">
                      <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: cls?.color_hex ?? "#94a3b8" }} />
                      {cls?.name ?? "Unclassed"} #{a.id}
                    </span>
                    <span className="text-label-sm text-on-surface-variant capitalize">{a.type.replace("_", " ")}</span>
                  </div>
                );
              })}
              {annotations.length === 0 && <div className="text-body-sm text-on-surface-variant">No objects yet -- draw one with the tools above.</div>}
            </div>
          </div>

          {selectedAnn && (
            <div className="rounded-lg bg-surface-container-low p-space-sm flex flex-col gap-space-sm" data-testid="selected-object">
              <div className="flex items-center justify-between">
                <span className="text-label-md font-headline-sm capitalize">
                  Edit {selectedAnn.type.replace("_", " ")} #{selectedAnn.id}
                </span>
                <button className="text-label-sm text-error hover:underline" onClick={handleDeleteSelected}>
                  Delete
                </button>
              </div>
              <label className="flex flex-col gap-1 text-label-sm text-on-surface-variant">
                Class
                <select
                  className="input"
                  value={selectedAnn.class_id ?? ""}
                  onChange={(e) => editAnnotation(selectedAnn.id, { class_id: e.target.value === "" ? null : Number(e.target.value) }, "change class")}
                >
                  <option value="">Unclassified</option>
                  {classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <Checkbox label="Unsure about this object" checked={selectedAnn.unsure} onChange={(v) => editAnnotation(selectedAnn.id, { unsure: v }, "mark unsure")} />
              <Checkbox label="Flag this object" checked={selectedAnn.flagged} onChange={(v) => editAnnotation(selectedAnn.id, { flagged: v }, "flag object")} />
              <input
                key={selectedAnn.id}
                className="input"
                placeholder="Note on this object..."
                defaultValue={selectedAnn.notes ?? ""}
                onBlur={(e) => e.target.value !== (selectedAnn.notes ?? "") && editAnnotation(selectedAnn.id, { notes: e.target.value || null }, "edit note")}
              />
              <p className="text-label-sm text-on-surface-variant">
                Drag the shape to move it, or its handles to reshape it. Double-click an outline to add a point, a point to remove it.
              </p>
            </div>
          )}

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
            <Checkbox label={isImage ? "Exclude from Model Training (Blur/Artifact)" : "Exclude from Model Training (Blur/Fold)"} checked={patch.excluded} onChange={(v) => persistPatchFields({ excluded: v })} />
          </div>

          <div className="flex items-center gap-space-sm mt-auto pt-space-sm border-t border-outline-variant">
            <button
              className="flex-1 h-8 rounded bg-surface-container-high text-label-md"
              onClick={() => persistPatchFields({ status: "skipped" }, true)}
            >
              Skip
            </button>
            <button
              className="flex-1 h-8 rounded bg-primary-container text-on-primary-container text-label-md"
              onClick={() => persistPatchFields({ status: "reviewed", reviewed_by: annotatorName }, true)}
            >
              Validate (V)
            </button>
          </div>
        </div>
      </div>

      {/* Bottom footer */}
      <div className="bg-[#0b1329] border-t border-[#1e293b] px-space-md py-space-sm flex items-center justify-between flex-wrap gap-space-sm">
        <Link
          to={isImage ? `/projects/${pid}/images` : `/projects/${pid}/slides/${sid}/gallery`}
          className="text-label-md text-slate-400 hover:text-white flex items-center gap-1"
        >
          <MaterialIcon name="grid_on" className="!text-[16px]" />
          {isImage ? "All images" : "Gallery"}
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

const FILTERS: { key: ImageFilter; label: string }[] = [
  { key: "any", label: "All" },
  { key: "unannotated", label: "To do" },
  { key: "flagged", label: "Flagged" },
];

/** The project's images as a scrollable list; the current one is highlighted and kept in view. */
function ImageList({
  images,
  currentId,
  filter,
  onFilter,
  onOpen,
}: {
  images: ImageSummary[];
  currentId: number;
  filter: ImageFilter;
  onFilter: (f: ImageFilter) => void;
  onOpen: (slideId: number) => void;
}) {
  const shown = images.filter((i) => (filter === "unannotated" ? i.status === "unannotated" : filter === "flagged" ? i.flagged : true));
  const currentRow = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    currentRow.current?.scrollIntoView({ block: "nearest" });
  }, [currentId, filter]);

  return (
    <>
      <div className="p-space-sm border-b border-[#1e293b] flex flex-col gap-space-sm">
        <div className="text-label-sm text-slate-400">Images ({shown.length.toLocaleString()})</div>
        <div className="flex bg-[#0b1329] rounded p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => onFilter(f.key)}
              className={`flex-1 py-1 rounded text-label-sm ${filter === f.key ? "bg-[#1e293b] text-white" : "text-slate-400 hover:text-white"}`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">
        {shown.map((img) => {
          const current = img.slide_id === currentId;
          return (
            <button
              key={img.slide_id}
              ref={current ? currentRow : undefined}
              onClick={() => onOpen(img.slide_id)}
              style={{ contentVisibility: "auto", containIntrinsicSize: "48px" }}
              className={`w-full flex items-center gap-space-sm px-space-sm py-1 text-left border-l-2 ${
                current ? "bg-[#1e293b] border-cyan-400" : "border-transparent hover:bg-[#162036]"
              }`}
            >
              <img src={thumbnailUrl(img.slide_id, 96)} alt="" loading="lazy" className="w-10 h-10 rounded object-cover bg-[#0b1329] shrink-0" />
              <span className="flex-1 min-w-0">
                <span className="block truncate text-label-md text-slate-200" title={img.filename}>
                  {img.filename}
                </span>
                <span className="block text-label-sm text-slate-500">
                  {img.annotation_count} object{img.annotation_count === 1 ? "" : "s"}
                </span>
              </span>
              {img.flagged && <MaterialIcon name="flag" className="!text-[14px] text-red-400" />}
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                title={img.status}
                style={{ backgroundColor: PATCH_STATUS_COLORS[img.status] ?? "#64748b" }}
              />
            </button>
          );
        })}
        {shown.length === 0 && <div className="p-space-md text-body-sm text-slate-500">Nothing here.</div>}
      </div>
    </>
  );
}
