import { useEffect, useRef, useState } from "react";
import { getTissueRegions, setTissueRegions } from "../../services/api";
import { useUiStore } from "../../stores/uiStore";
import type { TissueRegion, TissueRegions, TissueSource } from "../../types/api";

type Draft = Omit<TissueRegion, "id">;
interface Snapshot {
  source: TissueSource;
  regions: Draft[];
}

const strip = (regions: TissueRegion[]): Draft[] => regions.map(({ mode, type, coordinates }) => ({ mode, type, coordinates }));

/**
 * A slide's hand-drawn tissue regions and where its mask starts. Every change sends the whole list (the
 * server rebuilds the mask from it), shows at once, and can be undone. `onSaved` runs after each save so
 * the page can reload the slide and the mask image.
 */
export function useTissueRegions(slideId: number, onSaved: () => void) {
  const pushToast = useUiStore((s) => s.pushToast);
  const [data, setData] = useState<TissueRegions | null>(null);
  const [saving, setSaving] = useState(false);
  const [past, setPast] = useState<Snapshot[]>([]);
  const [future, setFuture] = useState<Snapshot[]>([]);
  const queue = useRef<Promise<unknown>>(Promise.resolve()); // saves run one after another, in order
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;

  useEffect(() => {
    let cancelled = false;
    setPast([]);
    setFuture([]);
    getTissueRegions(slideId)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && pushToast("Failed to load tissue regions", "error"));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideId]);

  const current = (): Snapshot | null => (data ? { source: data.source, regions: strip(data.regions) } : null);

  function send(next: Snapshot, before: Snapshot) {
    // Show it straight away; ids are positions in the list, as the server numbers them.
    setData((d) => d && { ...d, source: next.source, regions: next.regions.map((r, i) => ({ ...r, id: i + 1 })) });
    setSaving(true);
    queue.current = queue.current.then(async () => {
      try {
        setData(await setTissueRegions(slideId, next.source, next.regions));
        onSavedRef.current();
      } catch (e) {
        setData((d) => d && { ...d, source: before.source, regions: before.regions.map((r, i) => ({ ...r, id: i + 1 })) });
        pushToast(e instanceof Error ? e.message : "Failed to save tissue regions", "error");
      } finally {
        setSaving(false);
      }
    });
  }

  function change(next: Snapshot) {
    const before = current();
    if (!before) return;
    setPast((p) => [...p, before]);
    setFuture([]);
    send(next, before);
  }

  const regions = data?.regions ?? [];
  const source = data?.source ?? "auto";

  return {
    data,
    saving,
    setSource: (s: TissueSource) => s !== source && change({ source: s, regions: strip(regions) }),
    add: (region: Draft) => change({ source, regions: [...strip(regions), region] }),
    update: (id: number, fields: Partial<Draft>) =>
      change({ source, regions: strip(regions).map((r, i) => (i + 1 === id ? { ...r, ...fields } : r)) }),
    remove: (id: number) => change({ source, regions: strip(regions).filter((_, i) => i + 1 !== id) }),
    clear: () => regions.length > 0 && change({ source, regions: [] }),
    canUndo: past.length > 0,
    canRedo: future.length > 0,
    undo: () => {
      const before = current();
      const target = past[past.length - 1];
      if (!before || !target) return;
      setPast((p) => p.slice(0, -1));
      setFuture((f) => [...f, before]);
      send(target, before);
    },
    redo: () => {
      const before = current();
      const target = future[future.length - 1];
      if (!before || !target) return;
      setFuture((f) => f.slice(0, -1));
      setPast((p) => [...p, before]);
      send(target, before);
    },
    /** Detection switches the server to "auto" on its own; pick that up without a history entry. */
    reload: () => getTissueRegions(slideId).then(setData).catch(() => {}),
  };
}
