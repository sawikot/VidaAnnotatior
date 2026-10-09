import OpenSeadragon from "openseadragon";
import { useAuthStore } from "../../stores/authStore";
import { useEffect, useMemo, useRef, useState } from "react";
import { IconButton } from "../../components/primitives";
import {
  createAnnotation,
  createSlideAnnotation,
  deleteAnnotation,
  getPatch,
  listSlideAnnotations,
  updateAnnotation,
} from "../../services/api";
import { useAnnotationStore } from "../../stores/annotationStore";
import { useUiStore } from "../../stores/uiStore";
import type { ConfigVersion, GeometryAnnotation, GeometryType, Slide, Suggestion } from "../../types/api";
import type { Point } from "../../utils/coordinates";
import type { BrushChange } from "../../utils/brush";
import { shapeBounds } from "../../utils/shapes";
import { level0ToLocal, toLevel0Shape } from "../../utils/slideProjection";
import { PatchGridOverlay } from "../viewer/PatchGridOverlay";
import { WsiViewer, type ViewportBbox } from "../viewer/WsiViewer";
import { AnnotationModeSwitch, type AnnotationMode } from "./AnnotationModeSwitch";
import { ToolOptions } from "./BrushOptions";
import { ImportAnnotationsButton } from "./ImportAnnotationsButton";
import { SuggestionOverlay } from "../suggestions/SuggestionOverlay";
import { WsiSuggestionsPanel } from "../suggestions/WsiSuggestionsPanel";
import { ShapeLayer } from "./ShapeLayer";
import { HOTKEYS, PAN_TOOL, visibleTools } from "./tools";
import { useAnnotationHistory } from "./useAnnotationHistory";

/** Where to bring the view when the whole-slide mode opens: a patch's footprint, or a shape to select. */
export interface WsiFocus {
  annotationId?: number;
  rect?: { x: number; y: number; width: number; height: number };
}

interface Props {
  slide: Slide;
  config: ConfigVersion;
  /** Shared with the patch view, which shows these projected into each patch. */
  slideAnnotations: GeometryAnnotation[];
  setSlideAnnotations: React.Dispatch<React.SetStateAction<GeometryAnnotation[]>>;
  focus: WsiFocus | null;
  onModeChange: (mode: AnnotationMode) => void;
  /** Open the patch a patch-drawn annotation belongs to, with that annotation selected. */
  onOpenPatch: (patchId: number, annotationId: number) => void;
  /** Open a patch to go through a model's suggestions in it. */
  onReviewPatch: (patchId: number) => void;
}

type Fields = Partial<Pick<GeometryAnnotation, "type" | "class_id" | "unsure" | "flagged" | "notes" | "coordinates_level0">>;

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

/**
 * Annotating directly on the whole slide. Shapes are drawn in Level-0 pixels -- the master coordinate
 * space -- and stored as they are, belonging to no patch. Annotations drawn in patches appear faintly
 * for context; the patch view shows these the same way, projected into each patch.
 */
export function WsiAnnotationView({ slide, config, slideAnnotations, setSlideAnnotations, focus, onModeChange, onOpenPatch, onReviewPatch }: Props) {
  const pushToast = useUiStore((s) => s.pushToast);
  const annotatorName = useAuthStore((s) => s.user?.name ?? "");
  const tool = useAnnotationStore((s) => s.tool);
  const setTool = useAnnotationStore((s) => s.setTool);
  const activeClassId = useAnnotationStore((s) => s.activeClassId);
  const setActiveClassId = useAnnotationStore((s) => s.setActiveClassId);
  const saveState = useAnnotationStore((s) => s.saveState);
  const setSaveState = useAnnotationStore((s) => s.setSaveState);
  const history = useAnnotationHistory();

  const classes = config.annotation_classes;
  const tools = useMemo(() => [PAN_TOOL, ...visibleTools(config.enabled_tools)], [config.enabled_tools]);
  const width = slide.width_l0 ?? 0;
  const height = slide.height_l0 ?? 0;

  const [patchDrawn, setPatchDrawn] = useState<GeometryAnnotation[]>([]);
  const [showPatchDrawn, setShowPatchDrawn] = useState(true);
  const [showGrid, setShowGrid] = useState(false);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]); // a model's, waiting anywhere on the slide
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [bbox, setBbox] = useState<ViewportBbox | null>(null);
  const [scale, setScale] = useState(0.05);
  const [spaceHeld, setSpaceHeld] = useState(false);
  const viewerRef = useRef<OpenSeadragon.Viewer | null>(null);
  const [viewerReady, setViewerReady] = useState(false);
  const gestureRef = useRef(false);

  // Space held = pan, whatever tool is picked. The viewer asks (through blockPan) before each drag.
  const effectiveTool = spaceHeld ? "pan" : tool;
  const effectiveToolRef = useRef(effectiveTool);
  effectiveToolRef.current = effectiveTool;
  const blockPan = () => {
    const t = effectiveToolRef.current;
    return t !== "pan" && (t !== "select" || gestureRef.current);
  };

  // ---- ids: a recreated annotation (undo of a delete, redo of a create) gets a new database id, so undo/redo
  // commands refer to annotations by a stable key and look the current id up when they run.
  const keyToId = useRef(new Map<number, number>());
  const idToKey = useRef(new Map<number, number>());
  const nextKey = useRef(1);
  const keyOf = (id: number) => {
    let key = idToKey.current.get(id);
    if (key === undefined) {
      key = nextKey.current++;
      idToKey.current.set(id, key);
      keyToId.current.set(key, id);
    }
    return key;
  };
  const idOf = (key: number) => keyToId.current.get(key) as number;
  const rebind = (key: number, id: number) => {
    keyToId.current.set(key, id);
    idToKey.current.set(id, key);
  };

  const shapes = useMemo(() => {
    const own = slideAnnotations.map(toLevel0Shape);
    if (!showPatchDrawn) return own;
    const inPatches = patchDrawn.map((a) => {
      const b = a.patch_bounds_l0;
      return { ...toLevel0Shape(a), bounds: b ? { x0: b[0], y0: b[1], x1: b[2], y1: b[3] } : undefined };
    });
    return [...inPatches, ...own];
  }, [slideAnnotations, patchDrawn, showPatchDrawn]);
  const isPatchDrawn = (id: number | null) => patchDrawn.some((a) => a.id === id);
  const selected = slideAnnotations.find((a) => a.id === selectedId) ?? patchDrawn.find((a) => a.id === selectedId) ?? null;
  const selectedInPatch = selected != null && selected.patch_id != null;

  useEffect(() => {
    setTool("pan"); // start on the safe tool: a stray drag must move the view, not draw
    let cancelled = false;
    listSlideAnnotations(slide.id, "patch").then((list) => !cancelled && setPatchDrawn(list));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slide.id]);

  useEffect(() => {
    if (!activeClassId && classes.length) setActiveClassId(classes[0].id);
  }, [classes.length]);

  // ---- view: fit a patch or a shape when asked to
  function fitRect(x: number, y: number, w: number, h: number) {
    const viewer = viewerRef.current;
    if (!viewer) return;
    const margin = Math.max(w, h) * 0.15;
    viewer.viewport.fitBounds(viewer.viewport.imageToViewportRectangle(new OpenSeadragon.Rect(x - margin, y - margin, w + 2 * margin, h + 2 * margin)));
  }

  useEffect(() => {
    if (!viewerReady || !focus) return;
    if (focus.annotationId != null) {
      const target = slideAnnotations.find((a) => a.id === focus.annotationId);
      if (target) {
        setSelectedId(target.id);
        setTool("select");
        const b = shapeBounds(target.type, target.coordinates_level0 as Point[]);
        fitRect(b.minX, b.minY, Math.max(b.maxX - b.minX, 200), Math.max(b.maxY - b.minY, 200));
        return;
      }
    }
    if (focus.rect) fitRect(focus.rect.x, focus.rect.y, focus.rect.width, focus.rect.height);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerReady, focus]);

  // ---- saving
  async function guarded(label: string, work: () => Promise<void>) {
    setSaveState("saving");
    try {
      await work();
      setSaveState("saved");
    } catch (e) {
      setSaveState("error");
      pushToast(e instanceof Error ? e.message : `Failed to ${label}`, "error");
    }
  }

  async function createShape(type: GeometryType, points: Point[]) {
    if (!activeClassId) {
      pushToast("Select a diagnostic class first", "error");
      return;
    }
    const body = { type, class_id: activeClassId, coordinates_level0: points, created_by: annotatorName };
    await guarded("save annotation", async () => {
      const created = await createSlideAnnotation(slide.id, body);
      setSlideAnnotations((prev) => [...prev, created]);
      const key = keyOf(created.id);
      history.push({
        label: `create ${type}`,
        do: async () => {
          const again = await createSlideAnnotation(slide.id, body);
          rebind(key, again.id);
          setSlideAnnotations((prev) => [...prev, again]);
        },
        undo: async () => {
          const id = idOf(key);
          await deleteAnnotation(id);
          setSlideAnnotations((prev) => prev.filter((a) => a.id !== id));
        },
      });
    });
  }

  /** Change an existing annotation: it shows at once, saves, and can be undone. */
  async function editAnnotation(id: number, fields: Fields, label: string) {
    const inPatch = isPatchDrawn(id);
    const setList = inPatch ? setPatchDrawn : setSlideAnnotations;
    const target = (inPatch ? patchDrawn : slideAnnotations).find((a) => a.id === id);
    if (!target) return;
    const key = keyOf(id);
    const before = Object.fromEntries(Object.keys(fields).map((k) => [k, target[k as keyof Fields]])) as Fields;
    const send = async (f: Fields) => {
      const updated = await updateAnnotation(idOf(key), f);
      setList((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
    };
    setList((prev) => prev.map((a) => (a.id === id ? { ...a, ...fields } : a)));
    setSaveState("saving");
    try {
      await send(fields);
      setSaveState("saved");
      history.push({ label, do: () => send(fields), undo: () => send(before) });
    } catch (e) {
      setList((prev) => prev.map((a) => (a.id === id ? target : a))); // put back what the server still has
      setSaveState("error");
      pushToast(e instanceof Error ? e.message : "Failed to save the change", "error");
    }
  }

  const deleteSelected = () => (selected ? deleteOne(selected) : undefined);

  async function deleteOne(target: GeometryAnnotation) {
    const patchId = target.patch_id;
    const setList = patchId != null ? setPatchDrawn : setSlideAnnotations;
    const key = keyOf(target.id);
    const common = {
      type: target.type,
      class_id: target.class_id,
      created_by: target.created_by ?? annotatorName,
      notes: target.notes ?? undefined,
      unsure: target.unsure,
      flagged: target.flagged,
    };
    // Undo puts it back where it was: in its patch (in that patch's pixels), or on the whole slide.
    const recreate = () =>
      patchId != null
        ? createAnnotation(patchId, { ...common, coordinates_patch_local: target.coordinates_patch_local as Point[] })
        : createSlideAnnotation(slide.id, { ...common, coordinates_level0: target.coordinates_level0 as Point[] });
    await guarded("delete annotation", async () => {
      await deleteAnnotation(target.id);
      setList((prev) => prev.filter((a) => a.id !== target.id));
      setSelectedId((id) => (id === target.id ? null : id));
      history.push({
        label: "delete annotation",
        do: async () => {
          const id = idOf(key);
          await deleteAnnotation(id);
          setList((prev) => prev.filter((a) => a.id !== id));
        },
        undo: async () => {
          const again = await recreate();
          rebind(key, again.id);
          setList((prev) => [...prev, again]);
        },
      });
    });
  }

  /** A piece the eraser cut off a shape: a shape like the one it came from, kept where that one is kept. */
  async function createLike(source: GeometryAnnotation, points: Point[]) {
    const patchId = source.patch_id;
    const setList = patchId != null ? setPatchDrawn : setSlideAnnotations;
    const common = {
      type: "freehand" as const,
      class_id: source.class_id,
      created_by: annotatorName,
      notes: source.notes ?? undefined,
      unsure: source.unsure,
      flagged: source.flagged,
    };
    await guarded("save annotation", async () => {
      // A piece of a patch's shape stays in that patch, in that patch's pixels.
      const owner = patchId != null ? await getPatch(patchId) : null;
      const make = () =>
        owner
          ? createAnnotation(owner.id, { ...common, coordinates_patch_local: points.map((pt) => level0ToLocal(owner, pt)) })
          : createSlideAnnotation(slide.id, { ...common, coordinates_level0: points });
      const created = await make();
      setList((prev) => [...prev, created]);
      const key = keyOf(created.id);
      history.push({
        label: "split shape",
        do: async () => {
          const again = await make();
          rebind(key, again.id);
          setList((prev) => [...prev, again]);
        },
        undo: async () => {
          const id = idOf(key);
          await deleteAnnotation(id);
          setList((prev) => prev.filter((a) => a.id !== id));
        },
      });
    });
  }

  /** One brush stroke: it may reshape, split or remove several shapes, and is undone as one step. */
  async function applyBrush(changes: BrushChange[]) {
    const find = (id: number) => slideAnnotations.find((a) => a.id === id) ?? patchDrawn.find((a) => a.id === id);
    await history.batch("brush stroke", async () => {
      for (const change of changes) {
        if (change.kind === "create" && change.like === null) {
          await createShape("freehand", change.points);
          continue;
        }
        const target = find(change.kind === "create" ? (change.like as number) : change.id);
        if (!target) continue;
        if (change.kind === "edit") await editAnnotation(target.id, { type: change.type, coordinates_level0: change.points }, "brush");
        else if (change.kind === "delete") await deleteOne(target);
        else await createLike(target, change.points);
      }
    });
  }

  // ---- keys: tool hotkeys, class digits, undo/redo, hold Space to pan
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (isTyping(e.target)) return;
      if (e.key === " ") {
        e.preventDefault();
        setSpaceHeld(true);
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        e.shiftKey ? history.redo() : history.undo();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const picked = HOTKEYS[e.key.toLowerCase()];
      if (picked && tools.some((t) => t.id === picked)) setTool(picked);
      const classIdx = Number(e.key) - 1;
      if (!Number.isNaN(classIdx) && classes[classIdx]) setActiveClassId(classes[classIdx].id);
    }
    const onKeyUp = (e: KeyboardEvent) => e.key === " " && setSpaceHeld(false);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tools, classes]);

  const saveLabel = { idle: "", saving: "Saving...", saved: "Saved", error: "Error saving" }[saveState];

  /** Shapes arrived from outside the drawing tools (an import, accepted suggestions): they land on the slide and
   * in its patches, so show both again, and drop undo steps that predate them. */
  function afterImport() {
    history.reset();
    listSlideAnnotations(slide.id, "slide").then(setSlideAnnotations).catch(() => undefined);
    listSlideAnnotations(slide.id, "patch").then(setPatchDrawn).catch(() => undefined);
  }

  if (!width || !height) {
    return <div className="p-space-xl text-center text-slate-400 bg-[#0a0f1d] h-[calc(100vh-3.5rem)]">This slide has no known size yet; re-import it.</div>;
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] bg-[#0a0f1d] text-white overflow-hidden">
      {/* Strip */}
      <div className="bg-[#0b1329] border-b border-[#1e293b] px-space-md py-1.5 flex items-center gap-space-md text-body-sm flex-wrap">
        <span className="font-headline-sm">{slide.filename}</span>
        <AnnotationModeSwitch mode="wsi" onChange={onModeChange} />
        <span className="font-mono text-label-sm text-cyan-300">
          {width.toLocaleString()} × {height.toLocaleString()} px · Level-0
        </span>
        <ImportAnnotationsButton slide={slide} onImported={afterImport} />
        <div className="flex-1" />
        <span className="text-slate-400">{slideAnnotations.length} slide-level object{slideAnnotations.length === 1 ? "" : "s"}</span>
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
        {/* Center: toolbar + the slide */}
        <div className="flex-1 min-w-0 flex flex-col overflow-hidden bg-[#070d1e]">
          <div className="mx-auto mt-space-sm max-w-[calc(100%-1rem)] shrink-0 z-20 bg-[#0f172a]/95 rounded-xl shadow-2xl flex flex-wrap items-center justify-center gap-1 p-1">
            {tools.map((t) => (
              <IconButton key={t.id} icon={t.icon} active={effectiveTool === t.id} onClick={() => setTool(t.id)} title={`${t.label} (${t.key})`} />
            ))}
            <div className="w-px h-6 bg-slate-700 mx-1" />
            {classes.map((c, i) => (
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
                {i < 9 && <span className="font-mono">{i + 1}</span>}
                {c.name}
              </button>
            ))}
            <div className="w-px h-6 bg-slate-700 mx-1" />
            <ToggleChip label="Patch grid" checked={showGrid} onChange={setShowGrid} />
            <ToggleChip label={`Patch annotations (${patchDrawn.length})`} checked={showPatchDrawn} onChange={setShowPatchDrawn} />
          </div>

          <ToolOptions tool={tool} />

          <div className="relative flex-1 min-h-0 mt-space-sm">
            <div className="absolute inset-0">
            <WsiViewer
              slideId={slide.id}
              version={slide.image_version}
              className="w-full h-full"
              keyboardNav={false}
              blockPan={blockPan}
              onViewportChange={(b, _zoom, s) => {
                setBbox(b);
                setScale(s);
              }}
              onViewerReady={(v) => {
                viewerRef.current = v;
                setViewerReady(true);
              }}
            >
              {showGrid && <PatchGridOverlay slideId={slide.id} bbox={bbox} />}
              <ShapeLayer
                width={width}
                height={height}
                scale={scale}
                tool={effectiveTool}
                shapes={shapes}
                classes={classes}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onShapeComplete={createShape}
                onShapeEdit={(id, points) => editAnnotation(id, { coordinates_level0: points }, "edit shape")}
                onDeleteSelected={deleteSelected}
                onBrush={applyBrush}
                gestureRef={gestureRef}
                resetKey={slide.id}
              />
              <SuggestionOverlay suggestions={suggestions} classes={classes} scale={scale} space="level0" />
            </WsiViewer>
            </div>
            <div className="absolute top-3 left-3 z-10 px-space-sm py-1 rounded bg-black/50 text-label-sm text-slate-300 pointer-events-none">
              {effectiveTool === "pan" ? "Drag to move around · scroll to zoom" : "Hold Space to move around · scroll to zoom"}
            </div>
          </div>
        </div>

        {/* Right: objects + editor */}
        <div className="w-80 bg-surface border-l border-[#1e293b] text-on-surface p-space-md flex flex-col gap-space-md overflow-y-auto">
          <h2 className="font-headline-sm text-headline-sm">Whole-slide annotations</h2>
          <p className="text-body-sm text-on-surface-variant">
            Drawn on the slide itself and stored in Level-0 pixels, so they are not tied to any patch. Patch views show them too.
          </p>

          <WsiSuggestionsPanel projectId={slide.project_id} slideId={slide.id} onShown={setSuggestions} onAccepted={afterImport} onOpenPatch={onReviewPatch} />

          <div>
            <div className="text-label-md text-on-surface-variant mb-1">Objects on the slide ({slideAnnotations.length})</div>
            <div className="flex flex-col gap-1">
              {slideAnnotations.map((a) => {
                const cls = classes.find((c) => c.id === a.class_id);
                return (
                  <div
                    key={a.id}
                    onClick={() => {
                      setTool("select");
                      setSelectedId(a.id);
                      const b = shapeBounds(a.type, a.coordinates_level0 as Point[]);
                      fitRect(b.minX, b.minY, Math.max(b.maxX - b.minX, 200), Math.max(b.maxY - b.minY, 200));
                    }}
                    className={`flex items-center justify-between px-space-sm py-1.5 rounded bg-surface-container-lowest shadow-sm cursor-pointer ${
                      selectedId === a.id ? "ring-1 ring-primary" : ""
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
              {slideAnnotations.length === 0 && (
                <div className="text-body-sm text-on-surface-variant">Nothing yet. Pick a tool above and draw on the slide.</div>
              )}
            </div>
          </div>

          {showPatchDrawn && patchDrawn.length > 0 && (
            <div>
              <div className="text-label-md text-on-surface-variant mb-1">Drawn in patches ({patchDrawn.length})</div>
              <div className="flex flex-col gap-1 max-h-60 overflow-y-auto">
                {patchDrawn.map((a) => {
                  const cls = classes.find((c) => c.id === a.class_id);
                  return (
                    <div
                      key={a.id}
                      onClick={() => {
                        setTool("select");
                        setSelectedId(a.id);
                        const b = shapeBounds(a.type, a.coordinates_level0 as Point[]);
                        fitRect(b.minX, b.minY, Math.max(b.maxX - b.minX, 200), Math.max(b.maxY - b.minY, 200));
                      }}
                      className={`flex items-center justify-between px-space-sm py-1.5 rounded bg-surface-container-lowest shadow-sm cursor-pointer ${
                        selectedId === a.id ? "ring-1 ring-primary" : ""
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
              </div>
            </div>
          )}

          {selected && (
            <div className="rounded-lg bg-surface-container-low p-space-sm flex flex-col gap-space-sm" data-testid="selected-object">
              <div className="flex items-center justify-between">
                <span className="text-label-md font-headline-sm capitalize">
                  Edit {selected.type.replace("_", " ")} #{selected.id}
                </span>
                <button className="text-label-sm text-error hover:underline" onClick={deleteSelected}>
                  Delete
                </button>
              </div>
              <label className="flex flex-col gap-1 text-label-sm text-on-surface-variant">
                Class
                <select
                  className="input"
                  value={selected.class_id ?? ""}
                  onChange={(e) => editAnnotation(selected.id, { class_id: e.target.value === "" ? null : Number(e.target.value) }, "change class")}
                >
                  <option value="">Unclassified</option>
                  {classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-space-sm text-body-md cursor-pointer">
                <input type="checkbox" className="w-4 h-4" checked={selected.unsure} onChange={(e) => editAnnotation(selected.id, { unsure: e.target.checked }, "mark unsure")} />
                Unsure about this object
              </label>
              <label className="flex items-center gap-space-sm text-body-md cursor-pointer">
                <input type="checkbox" className="w-4 h-4" checked={selected.flagged} onChange={(e) => editAnnotation(selected.id, { flagged: e.target.checked }, "flag object")} />
                Flag this object
              </label>
              <input
                key={selected.id}
                className="input"
                placeholder="Note on this object..."
                defaultValue={selected.notes ?? ""}
                onBlur={(e) => e.target.value !== (selected.notes ?? "") && editAnnotation(selected.id, { notes: e.target.value || null }, "edit note")}
              />
              {selectedInPatch && (
                <div className="flex items-center justify-between gap-space-sm text-label-sm text-on-surface-variant">
                  <span>Drawn in patch {selected.patch_id}; it stays inside that patch.</span>
                  <button
                    className="text-primary hover:underline shrink-0"
                    onClick={() => selected.patch_id != null && onOpenPatch(selected.patch_id, selected.id)}
                  >
                    Open in patch view
                  </button>
                </div>
              )}
              <p className="text-label-sm text-on-surface-variant">
                Drag the shape to move it, or its handles to reshape it. Double-click an outline to add a point, a point to remove it.
              </p>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}

function ToggleChip({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      aria-pressed={checked}
      onClick={() => onChange(!checked)}
      className={`px-space-sm h-8 rounded text-label-md ${checked ? "bg-[#1e293b] text-white" : "text-slate-500 hover:text-slate-300"}`}
    >
      {label}
    </button>
  );
}
