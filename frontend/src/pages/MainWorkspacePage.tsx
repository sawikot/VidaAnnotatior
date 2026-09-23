import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { IconButton } from "../components/primitives";
import { AnnotationCanvas } from "../features/annotations/AnnotationCanvas";
import { useAnnotationHistory } from "../features/annotations/useAnnotationHistory";
import { neighbour, progress, type ImageFilter } from "../features/images/imageNav";
import { AnnotationModeSwitch, rememberMode, rememberedMode, type AnnotationMode } from "../features/annotations/AnnotationModeSwitch";
import { WsiAnnotationView, type WsiFocus } from "../features/annotations/WsiAnnotationView";
import { HOTKEYS, visibleTools as toolsFor } from "../features/annotations/tools";
import { PatchGridOverlay, PATCH_STATUS_COLORS } from "../features/viewer/PatchGridOverlay";
import { WsiViewer, type ViewportBbox } from "../features/viewer/WsiViewer";
import {
  createAnnotation,
  createSlideAnnotation,
  deleteAnnotation,
  dynamicPatchUrl,
  getConfig,
  getPatch,
  getProject,
  getSlide,
  listImages,
  listOverlappingAnnotations,
  listPatchAnnotations,
  listSlideAnnotations,
  listPatches,
  nextPatch,
  thumbnailUrl,
  updateAnnotation,
  updatePatch,
} from "../services/api";
import { useAnnotationStore } from "../stores/annotationStore";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";
import type { ConfigVersion, GeometryAnnotation, GeometryType, ImageSummary, OverlappingAnnotation, Patch, Slide } from "../types/api";
import type { Point } from "../utils/coordinates";
import type { LayerShape } from "../features/annotations/ShapeLayer";
import { level0ToLocal, localToLevel0, localToLocal, projectSlideShapes } from "../utils/slideProjection";

type AnnotationFields = Partial<
  Pick<GeometryAnnotation, "class_id" | "unsure" | "flagged" | "notes" | "coordinates_patch_local" | "coordinates_level0">
>;

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
  // Annotations drawn in overlapping patches (stride < patch size) that reach into this one.
  const [borrowed, setBorrowed] = useState<OverlappingAnnotation[]>([]);
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

  // Annotate one patch at a time ("patch"), or directly on the whole slide ("wsi"). Image projects have
  // no whole slide to speak of: an image is its own patch.
  const [mode, setModeState] = useState<AnnotationMode>(() => {
    const asked = searchParams.get("mode");
    return asked === "wsi" || asked === "patch" ? asked : rememberedMode();
  });
  const [slideAnnotations, setSlideAnnotations] = useState<GeometryAnnotation[]>([]); // drawn on the whole slide
  const [wsiFocus, setWsiFocus] = useState<WsiFocus | null>(null);
  const [noPatches, setNoPatches] = useState(false);
  const wsiActive = !isImage && mode === "wsi"; // the whole-slide view has taken over the page
  const pendingSelect = useRef<number | null>(null); // an annotation to select once its patch has loaded

  function setMode(next: AnnotationMode) {
    setModeState(next);
    rememberMode(next);
    history.reset();
    // Pick up changes made elsewhere (another tab, another annotator) each time the view changes.
    listSlideAnnotations(sid, "slide").then(setSlideAnnotations).catch(() => undefined);
    setSearchParams((prev) => {
      const params = new URLSearchParams(prev);
      params.set("mode", next);
      return params;
    });
  }

  function showPatchOnSlide() {
    setWsiFocus(patch ? { rect: { x: patch.x, y: patch.y, width: patch.width_l0, height: patch.height_l0 } } : null);
    setMode("wsi");
  }

  function showAnnotationOnSlide(annotationId: number) {
    setWsiFocus({ annotationId });
    setMode("wsi");
  }

  async function openPatchFromSlide(patchId: number, annotationId: number) {
    try {
      if (patch?.id === patchId) {
        // Already the patch on screen: nothing will reload, so select the annotation right away.
        setSelectedAnnId(annotationId);
        setTool("select");
      } else {
        pendingSelect.current = annotationId; // selected once that patch's annotations have loaded
        setPatch(await getPatch(patchId));
      }
      setMode("patch");
    } catch {
      pendingSelect.current = null;
      pushToast("Could not open that patch", "error");
    }
  }

  useEffect(() => {
    listSlideAnnotations(sid, "slide").then(setSlideAnnotations).catch(() => setSlideAnnotations([]));
  }, [sid]);

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
        setNoPatches(list.items.length === 0);
      } else {
        const list = await listPatches(sid, { limit: 1 });
        setPatch(list.items[0] ?? null);
        setNoPatches(list.items.length === 0);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid]);

  useEffect(() => {
    if (!patch) return;
    listPatchAnnotations(patch.id).then((list) => {
      setAnnotations(list);
      if (pendingSelect.current !== null) {
        // arrived from the whole-slide view by pressing one of this patch's own annotations
        if (list.some((a) => a.id === pendingSelect.current)) {
          setSelectedAnnId(pendingSelect.current);
          setTool("select");
        }
        pendingSelect.current = null;
      }
    });
    setNotes(patch.notes ?? "");
    history.reset();
    if (pendingSelect.current === null) setSelectedAnnId(null);
    if (isImage !== true) {
      // Replace, not push: which patch is open is not a step of its own, so Back leaves the workspace.
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("patch", String(patch.id));
          return next;
        },
        { replace: true },
      );
      refreshTotals();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patch?.id]);

  // Fetch the next patch's image in the background while this one is annotated, so moving on
  // (D, or Next) shows it at once: the browser keeps it, as it is asked for with the slide's version.
  useEffect(() => {
    if (!patch || isImage !== false || !slide || patch.slide_id !== sid) return;
    let cancelled = false;
    const version = slide.image_version;
    nextPatch(sid, patch.patch_index, "next")
      .then((next) => {
        if (cancelled || !next) return;
        const img = new Image();
        img.src = dynamicPatchUrl(sid, next.x, next.y, next.width, next.height, next.level, version);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patch?.id, isImage, slide?.image_version, sid]);

  useEffect(() => {
    if (!patch || isImage !== false || patch.slide_id !== sid) {
      setBorrowed([]);
      return;
    }
    let cancelled = false;
    listOverlappingAnnotations(patch.id)
      .then((list) => !cancelled && setBorrowed(list))
      .catch(() => !cancelled && setBorrowed([]));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patch?.id, isImage, mode]);

  function refreshTotals() {
    listPatches(sid, { limit: 1 }).then((r) => setTotals((t) => ({ ...t, total: r.total })));
    listPatches(sid, { status: "annotated,reviewed", limit: 1 }).then((r) => setTotals((t) => ({ ...t, done: r.total })));
  }

  const classes = config?.annotation_classes ?? [];
  const borrowedEntry = (id: number | null) => borrowed.find((o) => o.annotation.id === id) ?? null;
  const slideLevel = (id: number | null) => slideAnnotations.find((a) => a.id === id) ?? null;
  const selectedAnn =
    annotations.find((a) => a.id === selectedAnnId) ?? borrowedEntry(selectedAnnId)?.annotation ?? slideLevel(selectedAnnId) ?? null;
  const selectedIsSlideLevel = slideLevel(selectedAnnId) !== null;
  const selectedOwner = borrowedEntry(selectedAnnId)?.owner ?? null;

  // The project's configuration decides which drawing tools are offered; Select is always there.
  const visibleTools = toolsFor(config?.enabled_tools);
  useEffect(() => {
    // The remembered tool is switched off in this project, or is the whole-slide-only Pan (which the slide view manages itself).
    if (config && !wsiActive && !visibleTools.some((t) => t.id === tool)) setTool("select");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.id, tool, wsiActive]);

  useEffect(() => {
    if (!activeClassId && classes.length) setActiveClassId(classes[0].id);
  }, [classes.length]);

  // Every patch opens fitted to the window, whatever its size (a 2048 px patch shrinks to fit, a small
  // crop is enlarged); `zoom` is a multiplier on that fit. Out to half the fit, in to 4 (images: 8)
  // screen pixels per patch pixel.
  const fitZoom =
    patch && area.w > 0 ? Math.max(0.02, Math.min(8, (area.w - 48) / patch.width, (area.h - 48) / patch.height)) : 1;
  const effectiveZoom = fitZoom * zoom;
  const MIN_ZOOM = 0.5;
  const maxZoom = Math.max(1, (isImage ? 8 : 4) / fitZoom);
  const clampZoom = (z: number) => Math.min(maxZoom, Math.max(MIN_ZOOM, z));

  // Zoom about a point: the patch pixel under the cursor stays under the cursor. The scroll position
  // is corrected after the new size is laid out.
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const zoomAnchor = useRef<{ px: number; py: number; clientX: number; clientY: number } | null>(null);
  const zoomRef = useRef({ zoom, effectiveZoom, clampZoom });
  zoomRef.current = { zoom, effectiveZoom, clampZoom };

  function zoomAt(factor: number, clientX?: number, clientY?: number) {
    const { zoom: z, effectiveZoom: ez, clampZoom: clamp } = zoomRef.current;
    const next = clamp(z * factor);
    if (next === z) return;
    const box = canvasWrapRef.current?.getBoundingClientRect();
    const area = areaEl?.getBoundingClientRect();
    if (box && area) {
      const cx = clientX ?? area.left + area.width / 2; // buttons zoom about the middle of the view
      const cy = clientY ?? area.top + area.height / 2;
      zoomAnchor.current = { px: (cx - box.left) / ez, py: (cy - box.top) / ez, clientX: cx, clientY: cy };
    }
    setZoom(next);
  }

  useLayoutEffect(() => {
    const anchor = zoomAnchor.current;
    const box = canvasWrapRef.current?.getBoundingClientRect();
    zoomAnchor.current = null;
    if (!anchor || !box || !areaEl) return;
    areaEl.scrollLeft += box.left + anchor.px * effectiveZoom - anchor.clientX;
    areaEl.scrollTop += box.top + anchor.py * effectiveZoom - anchor.clientY;
  }, [effectiveZoom, areaEl]);

  // Mouse: the wheel zooms about the cursor; dragging with the middle button moves the patch around.
  useEffect(() => {
    if (!areaEl) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY; // lines -> pixels
      zoomAt(Math.exp(-delta * 0.0015), e.clientX, e.clientY);
    };
    let drag: { x: number; y: number } | null = null;
    const onDown = (e: MouseEvent) => {
      if (e.button !== 1) return;
      e.preventDefault(); // no autoscroll cursor
      drag = { x: e.clientX, y: e.clientY };
      areaEl.style.cursor = "grabbing";
    };
    const onMove = (e: MouseEvent) => {
      if (!drag) return;
      areaEl.scrollLeft -= e.clientX - drag.x;
      areaEl.scrollTop -= e.clientY - drag.y;
      drag = { x: e.clientX, y: e.clientY };
    };
    const onUp = () => {
      drag = null;
      areaEl.style.cursor = "";
    };
    areaEl.addEventListener("wheel", onWheel, { passive: false });
    areaEl.addEventListener("mousedown", onDown);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      areaEl.removeEventListener("wheel", onWheel);
      areaEl.removeEventListener("mousedown", onDown);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [areaEl]);

  // Whole-slide annotations as they lie in this patch; edited here, they may be moved anywhere on the slide.
  const onSlide = useMemo<LayerShape[]>(() => {
    if (!patch || isImage || !slide?.width_l0 || !slide.height_l0) return [];
    const sx = patch.width_l0 / patch.width;
    const sy = patch.height_l0 / patch.height;
    const bounds = { x0: -patch.x / sx, y0: -patch.y / sy, x1: (slide.width_l0 - patch.x) / sx, y1: (slide.height_l0 - patch.y) / sy };
    return projectSlideShapes(slideAnnotations, patch).map((shape) => ({ ...shape, bounds }));
  }, [slideAnnotations, patch, isImage, slide?.width_l0, slide?.height_l0]);

  // Overlapping patches' annotations, in this patch's pixels. Each is kept inside its own patch when moved.
  const borrowedShapes = useMemo<LayerShape[]>(() => {
    if (!patch) return [];
    const sx = patch.width_l0 / patch.width;
    const sy = patch.height_l0 / patch.height;
    return borrowed.map(({ annotation: a, owner }) => ({
      id: a.id,
      type: a.type,
      points: (a.coordinates_level0 as [number, number][]).map((pt) => level0ToLocal(patch, pt)),
      class_id: a.class_id,
      unsure: a.unsure,
      excluded: a.excluded,
      bounds: {
        x0: (owner.x - patch.x) / sx,
        y0: (owner.y - patch.y) / sy,
        x1: (owner.x + owner.width_l0 - patch.x) / sx,
        y1: (owner.y + owner.height_l0 - patch.y) / sy,
      },
    }));
  }, [borrowed, patch]);

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

  /** Like editAnnotation, for an annotation of an overlapping patch; `fields` are in its owner's pixels. */
  async function editBorrowed(id: number, fields: AnnotationFields, label: string) {
    const entry = borrowedEntry(id);
    if (!entry || patch?.slide_id !== sid) return;
    const target = entry.annotation;
    const before = Object.fromEntries(Object.keys(fields).map((key) => [key, target[key as keyof AnnotationFields]])) as AnnotationFields;
    const replace = (updated: GeometryAnnotation) =>
      setBorrowed((prev) => prev.map((o) => (o.annotation.id === id ? { ...o, annotation: updated } : o)));
    const send = async (f: AnnotationFields) => replace(await updateAnnotation(id, f));

    const shown = { ...target, ...fields };
    if (fields.coordinates_patch_local) {
      shown.coordinates_level0 = (fields.coordinates_patch_local as Point[]).map((pt) => localToLevel0(entry.owner, pt));
    }
    setSaveState("saving");
    replace(shown);
    try {
      await send(fields);
      setSaveState("saved");
      history.push({ label, do: () => send(fields), undo: () => send(before) });
    } catch (e) {
      replace(target);
      setSaveState("error");
      pushToast(e instanceof Error ? e.message : "Failed to save the change", "error");
    }
  }

  /** Like editAnnotation, for an annotation drawn on the whole slide (Level-0 coordinates only). */
  async function editSlideLevel(id: number, fields: AnnotationFields, label: string) {
    const target = slideLevel(id);
    if (!target || patch?.slide_id !== sid) return;
    const before = Object.fromEntries(Object.keys(fields).map((key) => [key, target[key as keyof AnnotationFields]])) as AnnotationFields;
    const replace = (updated: GeometryAnnotation) => setSlideAnnotations((prev) => prev.map((a) => (a.id === id ? updated : a)));
    const send = async (f: AnnotationFields) => replace(await updateAnnotation(id, f));

    setSaveState("saving");
    replace({ ...target, ...fields });
    try {
      await send(fields);
      setSaveState("saved");
      history.push({ label, do: () => send(fields), undo: () => send(before) });
    } catch (e) {
      replace(target);
      setSaveState("error");
      pushToast(e instanceof Error ? e.message : "Failed to save the change", "error");
    }
  }

  const editAny = (id: number, fields: AnnotationFields, label: string) =>
    borrowedEntry(id) ? editBorrowed(id, fields, label) : slideLevel(id) ? editSlideLevel(id, fields, label) : editAnnotation(id, fields, label);

  const handleShapeEdit = (id: number, points: Point[]) => {
    const entry = borrowedEntry(id);
    if (entry && patch) {
      // Drawn in this patch's pixels; stored in the pixels of the patch it belongs to.
      editBorrowed(id, { coordinates_patch_local: points.map((pt) => localToLocal(patch, entry.owner, pt)) }, "edit shape");
    } else if (slideLevel(id) && patch) {
      editSlideLevel(id, { coordinates_level0: points.map((pt) => localToLevel0(patch, pt)) }, "edit shape");
    } else {
      editAnnotation(id, { coordinates_patch_local: points }, "edit shape");
    }
  };

  async function handleDeleteSelected() {
    if (!selectedAnnId) return;
    const onWholeSlide = slideLevel(selectedAnnId);
    if (onWholeSlide) return deleteSlideLevel(onWholeSlide);
    const entry = borrowedEntry(selectedAnnId); // an overlapping patch's annotation, deleted from that patch
    const target = entry?.annotation ?? annotations.find((a) => a.id === selectedAnnId);
    const patchId = target?.patch_id; // the patch view only ever holds patch-drawn annotations
    if (!target || patchId == null) return;
    const drop = (id: number) =>
      entry ? setBorrowed((prev) => prev.filter((o) => o.annotation.id !== id)) : setAnnotations((prev) => prev.filter((a) => a.id !== id));
    const add = (a: GeometryAnnotation) =>
      entry ? setBorrowed((prev) => [...prev, { owner: entry.owner, annotation: a }]) : setAnnotations((prev) => [...prev, a]);
    setSaveState("saving");
    try {
      await deleteAnnotation(target.id);
      drop(target.id);
      setSelectedAnnId(null);
      setSaveState("saved");
      history.push({
        label: "delete annotation",
        do: async () => {
          await deleteAnnotation(target.id);
          drop(target.id);
        },
        undo: async () => {
          const recreated = await createAnnotation(patchId, {
            type: target.type,
            class_id: target.class_id,
            coordinates_patch_local: target.coordinates_patch_local,
            created_by: target.created_by ?? annotatorName,
          });
          add(recreated);
        },
      });
      setGridRefresh((n) => n + 1);
    } catch (e) {
      setSaveState("error");
      pushToast(e instanceof Error ? e.message : "Failed to delete annotation", "error");
    }
  }

  async function deleteSlideLevel(target: GeometryAnnotation) {
    const copy = {
      type: target.type,
      class_id: target.class_id,
      coordinates_level0: target.coordinates_level0 as [number, number][],
      created_by: target.created_by ?? annotatorName,
      notes: target.notes ?? undefined,
      unsure: target.unsure,
      flagged: target.flagged,
    };
    let currentId = target.id; // undo recreates it with a new id; redo must delete that one
    setSaveState("saving");
    try {
      await deleteAnnotation(target.id);
      setSlideAnnotations((prev) => prev.filter((a) => a.id !== target.id));
      setSelectedAnnId(null);
      setSaveState("saved");
      history.push({
        label: "delete annotation",
        do: async () => {
          await deleteAnnotation(currentId);
          setSlideAnnotations((prev) => prev.filter((a) => a.id !== currentId));
        },
        undo: async () => {
          const recreated = await createSlideAnnotation(sid, copy);
          currentId = recreated.id;
          setSlideAnnotations((prev) => [...prev, recreated]);
        },
      });
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
    if (wsiActive) return; // the whole-slide view owns the keyboard: Space pans there, it must not jump patches
    function onKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

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
  }, [patch, classes, imagesLive, isImage, sid, wsiActive]);

  if (!slide || !config || isImage === null) {
    return <div className="p-space-xl text-center text-slate-400 bg-[#0a0f1d] h-[calc(100vh-3.5rem)]">Loading workspace...</div>;
  }

  if (wsiActive) {
    return (
      <WsiAnnotationView
        slide={slide}
        config={config}
        slideAnnotations={slideAnnotations}
        setSlideAnnotations={setSlideAnnotations}
        focus={wsiFocus}
        onModeChange={(next) => (next === "patch" ? setMode("patch") : undefined)}
        onOpenPatch={openPatchFromSlide}
      />
    );
  }

  if (!patch || !patchOrigin) {
    return noPatches ? (
      <div className="p-space-xl text-center text-slate-300 bg-[#0a0f1d] h-[calc(100vh-3.5rem)] flex flex-col items-center gap-space-md">
        <p>This slide has no patches yet, so there is nothing to annotate patch by patch.</p>
        <div className="flex gap-space-sm">
          <button className="px-space-md h-8 rounded bg-[#0284c7] text-white text-label-md" onClick={() => setMode("wsi")}>
            Annotate the whole slide instead
          </button>
          <Link className="px-space-md h-8 rounded bg-surface-container-high text-on-surface text-label-md inline-flex items-center" to={`/projects/${pid}/slides/${sid}/processing`}>
            Generate patches
          </Link>
        </div>
      </div>
    ) : (
      <div className="p-space-xl text-center text-slate-400 bg-[#0a0f1d] h-[calc(100vh-3.5rem)]">Loading workspace...</div>
    );
  }

  const imageUrl = dynamicPatchUrl(sid, patch.x, patch.y, patch.width, patch.height, patch.level, slide.image_version);
  const switching = patch.slide_id !== sid; // the next image is still loading
  const prog = isImage ? progress(imagesLive) : totals;
  const noun = isImage ? "Image" : "Patch";
  const saveLabel = { idle: "", saving: "Saving...", saved: "Saved", error: "Error saving" }[saveState];

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] bg-[#0a0f1d] text-white overflow-hidden">
      {/* Precision strip */}
      <div className="bg-[#0b1329] border-b border-[#1e293b] px-space-md py-1.5 flex items-center gap-space-md text-body-sm flex-wrap">
        <span className="font-headline-sm">{switching ? "Loading..." : slide.filename}</span>
        {!isImage && <AnnotationModeSwitch mode="patch" onChange={(next) => (next === "wsi" ? showPatchOnSlide() : undefined)} />}
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
                <WsiViewer slideId={sid} version={slide.image_version} className="w-full h-full" showNavigator={false} showControls={false} onViewportChange={setBbox}>
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
            <IconButton icon="zoom_in" onClick={() => zoomAt(1.25)} title="Zoom in (mouse wheel)" />
            <IconButton icon="zoom_out" onClick={() => zoomAt(1 / 1.25)} title="Zoom out (mouse wheel)" />
            <button onClick={() => setZoom(1)} className="text-label-md text-slate-300 px-space-sm" title="Show the whole patch">
              Fit
            </button>
            <span className="font-mono text-label-sm text-slate-400 w-12 text-right" title="Screen pixels per patch pixel">
              {Math.round(effectiveZoom * 100)}%
            </span>
          </div>

          <div ref={setAreaEl} className="relative flex-1 min-h-0 overflow-auto flex p-space-lg" style={{ scrollbarGutter: "stable both-edges" }}>
            <div ref={canvasWrapRef} className="m-auto shrink-0">
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
              borrowed={[...onSlide, ...borrowedShapes]}
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

          {borrowed.length > 0 && (
            <div data-testid="from-overlapping-patches">
              <div className="text-label-md text-on-surface-variant mb-1">From overlapping patches ({borrowed.length})</div>
              <div className="flex flex-col gap-1">
                {borrowed.map(({ annotation: a, owner }) => {
                  const cls = classes.find((c) => c.id === a.class_id);
                  return (
                    <div
                      key={a.id}
                      onClick={() => {
                        setTool("select");
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
                      <span className="text-label-sm text-on-surface-variant">patch #{owner.patch_index}</span>
                    </div>
                  );
                })}
              </div>
              <p className="text-label-sm text-on-surface-variant mt-1">
                Drawn in a neighbouring patch that shares this area. Edit them here; they stay in the patch they were drawn in.
              </p>
            </div>
          )}

          {onSlide.length > 0 && (
            <div data-testid="from-the-slide">
              <div className="text-label-md text-on-surface-variant mb-1">From the whole slide ({onSlide.length})</div>
              <div className="flex flex-col gap-1">
                {onSlide.map((a) => {
                  const cls = classes.find((c) => c.id === a.class_id);
                  return (
                    <button
                      key={a.id}
                      onClick={() => {
                        setTool("select");
                        setSelectedAnnId(a.id);
                      }}
                      className={`flex items-center justify-between px-space-sm py-1.5 rounded bg-surface-container-low text-left hover:bg-surface-container ${
                        selectedAnnId === a.id ? "ring-1 ring-primary" : ""
                      }`}
                    >
                      <span className="flex items-center gap-1.5 text-label-md">
                        <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: cls?.color_hex ?? "#94a3b8" }} />
                        {cls?.name ?? "Unclassed"} #{a.id}
                      </span>
                      <span className="text-label-sm text-on-surface-variant capitalize">{a.type.replace("_", " ")}</span>
                    </button>
                  );
                })}
              </div>
              <p className="text-label-sm text-on-surface-variant mt-1">Drawn on the whole slide; shown here as they lie in this patch. Edit them here or on the whole slide.</p>
            </div>
          )}

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
                  onChange={(e) => editAny(selectedAnn.id, { class_id: e.target.value === "" ? null : Number(e.target.value) }, "change class")}
                >
                  <option value="">Unclassified</option>
                  {classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
              <Checkbox label="Unsure about this object" checked={selectedAnn.unsure} onChange={(v) => editAny(selectedAnn.id, { unsure: v }, "mark unsure")} />
              <Checkbox label="Flag this object" checked={selectedAnn.flagged} onChange={(v) => editAny(selectedAnn.id, { flagged: v }, "flag object")} />
              <input
                key={selectedAnn.id}
                className="input"
                placeholder="Note on this object..."
                defaultValue={selectedAnn.notes ?? ""}
                onBlur={(e) => e.target.value !== (selectedAnn.notes ?? "") && editAny(selectedAnn.id, { notes: e.target.value || null }, "edit note")}
              />
              {selectedIsSlideLevel && (
                <div className="flex items-center justify-between gap-space-sm text-label-sm text-on-surface-variant">
                  <span>Drawn on the whole slide; changes are saved there.</span>
                  <button className="text-primary hover:underline shrink-0" onClick={() => showAnnotationOnSlide(selectedAnn.id)}>
                    Show on whole slide
                  </button>
                </div>
              )}
              {selectedOwner && (
                <p className="text-label-sm text-on-surface-variant">
                  Belongs to patch #{selectedOwner.patch_index}, which overlaps this one; changes are saved there. It can be moved
                  anywhere within that patch.
                </p>
              )}
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
